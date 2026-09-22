"""D-1b: a bot built from a brief is built now and says what it waits on.

The apps the brief names that the workspace has connected become the bot's
tools; the ones it has not connected are the "waiting on" list, each with
a connect card on the thread, so the build finishes before the connection
does and nobody is sent to a screen.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import bot_from_brief


def _connector(slug):
    return SimpleNamespace(slug=slug)


def _tool(toolkit):
    return SimpleNamespace(
        id=1,
        tool_uuid=f"t-{toolkit}",
        name=toolkit,
        description="",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": "X", "toolkit": toolkit},
        },
    )


@pytest.mark.asyncio
class TestWaitingOn:
    async def test_named_but_unconnected_apps_get_a_card_and_a_line(self):
        with (
            patch(
                "api.services.integrations.composio.catalogue.connectors",
                AsyncMock(
                    return_value=[
                        _connector("shopify"),
                        _connector("gmail"),
                        _connector("notion"),
                    ]
                ),
            ),
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[_tool("gmail")]),
            ),
            patch("api.services.workflow.connector_offer.offer", AsyncMock()) as offer,
        ):
            waiting = await bot_from_brief._waiting_on(
                organization_id=7,
                spec="Every hour check Shopify stock and email me from Gmail when low.",
            )
        assert waiting == ["shopify"]
        offer.assert_awaited_once()
        assert offer.await_args.kwargs["arguments"]["app"] == "shopify"

    async def test_whole_words_only(self):
        with (
            patch(
                "api.services.integrations.composio.catalogue.connectors",
                AsyncMock(return_value=[_connector("gmail")]),
            ),
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch("api.services.workflow.connector_offer.offer", AsyncMock()) as offer,
        ):
            waiting = await bot_from_brief._waiting_on(
                organization_id=7, spec="mail notgmailish@example.com"
            )
        assert waiting == []
        offer.assert_not_awaited()

    async def test_at_most_three_cards(self):
        slugs = ["a1", "b2", "c3", "d4", "e5"]
        with (
            patch(
                "api.services.integrations.composio.catalogue.connectors",
                AsyncMock(return_value=[_connector(s) for s in slugs]),
            ),
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch("api.services.workflow.connector_offer.offer", AsyncMock()) as offer,
        ):
            waiting = await bot_from_brief._waiting_on(
                organization_id=7, spec="use a1 b2 c3 d4 e5"
            )
        assert waiting == slugs
        assert offer.await_count == bot_from_brief.MAX_WAITING_CARDS

    async def test_a_catalogue_that_cannot_be_read_waits_on_nothing(self):
        with patch(
            "api.services.integrations.composio.catalogue.connectors",
            AsyncMock(side_effect=RuntimeError("down")),
        ):
            assert (
                await bot_from_brief._waiting_on(organization_id=7, spec="shopify")
                == []
            )


def _bot(id_, name, *, apps, brief="Check Shopify stock hourly and email me."):
    return SimpleNamespace(
        id=id_,
        name=name,
        workflow_definition={
            "nodes": [
                {"id": "start-1", "type": "startCall", "data": {"name": "Start"}},
                {"id": "agent-1", "type": "agentNode", "data": {"name": "Watch"}},
            ],
            "edges": [],
        },
        workflow_configurations={
            "channel": "chat",
            bot_from_brief.WAITING_KEY: {"apps": apps, "brief": brief},
        },
    )


def _shopify_tool(slug, uuid):
    return SimpleNamespace(
        id=1,
        tool_uuid=uuid,
        name=slug.replace("_", " ").title(),
        description="",
        category="composio",
        definition={
            "type": "composio",
            "config": {"tool_slug": slug, "toolkit": "shopify", "parameters": []},
        },
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query):
        rows = self._rows
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.asyncio
class TestWhenTheAppConnects:
    async def test_the_waiting_bot_gets_the_tools_and_the_thread_is_told(self):
        rows = [
            _bot(1, "Inventory Watch", apps=["shopify"]),
            _bot(2, "Two Apps", apps=["shopify", "notion"]),
            _bot(3, "Not Waiting", apps=[]),
        ]
        updates: list[dict] = []

        async def _update(workflow_id, **kwargs):
            updates.append({"id": workflow_id, **kwargs})

        tools = [
            _shopify_tool("SHOPIFY_GET_PRODUCTS", "u-get"),
            _shopify_tool("SHOPIFY_GET_INVENTORY_LEVELS", "u-inv"),
        ]
        with (
            patch.object(
                bot_from_brief.db_client, "async_session", lambda: _Rows(rows)
            ),
            patch.object(bot_from_brief.db_client, "update_workflow", _update),
            patch.object(
                bot_from_brief.connected_tools,
                "list_for_organization",
                AsyncMock(return_value=tools),
            ),
            patch.object(
                bot_from_brief.connected_tools,
                "mcp_for_organization",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.agent_timeline.record_activity", AsyncMock()
            ) as told,
        ):
            done = await bot_from_brief.attach_waiting(organization_id=7, app="shopify")

        assert done == ["Inventory Watch", "Two Apps"]
        assert [u["id"] for u in updates] == [1, 2]
        first = updates[0]
        callers = [
            n
            for n in first["workflow_definition"]["nodes"]
            if n["type"] in ("startCall", "agentNode")
        ]
        assert all({"u-get", "u-inv"} <= set(n["data"]["tool_uuids"]) for n in callers)
        # One bot is done waiting; the other still waits on notion.
        assert bot_from_brief.WAITING_KEY not in first["workflow_configurations"]
        second = updates[1]["workflow_configurations"][bot_from_brief.WAITING_KEY]
        assert second["apps"] == ["notion"]
        lines = [c.kwargs["summary"] for c in told.await_args_list]
        assert "Inventory Watch now has shopify" in lines
        assert "Two Apps now has shopify; still waiting on notion" in lines

    async def test_nobody_waiting_means_nothing_read(self):
        with (
            patch.object(
                bot_from_brief.db_client,
                "async_session",
                lambda: _Rows([_bot(3, "Not Waiting", apps=[])]),
            ),
            patch.object(
                bot_from_brief.connected_tools, "list_for_organization", AsyncMock()
            ) as listed,
        ):
            assert (
                await bot_from_brief.attach_waiting(organization_id=7, app="shopify")
                == []
            )
        listed.assert_not_awaited()

    def test_the_bot_row_carries_what_it_waits_on(self):
        from api.enums import BotChannel

        config = bot_from_brief._configurations(
            channel=BotChannel.CHAT, waiting_on=["shopify"], spec="Check Shopify."
        )
        assert config == {
            "channel": "chat",
            bot_from_brief.WAITING_KEY: {
                "apps": ["shopify"],
                "brief": "Check Shopify.",
            },
        }
        assert (
            bot_from_brief._configurations(
                channel=BotChannel.VOICE, waiting_on=[], spec="x"
            )
            is None
        )

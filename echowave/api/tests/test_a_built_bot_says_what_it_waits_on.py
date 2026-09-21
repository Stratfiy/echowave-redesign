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

"""Decibyl's drawer: the connected apps, reachable from the thread.

The tests that matter are the boundary ones. A read runs and is billed; an
unknown verb is a write, not a read; a tool that errors is not billed; and
nothing about a tool's name can shadow one of Decibyl's own.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import connected_tools


def _tool(
    slug: str, *, name: str = "Gmail: fetch emails", toolkit: str = "gmail", **extra
):
    return SimpleNamespace(
        id=1,
        tool_uuid="t-1",
        name=name,
        description="Fetch recent emails",
        category="composio",
        definition={
            "type": "composio",
            "config": {
                "tool_slug": slug,
                "toolkit": toolkit,
                "connected_account_id": "ca_1",
                "parameters": [
                    {
                        "name": "query",
                        "type": "string",
                        "description": "search",
                        "required": False,
                    }
                ],
                **extra,
            },
        },
    )


class TestReadOrWrite:
    def test_a_fetch_is_a_read(self):
        assert connected_tools.is_read(_tool("GMAIL_FETCH_EMAILS"))

    def test_a_send_is_a_write(self):
        assert not connected_tools.is_read(_tool("GMAIL_SEND_EMAIL"))

    def test_search_find_list_get_are_reads(self):
        for slug in (
            "HUBSPOT_SEARCH_CONTACTS",
            "GOOGLECALENDAR_FIND_EVENT",
            "GOOGLESHEETS_LIST_SHEETS",
            "ZOHO_GET_LEAD",
        ):
            assert connected_tools.is_read(_tool(slug)), slug

    def test_create_update_delete_are_writes(self):
        for slug in (
            "HUBSPOT_CREATE_CONTACT",
            "GOOGLECALENDAR_UPDATE_EVENT",
            "ZOHO_DELETE_LEAD",
            "WHATSAPP_SEND_MESSAGE",
        ):
            assert not connected_tools.is_read(_tool(slug)), slug

    def test_an_unknown_verb_is_a_write(self):
        # The safe direction: a needless confirm is noticed; an email that
        # went out is not.
        assert not connected_tools.is_read(_tool("ACME_FROBNICATE_THING"))
        assert not connected_tools.is_read(_tool(""))


class TestWhatIsConnected:
    def test_only_composio_tools_with_a_slug_count(self):
        assert connected_tools.is_connected(_tool("GMAIL_FETCH_EMAILS"))
        http = SimpleNamespace(
            category="http_api",
            name="x",
            definition={"config": {"url": "https://api.acme.com"}},
        )
        assert not connected_tools.is_connected(http)
        no_slug = _tool("GMAIL_FETCH_EMAILS")
        no_slug.definition["config"].pop("tool_slug")
        assert not connected_tools.is_connected(no_slug)

    @pytest.mark.asyncio
    async def test_listing_reads_active_rows_and_never_raises(self):
        rows = [
            _tool("GMAIL_FETCH_EMAILS"),
            SimpleNamespace(category="http_api", name="h", definition={}),
        ]
        with patch.object(
            connected_tools.db_client,
            "get_tools_for_organization",
            AsyncMock(return_value=rows),
        ) as listing:
            got = await connected_tools.list_for_organization(7)
        assert [t.name for t in got] == ["Gmail: fetch emails"]
        assert listing.await_args.kwargs["status"] == "active"

        with patch.object(
            connected_tools.db_client,
            "get_tools_for_organization",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            assert await connected_tools.list_for_organization(7) == []


class TestTheSchema:
    def test_the_name_is_prefixed_so_it_cannot_shadow_decibyls_own(self):
        schema = connected_tools.schema_for(_tool("GMAIL_FETCH_EMAILS"))
        assert schema["name"].startswith(connected_tools.PREFIX)
        assert schema["name"] != "propose_action"
        assert "query" in schema["parameters"]["properties"]

    def test_the_description_says_whether_it_reads_or_cards(self):
        assert (
            "reads"
            in connected_tools.schema_for(_tool("GMAIL_FETCH_EMAILS"))["description"]
        )
        assert (
            "card"
            in connected_tools.schema_for(_tool("GMAIL_SEND_EMAIL"))["description"]
        )

    def test_the_context_block_names_the_apps(self):
        block = connected_tools.apps_block(
            [
                _tool("GMAIL_FETCH_EMAILS"),
                _tool("HUBSPOT_CREATE_CONTACT", toolkit="hubspot"),
            ]
        )
        assert "gmail" in block and "hubspot" in block
        assert "1 read-only" in block
        assert "No apps connected" in connected_tools.apps_block([])


@pytest.mark.asyncio
class TestRunningOne:
    async def test_a_success_is_returned_and_billed_once(self):
        tool = _tool("GMAIL_FETCH_EMAILS")
        with (
            patch.object(
                connected_tools,
                "execute_composio_tool",
                AsyncMock(return_value={"status": "success", "data": {"n": 2}}),
            ) as run,
            patch(
                "api.services.billing.events.charge_in_own_session", new=AsyncMock()
            ) as charge,
        ):
            result = await connected_tools.execute(
                organization_id=7,
                tool=tool,
                arguments={"query": "x"},
                ref_id="decibyl:7:c1",
            )
        assert result["status"] == "success"
        kwargs = run.await_args.kwargs
        assert kwargs["tool_slug"] == "GMAIL_FETCH_EMAILS"
        assert kwargs["organization_id"] == 7
        assert kwargs["connected_account_id"] == "ca_1"
        charge.assert_awaited_once()
        assert charge.await_args.kwargs["ref_id"] == "decibyl:7:c1"
        assert charge.await_args.kwargs["organization_id"] == 7

    async def test_an_error_is_returned_and_not_billed(self):
        with (
            patch.object(
                connected_tools,
                "execute_composio_tool",
                AsyncMock(return_value={"status": "error", "error": "nope"}),
            ),
            patch(
                "api.services.billing.events.charge_in_own_session", new=AsyncMock()
            ) as charge,
        ):
            result = await connected_tools.execute(
                organization_id=7,
                tool=_tool("GMAIL_FETCH_EMAILS"),
                arguments={},
                ref_id="r",
            )
        assert result["status"] == "error"
        charge.assert_not_awaited()

    async def test_a_crash_becomes_an_error_envelope(self):
        with patch.object(
            connected_tools,
            "execute_composio_tool",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await connected_tools.execute(
                organization_id=7,
                tool=_tool("GMAIL_FETCH_EMAILS"),
                arguments={},
                ref_id="r",
            )
        assert result["status"] == "error" and "boom" in result["error"]

    async def test_a_big_result_is_bounded(self):
        with (
            patch.object(
                connected_tools,
                "execute_composio_tool",
                AsyncMock(return_value={"status": "success", "data": ["x" * 10_000]}),
            ),
            patch("api.services.billing.events.charge_in_own_session", new=AsyncMock()),
        ):
            result = await connected_tools.execute(
                organization_id=7,
                tool=_tool("GMAIL_FETCH_EMAILS"),
                arguments={},
                ref_id="r",
            )
        assert result["truncated"] is True
        assert len(result["data"]) <= connected_tools.MAX_RESULT_CHARS + 32

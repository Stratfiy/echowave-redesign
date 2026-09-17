"""A newly connected MCP server exposes its reads and nothing else.

Zerodha's server was connected with an empty filter -- the schema's word for
"expose everything" -- and exposed place_order, modify_order and cancel_order
to a model told to be helpful. The filter was set by hand afterwards, on one
account. Every other server anybody connects starts where that one did.

Guarded here: the classifier on the real Kite names; that the create path
narrows an empty filter and never touches a set one; that the update path is
not involved at all; and the one outcome that must never happen -- a server
with no reads ending up with an empty filter, which would expose everything
in the name of exposing nothing.
"""

from __future__ import annotations

import pytest

from api.services import tool_management
from api.services.workflow import mcp_read_only

#: The 22 tools Kite's hosted server actually discovered, on the night.
KITE = [
    "login",
    "get_profile",
    "get_margins",
    "get_holdings",
    "get_positions",
    "get_mf_holdings",
    "get_quotes",
    "get_ltp",
    "get_ohlc",
    "get_historical_data",
    "search_instruments",
    "get_orders",
    "get_order_history",
    "get_order_trades",
    "get_trades",
    "get_gtts",
    "place_order",
    "modify_order",
    "cancel_order",
    "place_gtt_order",
    "modify_gtt_order",
    "delete_gtt_order",
]


class TestTheClassifier:
    @pytest.mark.parametrize(
        "name",
        [
            "get_quotes",
            "get_holdings",
            "get_positions",
            "get_ltp",
            "get_ohlc",
            "get_historical_data",
            "search_instruments",
            "get_orders",
            "get_order_history",
            "get_trades",
            "get_profile",
            "get_margins",
            "list_accounts",
            "fetch_balance",
            "read_file",
        ],
    )
    def test_a_read_is_a_read(self, name):
        assert mcp_read_only.is_read(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "place_order",
            "modify_order",
            "cancel_order",
            "place_gtt_order",
            "delete_gtt_order",
            "login",
            "send_email",
            "create_ticket",
            "update_contact",
            "transfer_funds",
            "book_order",
        ],
    )
    def test_a_write_is_a_write(self, name):
        assert mcp_read_only.is_read(name) is False

    def test_a_description_that_says_place_settles_it(self):
        """The name is the model's word; the description is the server's."""
        assert mcp_read_only.is_read("get_it_done", "Places a market order") is False

    def test_unknown_means_write(self):
        """order_book is a read and is excluded, at the cost of one edit. The
        other way round costs somebody an order."""
        assert mcp_read_only.is_read("order_book") is False
        assert mcp_read_only.is_read("") is False

    def test_kite_keeps_its_reads_and_loses_its_writes(self):
        chosen = mcp_read_only.default_filter([{"name": n} for n in KITE])
        assert "get_quotes" in chosen and "get_holdings" in chosen
        for write in ("place_order", "modify_order", "cancel_order", "login"):
            assert write not in chosen
        # Including the six that were live on the account for a day.
        assert not {n for n in chosen if "gtt" in n and n != "get_gtts"}


class TestTheDefaultIsNeverEmpty:
    def test_a_server_of_writes_gets_a_sentinel_not_an_empty_list(self):
        """Empty means everything. The one outcome this must not produce."""
        chosen = mcp_read_only.default_filter(
            [{"name": "place_order"}, {"name": "cancel_order"}]
        )
        assert chosen == [mcp_read_only.NOTHING_ENABLED]
        assert chosen  # and therefore exposes nothing

    def test_the_sentinel_cannot_be_a_tool_name(self):
        assert " " in mcp_read_only.NOTHING_ENABLED

    def test_nothing_discovered_stays_empty(self):
        """A dead server at connect time has no catalogue to narrow. Leaving
        the filter empty there is the existing behaviour, and the refresh
        button fills the catalogue later without touching the filter."""
        assert mcp_read_only.default_filter([]) == []


class TestTheCreatePath:
    def _definition(self, **config):
        base = {
            "url": "https://mcp.example.com/",
            "discovered_tools": [{"name": n} for n in KITE],
        }
        base.update(config)
        return {"type": "mcp", "config": base}

    def test_an_empty_filter_is_narrowed_to_reads(self):
        out = tool_management.start_read_only(self._definition(tools_filter=[]))
        assert "get_quotes" in out["config"]["tools_filter"]
        assert "place_order" not in out["config"]["tools_filter"]

    def test_a_filter_somebody_set_is_never_touched(self):
        """The whole point of it being a default."""
        mine = ["place_order", "get_quotes"]
        out = tool_management.start_read_only(self._definition(tools_filter=list(mine)))
        assert out["config"]["tools_filter"] == mine

    def test_a_non_mcp_definition_passes_through(self):
        definition = {"type": "http", "config": {"url": "https://x/"}}
        assert tool_management.start_read_only(definition) is definition

    def test_the_update_path_does_not_narrow(self):
        """Discovery runs on update too (routes/tool.py). Narrowing there
        would rewrite a filter every time somebody pressed Save."""
        import inspect

        source = inspect.getsource(tool_management.create_tool_for_user)
        assert "start_read_only" in source
        # And nowhere else in the module's public update helpers.
        for name, fn in inspect.getmembers(tool_management, inspect.isfunction):
            if "update" in name.lower():
                assert "start_read_only" not in inspect.getsource(fn), name


class TestNounsAreNotWrites:
    """The first draft listed "message", "email", "call", "book" and "alert"
    as write words, which would have excluded get_messages and list_calls --
    the most ordinary reads there are -- from every new server. It is the
    verb that makes a write; a noun only says what the tool is about."""

    @pytest.mark.parametrize(
        "name",
        [
            "get_messages",
            "list_calls",
            "get_posts",
            "list_bookings",
            "get_alerts",
            "get_settings",
            "get_address",
            "search_payments",
        ],
    )
    def test_a_read_about_a_thing_is_still_a_read(self, name):
        assert mcp_read_only.is_read(name) is True

    @pytest.mark.parametrize(
        "name",
        ["send_message", "book_appointment", "post_update", "set_alert", "add_contact"],
    )
    def test_the_verb_still_decides(self, name):
        assert mcp_read_only.is_read(name) is False

    @pytest.mark.parametrize(
        "name", ["places_order", "cancelling_order", "transferred_funds"]
    )
    def test_an_inflected_verb_is_still_that_verb(self, name):
        assert mcp_read_only.is_read(name) is False

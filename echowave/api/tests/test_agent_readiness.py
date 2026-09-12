"""What an agent still needs, and why it is one list rather than two screens.

"Google Sheets was never connected" and "Google Sheets stopped working on
Tuesday" mean the same thing to the business -- the booking is not being
written -- and different things to whoever fixes it. A setup wizard answers
only the first, and answers it once: a token revoked three months later leaves
a green tick over an agent that has silently stopped filing anything.
"""

from types import SimpleNamespace

import pytest

from api.services.workflow.readiness import (
    FAILURES_BEFORE_CONCERN,
    STATUS_FAILING,
    STATUS_MISSING,
    STATUS_READY,
    build_checklist,
    connector_for,
    required_tool_uuids,
)


def _tool(name="Tool", category="composio", toolkit=None, url=None):
    config = {}
    if toolkit:
        config["toolkit"] = toolkit
    if url:
        config["url"] = url
    return SimpleNamespace(
        name=name, category=category, definition={"type": category, "config": config}
    )


class TestWhatAnAgentNeeds:
    def test_tools_are_collected_from_every_node(self):
        definition = {
            "nodes": [
                {"data": {"tool_uuids": ["t1", "t2"]}},
                {"data": {"tool_uuids": ["t3"]}},
            ]
        }
        assert required_tool_uuids(definition, None) == ["t1", "t2", "t3"]

    def test_a_tool_on_two_nodes_is_one_requirement(self):
        definition = {
            "nodes": [
                {"data": {"tool_uuids": ["t1"]}},
                {"data": {"tool_uuids": ["t1"]}},
            ]
        }
        assert required_tool_uuids(definition, None) == ["t1"]

    def test_after_call_steps_count_too(self):
        """An agent whose after-call step cannot run answers the phone and files
        nothing, which is the failure the product exists to prevent. Looking
        only at the conversation would miss it entirely."""
        assert required_tool_uuids(
            None, {"outcome_actions": [{"tool_uuid": "t9"}]}
        ) == ["t9"]

    def test_a_disabled_step_is_not_a_requirement(self):
        actions = {"outcome_actions": [{"tool_uuid": "t9", "enabled": False}]}
        assert required_tool_uuids(None, actions) == []

    @pytest.mark.parametrize(
        "definition", [None, {}, {"nodes": None}, {"nodes": "x"}, {"nodes": [None, 1]}]
    )
    def test_a_malformed_graph_cannot_crash_the_screen(self, definition):
        assert required_tool_uuids(definition, None) == []


class TestWhatCountsAsAConnector:
    def test_a_composio_tool_names_its_app(self):
        assert connector_for(_tool(toolkit="GOOGLESHEETS"))[0] == "googlesheets"

    def test_google_calendar_is_named_even_though_it_has_no_toolkit(self):
        assert connector_for(_tool(category="google_calendar"))[0] == "googlecalendar"

    def test_the_customers_own_endpoint_is_listed_by_host(self):
        app, label = connector_for(
            _tool(category="http_api", url="https://API.acme.com/x")
        )
        assert label == "api.acme.com"

    @pytest.mark.parametrize(
        "category", ["calculator", "rate_table", "end_call", "transfer_call"]
    )
    def test_a_tool_that_touches_nothing_is_not_on_the_checklist(self, category):
        """Listing them as ready would pad the list with rows that can never be
        anything else, and a checklist of things that cannot fail teaches an
        operator to stop reading it."""
        assert connector_for(_tool(category=category)) is None


class TestTheChecklist:
    def test_an_unconnected_app_is_missing(self):
        rows = build_checklist(
            tools=[_tool(toolkit="GMAIL")], connected_apps=set(), failures_by_app={}
        )
        assert rows[0]["status"] == STATUS_MISSING

    def test_a_connected_app_that_keeps_failing_is_not_ready(self):
        """The state a wizard cannot express and the one that costs a customer
        a month of unfiled bookings."""
        rows = build_checklist(
            tools=[_tool(toolkit="GMAIL")],
            connected_apps={"GMAIL"},
            failures_by_app={"gmail": FAILURES_BEFORE_CONCERN},
        )
        assert rows[0]["status"] == STATUS_FAILING

    def test_one_failure_is_not_a_broken_connection(self):
        """A bad request or somebody's expired card. A run of them is the
        connection."""
        rows = build_checklist(
            tools=[_tool(toolkit="GMAIL")],
            connected_apps={"GMAIL"},
            failures_by_app={"gmail": 1},
        )
        assert rows[0]["status"] == STATUS_READY

    def test_four_gmail_tools_are_one_gmail_problem(self):
        """Four rows saying the same thing is a worse screen and a worse
        instruction."""
        rows = build_checklist(
            tools=[
                _tool(name="Send", toolkit="GMAIL"),
                _tool(name="Read", toolkit="GMAIL"),
                _tool(name="Reply", toolkit="GMAIL"),
            ],
            connected_apps=set(),
            failures_by_app={},
        )
        assert len(rows) == 1
        assert rows[0]["needed_by"] == ["Send", "Read", "Reply"]

    def test_the_thing_to_fix_is_first(self):
        """An operator opening this wants the problem, not an alphabetical
        inventory of what already works."""
        rows = build_checklist(
            tools=[
                _tool(name="A", toolkit="AIRTABLE"),
                _tool(name="B", toolkit="GMAIL"),
                _tool(name="C", toolkit="ZOHO"),
            ],
            connected_apps={"AIRTABLE", "GMAIL"},
            failures_by_app={"gmail": 9},
        )
        assert [r["status"] for r in rows] == [
            STATUS_MISSING,
            STATUS_FAILING,
            STATUS_READY,
        ]

    def test_the_customers_own_endpoint_is_shown_without_a_connect_button(self):
        """We cannot connect it for them. Omitting it would tell an operator
        the agent needs two things when it depends on three."""
        rows = build_checklist(
            tools=[_tool(category="http_api", url="https://api.acme.com/x")],
            connected_apps=set(),
            failures_by_app={},
        )
        assert rows[0]["connectable"] is False
        assert rows[0]["status"] == STATUS_READY

    def test_an_agent_needing_nothing_outside_has_an_empty_checklist(self):
        rows = build_checklist(
            tools=[_tool(category="calculator")],
            connected_apps=set(),
            failures_by_app={},
        )
        assert rows == []

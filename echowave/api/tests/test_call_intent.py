"""What callers wanted, read off the path their call took.

The rule is one sentence -- the first node after the greeting that is not
just routing -- and nearly every test here is a way that sentence goes wrong:
a call that never got past the greeting, a call that went straight through a
branch, a workflow whose router is named like a real step.
"""

from api.services.reports.call_intent import (
    NO_INTENT,
    intent_of,
    passthrough_names,
    summarise,
)


class TestReadingOneCall:
    def test_the_step_after_the_greeting_is_the_intent(self):
        assert (
            intent_of(
                ["Greet and understand", "Take the booking details", "Find a slot"]
            )
            == "Take the booking details"
        )

    def test_a_different_branch_is_a_different_intent(self):
        assert (
            intent_of(["Greet and understand", "Reschedule or cancel"])
            == "Reschedule or cancel"
        )

    def test_a_call_that_never_left_the_greeting_is_named(self):
        """Not dropped. A rising count here is the agent failing to understand
        people, and a silent filter is exactly what would hide that."""
        assert intent_of(["Greet and understand"]) == NO_INTENT

    def test_a_call_with_no_nodes_at_all(self):
        assert intent_of([]) == NO_INTENT
        assert intent_of(None) == NO_INTENT

    def test_routing_nodes_are_passed_through(self):
        assert (
            intent_of(["Greet", "Router", "Take the booking details"], {"Router"})
            == "Take the booking details"
        )

    def test_several_routers_in_a_row_are_all_passed_through(self):
        assert (
            intent_of(
                ["Greet", "Router", "Hold", "Clinical question"], {"Router", "Hold"}
            )
            == "Clinical question"
        )

    def test_a_call_that_only_reached_a_router_got_nowhere(self):
        assert intent_of(["Greet", "Router"], {"Router"}) == NO_INTENT

    def test_junk_entries_do_not_crash_it(self):
        assert intent_of(["Greet", None, "Clinical question"]) == "Clinical question"


class TestFindingTheRoutingNodes:
    def test_branch_and_wait_nodes_are_found_by_type(self):
        workflow = {
            "nodes": [
                {"type": "startCall", "data": {"name": "Greet"}},
                {"type": "branch", "data": {"name": "Router"}},
                {"type": "wait", "data": {"name": "Hold"}},
                {"type": "agentNode", "data": {"name": "Book"}},
            ]
        }
        assert passthrough_names(workflow) == {"Router", "Hold"}

    def test_a_conversational_node_named_like_a_router_is_not_one(self):
        """The type decides, not the name. An agent node called 'Router' is
        still somewhere the caller was taken."""
        workflow = {"nodes": [{"type": "agentNode", "data": {"name": "Router"}}]}
        assert passthrough_names(workflow) == set()

    def test_an_empty_or_missing_definition_is_survivable(self):
        assert passthrough_names(None) == set()
        assert passthrough_names({}) == set()
        assert passthrough_names({"nodes": []}) == set()


class TestCountingADay:
    def test_calls_are_grouped_and_ordered_commonest_first(self):
        rows = summarise(
            [
                (18, ["Greet", "Take the booking details"]),
                (18, ["Greet", "Take the booking details"]),
                (18, ["Greet", "Reschedule or cancel"]),
            ]
        )
        assert [(r["intent"], r["calls"]) for r in rows] == [
            ("Take the booking details", 2),
            ("Reschedule or cancel", 1),
        ]

    def test_shares_are_of_the_whole_day(self):
        rows = summarise(
            [(18, ["Greet", "A"]), (18, ["Greet", "A"]), (18, ["Greet", "B"])]
        )
        assert {r["intent"]: r["share"] for r in rows} == {"A": 0.6667, "B": 0.3333}

    def test_a_day_with_no_calls_is_empty_not_zero(self):
        assert summarise([]) == []

    def test_each_workflow_gets_its_own_routing_nodes(self):
        """Two agents can name a node the same thing and mean different
        things by it; the router lookup is per workflow for that reason."""
        rows = summarise(
            [(18, ["Greet", "Router", "Book"]), (19, ["Greet", "Router"])],
            {18: {"Router"}, 19: set()},
        )
        assert [(r["intent"], r["calls"]) for r in rows] == [("Book", 1), ("Router", 1)]

    def test_the_order_is_stable_when_counts_tie(self):
        """A dashboard that reshuffles on every refresh looks like it is
        telling you something when it is not."""
        first = summarise([(1, ["G", "B"]), (1, ["G", "A"])])
        second = summarise([(1, ["G", "A"]), (1, ["G", "B"])])
        assert (
            [r["intent"] for r in first] == [r["intent"] for r in second] == ["A", "B"]
        )

    def test_calls_that_got_nowhere_are_counted_too(self):
        rows = summarise([(18, ["Greet"]), (18, ["Greet", "Book"])])
        assert {r["intent"]: r["calls"] for r in rows} == {NO_INTENT: 1, "Book": 1}

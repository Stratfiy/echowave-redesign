"""An agent does not publish still saying ``{{clinic_name}}``.

Heard on a real call on 20 September 2026: "Namaste, . How may I help you
today?" All three Clinic front desk agents were live with four unanswered
variables each. Everything to prevent it existed -- the template calls these
"values the operator must supply before going live" -- except the check.

The negatives matter more than the positives here. The renderer has its own
syntax for things filled from the call, and a rule that refused those would
block every agent that reads a caller's number.
"""

from __future__ import annotations

from api.services.workflow import unfilled
from api.services.workflow.errors import ItemKind


def node(node_id: str, **data):
    return {"id": node_id, "type": "startCall", "data": data}


def definition(*nodes):
    return {"nodes": list(nodes), "edges": []}


class TestWhatCounts:
    def test_a_bare_placeholder_is_the_operators_to_answer(self):
        assert unfilled.names_in("Namaste, {{clinic_name}}.") == {"clinic_name"}

    def test_the_runtime_set_is_not_the_operators(self):
        # Filled per call from the contact or campaign row. Asking an operator
        # for the caller's first name is asking for something they cannot know.
        assert (
            unfilled.names_in("Hello {{first_name}}, about {{order_summary}}") == set()
        )

    def test_a_context_path_is_the_renderers_own_syntax(self):
        # These are filled from the call, so they are never a gap. The pattern
        # does not match them at all, which is the point.
        for text in (
            "Calling {{initial_context.phone_number}}",
            "City is {{gathered_context.customer.address.city}}",
        ):
            assert unfilled.names_in(text) == set(), text

    def test_a_fallback_is_already_answered(self):
        assert unfilled.names_in("Hello {{name | fallback:there}}") == set()

    def test_prose_about_braces_is_not_a_placeholder(self):
        assert unfilled.names_in("Say { and } but never {{ alone") == set()

    def test_it_walks_into_dicts_and_lists(self):
        # Prompts, greetings and transition speech all hold text, and this
        # module should not have to know where in a node they live.
        data = {
            "prompt": "You work for {{business}}.",
            "phrases": ["one moment", "calling {{clinic_name}}"],
            "nested": {"deep": {"greeting": "Hi from {{town}}"}},
            "count": 3,
            "flag": True,
        }
        assert unfilled.names_in(data) == {"business", "clinic_name", "town"}

    def test_empty_things_are_empty(self):
        assert unfilled.names_in({}) == set()
        assert unfilled.names_in([]) == set()
        assert unfilled.names_in(None) == set()


class TestWhatItRefuses:
    def test_the_call_that_actually_happened(self):
        found = unfilled.problems(
            definition(
                node(
                    "start-1",
                    greeting="Namaste, {{clinic_name}}. How may I help you today?",
                    prompt="You are the front desk at {{clinic_name}}, {{clinic_address}}. "
                    "Doctors: {{doctor_names}}. Open {{opening_hours}}.",
                )
            )
        )
        assert len(found) == 1
        problem = found[0]
        assert problem["kind"] is ItemKind.node and problem["id"] == "start-1"
        # All four the real agent was missing, named so the operator knows
        # what to type rather than being told to go hunting.
        for name in ("clinic_address", "clinic_name", "doctor_names", "opening_hours"):
            assert name in problem["message"], name
        assert "caller would hear the gap" in problem["message"]

    def test_a_long_list_stops_naming_and_counts_the_rest(self):
        # A node with eleven unanswered variables has a message nobody
        # finishes reading, and the editor highlights the node anyway.
        many = " ".join("{{v%d}}" % i for i in range(unfilled.MAX_NAMED + 3))
        message = unfilled.problems(definition(node("n", prompt=many)))[0]["message"]
        assert "and 3 more" in message
        named = [f"v{i}" for i in range(unfilled.MAX_NAMED)]
        assert all(name in message for name in named)
        # and the ones past the limit are counted, not listed
        assert not any(f"v{i}" in message for i in range(unfilled.MAX_NAMED, 7))

    def test_one_error_per_node_not_per_placeholder(self):
        found = unfilled.problems(
            definition(
                node("a", prompt="{{one}} and {{two}}"),
                node("b", prompt="{{three}}"),
                node("c", prompt="nothing to fill here"),
            )
        )
        assert [p["id"] for p in found] == ["a", "b"]

    def test_a_finished_agent_publishes(self):
        found = unfilled.problems(
            definition(
                node(
                    "start-1",
                    greeting="Namaste, Narayani Dental. How may I help you?",
                    prompt="Open 9 to 6. Caller is {{first_name}} on "
                    "{{initial_context.phone_number}}.",
                )
            )
        )
        assert found == []

    def test_a_definition_that_is_not_one_is_not_an_error(self):
        # Never the place a malformed definition is first reported; the
        # schema check upstream owns that, and two messages about one
        # problem is worse than one.
        for junk in (None, {}, {"nodes": None}, {"nodes": "no"}, {"nodes": [None, 7]}):
            assert unfilled.problems(junk) == []


class TestHiringIsTheDoorTheBugCameThrough:
    """Publish was not the path that broke it.

    The three live Clinic front desks were never published: hiring a template
    calls ``create_workflow`` directly, and ``is_live`` defaults to True. So
    an unanswered template went straight to answering a phone without passing
    any validation at all. A half-finished agent is still made -- that is a
    normal place to be -- it just does not take calls until it is finished.
    """

    def test_an_unanswered_hire_is_made_but_not_live(self):
        import inspect

        from api.routes import agent_templates as route

        source = inspect.getsource(route.create_from_template)
        assert "gaps = unfilled.problems(definition)" in source
        assert "is_live=not gaps" in source

    def test_the_response_names_what_is_still_empty(self):
        import inspect

        from api.routes import agent_templates as route

        source = inspect.getsource(route.create_from_template)
        assert '"unanswered": sorted(unfilled.names_in(definition))' in source

    def test_the_db_client_can_be_told_not_to_go_live(self):
        import inspect

        from api.db.workflow_client import WorkflowClient

        signature = inspect.signature(WorkflowClient.create_workflow)
        assert signature.parameters["is_live"].default is True


class TestItIsWiredWhereItBites:
    def test_the_validator_runs_it(self):
        # Publish and the editor's own check both go through
        # `_validate_workflow_definition`; a save does not, because a
        # half-written prompt is a normal thing to be holding.
        import inspect

        from api.routes import workflow as route

        source = inspect.getsource(route._validate_workflow_definition)
        assert "unfilled.problems(workflow_definition)" in source

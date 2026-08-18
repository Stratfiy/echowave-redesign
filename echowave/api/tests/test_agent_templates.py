"""The starting agents, and the properties that make them safe to ship.

A template's whole point is that its prompts go into a live workflow unedited.
That makes structural mistakes expensive in a way an example's would not be: a
dangling edge produces an agent that cannot finish a call, and a missing
guardrail on the lending template produces a regulatory problem rather than a
tuning one.

These tests are all structural or content invariants, so they need no database
and no fixtures.
"""

import pytest

from api.services.agent_templates import (
    CallDirection,
    find_templates,
    get_template,
    list_templates,
)

ALL = list_templates()


def test_catalogue_is_not_empty():
    assert ALL


@pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
class TestEveryTemplate:
    """Invariants that hold for every template in the catalogue."""

    def test_has_exactly_one_start_node(self, template):
        """`startCall` has `max_instances=1` in the node spec.

        A template with two would fail graph validation at create time, after
        the user has already answered every question.
        """
        starts = [n for n in template.nodes if n.type == "startCall"]
        assert len(starts) == 1

    def test_start_node_has_a_greeting(self, template):
        """The first words are the template's job, not the model's."""
        start = template.start_node
        assert start is not None
        assert start.greeting

    def test_reaches_an_end_node(self, template):
        """Every template must be able to finish a call."""
        assert any(n.type == "endCall" for n in template.nodes)

    def test_edges_only_reference_declared_nodes(self, template):
        """A dangling edge is a workflow that fails to build."""
        names = {n.name for n in template.nodes}
        for edge in template.edges:
            assert edge.source in names, f"{edge.source} is not a node"
            assert edge.target in names, f"{edge.target} is not a node"

    def test_every_node_is_reachable_from_the_start(self, template):
        """An unreachable node is prompt work the caller will never hear."""
        start = template.start_node
        assert start is not None

        reached = {start.name}
        # Small graphs; iterate to a fixed point rather than recursing.
        for _ in range(len(template.nodes)):
            for edge in template.edges:
                if edge.source in reached:
                    reached.add(edge.target)

        unreachable = {n.name for n in template.nodes} - reached
        assert not unreachable, f"unreachable: {sorted(unreachable)}"

    def test_every_non_end_node_has_a_way_out(self, template):
        """A node with no outgoing edge strands the call there."""
        sources = {e.source for e in template.edges}
        for node in template.nodes:
            if node.type != "endCall":
                assert node.name in sources, f"{node.name} has no outgoing edge"

    def test_every_edge_carries_a_condition(self, template):
        """The condition is what the model routes on. An empty one routes on
        the label, which is not written to be evaluated."""
        for edge in template.edges:
            assert edge.condition.strip()
            assert edge.label.strip()

    def test_prompts_are_substantial(self, template):
        """Guards against a placeholder shipping as a production prompt."""
        for node in template.nodes:
            assert len(node.prompt) > 120, f"{node.name} prompt looks like a stub"

    def test_carries_the_shared_guardrails(self, template):
        """Every template must keep the house rules.

        They are copied into each template rather than referenced precisely so
        that editing one template cannot silently drop them — which means a
        test has to be what notices if someone does.
        """
        joined = " ".join(template.guardrails).lower()
        assert "one question per turn" in joined
        assert "read back" in joined
        assert "language" in joined

    def test_declares_compliance_notes(self, template):
        """Every vertical here touches personal data over a phone line."""
        assert template.compliance_notes

    def test_is_findable_by_its_own_example_requests(self, template):
        """The example phrasings exist to route intent. If a template's own
        examples do not reach it, they are decoration."""
        for request in template.example_requests:
            found = find_templates(request)
            assert template.id in {t.id for t in found}, (
                f"{template.id} not found by its own example {request!r}"
            )

    def test_template_variables_used_in_prompts_are_declared(self, template):
        """An undeclared `{{variable}}` is spoken to a caller verbatim.

        Runtime variables the pipeline fills (`first_name`, campaign fields)
        are excluded — only values the operator has to supply are the
        template's responsibility to declare.
        """
        import re

        runtime_supplied = {
            "first_name",
            "last_name",
            "order_summary",
            "order_amount",
            "amount_due",
            "due_date",
            "loan_ref",
        }
        text = " ".join(
            [n.prompt for n in template.nodes]
            + [n.greeting or "" for n in template.nodes]
        )
        used = set(re.findall(r"\{\{(\w+)\}\}", text))
        undeclared = used - set(template.template_variables) - runtime_supplied
        assert not undeclared, f"undeclared variables: {sorted(undeclared)}"

    def test_priced_stack_names_a_provider_for_every_component(self, template):
        """`estimate_agent_cost` prices what it is given. A blank provider is a
        quote with a hole in it, and the hole is usually the voice."""
        stack = template.stack
        assert stack.stt_provider
        assert stack.llm_provider
        assert stack.tts_provider
        assert stack.telephony_provider
        assert stack.rationale


# --- the constraints that are legal rather than stylistic -------------------


def test_lending_template_forbids_third_party_disclosure():
    """Disclosing a debt to anyone but the borrower is the serious failure in
    this vertical, and the template must forbid it in the running prompt."""
    template = get_template("lending_payment_reminder")
    assert template is not None
    joined = " ".join(template.guardrails).lower()
    assert "never disclose" in joined
    assert "borrower" in joined


def test_lending_template_forbids_threats():
    template = get_template("lending_payment_reminder")
    assert template is not None
    joined = " ".join(template.guardrails).lower()
    for forbidden in ("threaten", "legal action", "credit score", "recovery agent"):
        assert forbidden in joined, f"guardrails do not mention {forbidden}"


def test_lending_template_verifies_identity_before_saying_anything():
    """The first node must be the identity check, not the reminder."""
    template = get_template("lending_payment_reminder")
    assert template is not None
    start = template.start_node
    assert start is not None
    assert "confirm" in start.prompt.lower()
    # The amount must not appear before verification.
    assert "{{amount_due}}" not in start.prompt


def test_clinic_template_refuses_medical_advice():
    template = get_template("clinic_appointment")
    assert template is not None
    joined = " ".join(template.guardrails).lower()
    assert "never give medical advice" in joined
    assert "emergency" in joined


def test_edtech_template_forbids_placement_promises():
    template = get_template("edtech_admissions")
    assert template is not None
    joined = " ".join(template.guardrails).lower()
    assert "never promise a job" in joined


def test_cod_template_accepts_cancellation_without_argument():
    """A pressured confirmation becomes a refused delivery, which costs more
    than the cancellation would have."""
    template = get_template("ecom_cod_confirmation")
    assert template is not None
    joined = " ".join(template.guardrails).lower()
    assert "cancellation" in joined
    assert "without asking why" in joined


# --- lookup -----------------------------------------------------------------


def test_ids_are_unique():
    ids = [t.id for t in ALL]
    assert len(ids) == len(set(ids))


def test_get_template_returns_none_for_an_unknown_id():
    """Not an error: the caller may be working from a stale list, and re-listing
    is a better recovery than a failed session."""
    assert get_template("no_such_template") is None


def test_find_templates_returns_everything_for_an_empty_query():
    assert len(find_templates("")) == len(ALL)


def test_find_templates_ranks_the_better_match_first():
    found = find_templates("clinic appointment booking")
    assert found
    assert found[0].id == "clinic_appointment"


def test_find_templates_returns_nothing_for_an_unrelated_query():
    """An empty result is the signal to author normally rather than to bend a
    template onto the wrong vertical."""
    assert find_templates("quantum submarine logistics") == ()


def test_catalogue_covers_both_call_directions():
    """Inbound and outbound need different plans — numbers versus concurrency —
    so a catalogue that only covered one would only sell one shape of account."""
    directions = {t.direction for t in ALL}
    assert directions == {CallDirection.inbound, CallDirection.outbound}

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
from api.services.agent_templates.materialise import (
    TemplateShapeError,
    to_workflow_definition,
)

ALL = list_templates()

#: Split, because most of the invariants below are about a phone call and were
#: written when every template was a voice template. ``_base.py`` already
#: scoped its own validator this way -- "the rule that used to be enforced by
#: making the fields mandatory, now scoped to the templates it is actually
#: about" -- and these tests had not followed, so the first non-speaking
#: template failed nine of them on rules that do not apply to it.
#:
#: Scoped rather than relaxed. Each voice rule still holds for every calling
#: template, and the quiet ones get their own class below with the invariants
#: that *are* theirs.
CALLING = [t for t in ALL if t.speaks]
QUIET = [t for t in ALL if not t.speaks]


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

    def test_has_a_start_node_at_all(self, template):
        start = template.start_node
        assert start is not None

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
        # The one rule that is about every agent rather than about a call: do
        # not make things up. The conversational rules -- one question a turn,
        # read the number back, follow their language -- are asserted for
        # calling templates in TestEveryCallingTemplate, because a bot running
        # at eight in the morning against a spreadsheet has no turns, no
        # number to read back and nobody whose language to follow.
        assert "never invent" in joined

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

    def test_the_stack_names_a_language_model_and_says_why(self, template):
        """Every agent has a brain, whatever else it has."""
        assert template.stack.llm_provider
        assert template.stack.rationale


@pytest.mark.parametrize("template", CALLING, ids=lambda t: t.id)
class TestEveryCallingTemplate:
    """Invariants about being on a phone call.

    These were in TestEveryTemplate until a template arrived that answers no
    phone. They are unchanged -- only the set they run over is narrower.
    """

    def test_the_start_node_opens_with_words(self, template):
        """The first words are the template's job, not the model's."""
        assert template.start_node is not None
        assert template.start_node.greeting

    def test_carries_the_conversational_guardrails(self, template):
        """The house rules for talking to somebody.

        Copied into each template rather than referenced, precisely so that
        editing one cannot silently drop them -- which means a test has to be
        what notices.
        """
        joined = " ".join(template.guardrails).lower()
        assert "one question per turn" in joined
        assert "read back" in joined
        assert "language" in joined

    def test_priced_stack_names_a_provider_for_every_component(self, template):
        """`estimate_agent_cost` prices what it is given. A blank provider is a
        quote with a hole in it, and the hole is usually the voice."""
        stack = template.stack
        assert stack.stt_provider
        assert stack.llm_provider
        assert stack.tts_provider
        assert stack.telephony_provider

    def test_it_is_offered_voices_to_choose_from(self, template):
        """A voice agent sold without a voice to hear is sold on a promise."""
        assert template.suggested_voices


@pytest.mark.parametrize("template", QUIET, ids=lambda t: t.id)
class TestEveryQuietTemplate:
    """Invariants for an agent nobody hears.

    Not the voice rules relaxed -- a different set. What can go wrong with a
    bot that reads data and writes a summary is not a bad greeting, it is a
    confident figure nobody can trace.
    """

    def test_it_names_no_speech_or_telephony_provider(self, template):
        """A stack with a voice on a bot that never speaks is a rate we would
        quote, and a cost we would predict, for a component that never runs."""
        assert not template.stack.stt_provider
        assert not template.stack.tts_provider
        assert not template.stack.telephony_provider

    def test_it_is_offered_no_voices(self, template):
        """The gallery fell back to six default voices for any template
        without its own, which for a silent one is a choice offered, stored,
        and then ignored on every run."""
        assert template.suggested_voices == []

    def test_it_has_no_call_shape_and_a_schedule_shape_if_scheduled(self, template):
        assert template.call_shape is None
        if template.direction is CallDirection.scheduled:
            assert template.schedule_shape is not None
            assert template.schedule_shape.runs

    def test_it_is_told_not_to_invent_a_figure(self, template):
        """The failure mode of this family. A summary carrying a plausible
        number nobody can trace is worse than one that admits a gap, because
        somebody forwards it to their accountant."""
        joined = " ".join(template.guardrails).lower()
        assert "never invent" in joined

    def test_it_is_told_to_say_where_a_figure_came_from(self, template):
        joined = " ".join(template.guardrails).lower()
        assert "where it came from" in joined or "which invoice" in joined

    def test_it_defaults_to_reading_rather_than_writing(self, template):
        """An unsupervised run that spends money or messages a customer
        without being asked is the one failure here that cannot be undone."""
        joined = " ".join(template.guardrails).lower()
        assert "reading is the default" in joined


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
    assert {CallDirection.inbound, CallDirection.outbound} <= directions


def test_catalogue_has_something_that_does_not_speak():
    """Otherwise the non-voice plan has nothing to sell.

    Asserted rather than assumed: every template was a voice template for the
    whole life of this file, the pack format's non-calling branch was
    unreachable, and the cheapest tier we intend to charge for had no product
    behind it. A catalogue that drifts back to voice-only should fail here
    rather than on a pricing page.
    """
    assert QUIET


class TestMaterialising:
    """Turning a template into a workflow the editor and the engine can read.

    A template is only useful if it can become an agent, and the two bugs this
    class exists to catch were both invisible to every other test here: a node
    type the converter did not recognise, and a persona node no template
    carries. Neither would have failed a structural check on the catalogue —
    both would have failed on a customer's first call.
    """

    def test_every_template_becomes_a_workflow(self):
        for template in ALL:
            definition = to_workflow_definition(template)
            assert definition["nodes"], template.id
            assert "edges" in definition, template.id

    @pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
    def test_node_ids_are_unique(self, template):
        """Edges are matched by id. Two nodes sharing one silently reroutes a
        call."""
        nodes = to_workflow_definition(template)["nodes"]
        ids = [node["id"] for node in nodes]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
    def test_no_edge_dangles(self, template):
        """An edge into a node that does not exist is an agent that cannot
        finish the call it starts."""
        definition = to_workflow_definition(template)
        ids = {node["id"] for node in definition["nodes"]}
        for edge in definition["edges"]:
            assert edge["source"] in ids, (template.id, edge)
            assert edge["target"] in ids, (template.id, edge)

    @pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
    def test_exactly_one_start_node(self, template):
        nodes = to_workflow_definition(template)["nodes"]
        starts = [n for n in nodes if n["type"] == "startCall"]
        assert len(starts) == 1

    @pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
    def test_carries_a_persona(self, template):
        """No template in the catalogue ships a globalNode, and every
        hand-authored launch template opens with one. Without it the agent runs
        with no shared voice and no shared guardrail — which shows up on a call
        and nowhere else."""
        nodes = to_workflow_definition(template)["nodes"]
        personas = [n for n in nodes if n["type"] == "globalNode"]
        assert len(personas) == 1
        assert personas[0]["data"]["prompt"].strip()

    @pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
    def test_every_agent_node_keeps_its_prompt(self, template):
        """The prompts are the template. A conversion that dropped them would
        still produce a runnable agent, and a useless one."""
        definition = to_workflow_definition(template)
        prompts = {
            node["data"].get("prompt", "").strip()
            for node in definition["nodes"]
            if node["type"] == "agentNode"
        }
        for node in template.nodes:
            if node.type in ("agentNode", "agent"):
                assert node.prompt.strip() in prompts, (template.id, node.name)

    def test_an_unknown_node_type_is_refused(self):
        """Loudly, rather than by quietly dropping the node — a template that
        half-converts is worse than one that does not convert at all."""
        broken = ALL[0].model_copy(deep=True)
        broken.nodes[-1].type = "carrierPigeon"
        with pytest.raises(TemplateShapeError):
            to_workflow_definition(broken)

    def test_duplicate_node_names_are_refused(self):
        """Edges reference nodes by name, so a duplicate makes one of them
        unreachable."""
        broken = ALL[0].model_copy(deep=True)
        broken.nodes[-1].name = broken.nodes[-2].name
        with pytest.raises(TemplateShapeError):
            to_workflow_definition(broken)


class TestPersonalising:
    """The first-agent flow writes a name, the business facts and the opening
    line into a template before it becomes an agent."""

    def _clinic(self):
        template = get_template("clinic_appointment")
        assert template is not None
        return template

    def test_the_summary_lists_what_to_ask(self):
        from api.routes.agent_templates import _summary

        summary = _summary(self._clinic())
        names = [v["name"] for v in summary["variables"]]
        assert "clinic_name" in names
        # The question, not the key: the flow shows this to a person.
        asks = next(
            v["asks_for"] for v in summary["variables"] if v["name"] == "clinic_name"
        )
        assert asks != "clinic_name"
        assert summary["greeting"]

    def test_answers_reach_every_prompt(self):
        from api.routes.agent_templates import CreateFromTemplateRequest, _personalise

        definition = _personalise(
            to_workflow_definition(self._clinic()),
            CreateFromTemplateRequest(variables={"clinic_name": "City Clinic"}),
        )
        text = str(definition)
        assert "City Clinic" in text
        assert "{{clinic_name}}" not in text
        # Unanswered placeholders stay for the call to fill.
        assert "{{opening_hours}}" in text

    def test_the_greeting_is_replaced_verbatim(self):
        from api.routes.agent_templates import CreateFromTemplateRequest, _personalise

        definition = _personalise(
            to_workflow_definition(self._clinic()),
            CreateFromTemplateRequest(greeting="Namaste, Asha here from City Clinic."),
        )
        start = next(n for n in definition["nodes"] if n["type"] == "startCall")
        assert start["data"]["greeting"] == "Namaste, Asha here from City Clinic."

    def test_blank_answers_change_nothing(self):
        from api.routes.agent_templates import CreateFromTemplateRequest, _personalise

        original = to_workflow_definition(self._clinic())
        assert (
            _personalise(
                original,
                CreateFromTemplateRequest(
                    variables={"clinic_name": "  "}, greeting=" "
                ),
            )
            == original
        )


def test_a_workflow_with_a_handoff_counts_as_a_squad():
    """What the agent list's `is_squad` flag is derived from."""
    from api.services.workflow.squad import has_handoffs

    plain = to_workflow_definition(get_template("clinic_appointment"))
    assert not has_handoffs(plain)
    squad = {
        **plain,
        "nodes": [
            *plain["nodes"],
            {
                "id": "h1",
                "type": "handoff",
                "position": {"x": 0, "y": 0},
                "data": {"name": "Billing", "agent_uuid": "abc"},
            },
        ],
    }
    assert has_handoffs(squad)

"""Deriving the setup form from a template.

The product claim is that a non-technical seller sets an agent up without
opening a canvas. These tests are that claim: if the wrong fields are asked
for, or a required one is missed, the agent goes live saying
"{{business_name}}" out loud to a caller.
"""

import pytest

from api.services.workflow.setup_fields import (
    PER_CALL_VARIABLES,
    missing_required,
    placeholders_in,
    setup_fields_for,
)


def definition(*prompts: str) -> dict:
    return {
        "nodes": [
            {"id": f"n{i}", "type": "agentNode", "data": {"prompt": p}}
            for i, p in enumerate(prompts)
        ],
        "edges": [],
    }


class TestFindingPlaceholders:
    def test_it_reads_prompts_and_greetings(self):
        d = {
            "nodes": [
                {"id": "a", "data": {"prompt": "Call {{business_name}}"}},
                {"id": "b", "data": {"greeting": "We open {{opening_hours}}"}},
            ]
        }
        assert placeholders_in(d) == {"business_name", "opening_hours"}

    def test_whitespace_inside_the_braces_is_the_same_variable(self):
        """Someone will type {{ business_name }} eventually. It renders the
        same, so it must ask the same question."""
        assert placeholders_in(definition("{{ business_name }}")) == {"business_name"}

    def test_a_node_with_no_prompt_is_not_a_crash(self):
        assert placeholders_in({"nodes": [{"id": "a", "data": {}}]}) == set()

    def test_an_empty_definition_is_not_a_crash(self):
        assert placeholders_in({}) == set()


class TestWhichFieldsGetAsked:
    def test_per_call_variables_are_never_asked_at_setup(self):
        """The patient's name arrives from the contact row on each call. Asking
        a clinic to type it once during setup is nonsense, and whatever they
        typed would then be said to every caller."""
        fields = setup_fields_for(definition("Is that {{patient_name}}?"))
        assert fields == []

    def test_an_unknown_placeholder_still_becomes_a_field(self):
        """A template nobody has classified must be set up awkwardly rather
        than not at all. The alternative is a live agent with a hole in its
        greeting."""
        fields = setup_fields_for(definition("Ask about {{loyalty_scheme}}"))
        assert [f.name for f in fields] == ["loyalty_scheme"]
        assert fields[0].label == "Loyalty scheme"

    def test_known_fields_get_written_wording(self):
        field = setup_fields_for(definition("{{business_name}}"))[0]
        assert field.label == "Business name"
        assert "the way you say it" in field.hint

    def test_required_fields_come_before_optional_ones(self):
        """People stop filling a form when it stops looking mandatory."""
        d = definition("{{fees}} {{business_name}} {{address}} {{opening_hours}}")
        names = [f.name for f in setup_fields_for(d)]
        assert names.index("business_name") < names.index("fees")
        assert names.index("opening_hours") < names.index("address")

    def test_a_field_is_asked_once_however_often_it_appears(self):
        d = definition("{{business_name}}", "{{business_name}} again")
        assert len(setup_fields_for(d)) == 1


class TestMissingRequired:
    def test_nothing_filled_means_every_required_field_is_missing(self):
        d = definition("{{business_name}} {{opening_hours}} {{fees}}")
        assert set(missing_required(d, {})) == {"business_name", "opening_hours"}

    def test_blank_counts_as_missing(self):
        """An empty string reaches the prompt as nothing at all, and the agent
        greets the caller with a sentence that has a hole in it. Refusing to go
        live is the better failure."""
        d = definition("{{business_name}}")
        assert missing_required(d, {"business_name": "   "}) == ["business_name"]

    def test_optional_fields_never_block_going_live(self):
        d = definition("{{business_name}} {{fees}}")
        assert missing_required(d, {"business_name": "Sharma Dental"}) == []

    def test_none_is_survivable(self):
        """A workflow that has never been set up has no variables at all, not
        an empty dict."""
        assert missing_required(definition("{{business_name}}"), None) == [
            "business_name"
        ]


class TestAgainstTheRealTemplates:
    """The derivation is only worth anything if it works on what we ship."""

    @pytest.mark.parametrize(
        "name,build",
        [(n, b) for n, _, b in __import__(
            "api.services.workflow.clinic_pack", fromlist=["CLINIC_TEMPLATES"]
        ).CLINIC_TEMPLATES],
    )
    def test_every_clinic_template_asks_for_its_business_name(self, name, build):
        assert "business_name" in [f.name for f in setup_fields_for(build())]

    @pytest.mark.parametrize(
        "name,build",
        [(n, b) for n, _, b in __import__(
            "api.services.workflow.clinic_pack", fromlist=["CLINIC_TEMPLATES"]
        ).CLINIC_TEMPLATES],
    )
    def test_no_clinic_template_asks_for_a_per_call_variable(self, name, build):
        asked = {f.name for f in setup_fields_for(build())}
        assert not (asked & PER_CALL_VARIABLES), f"{name} asks for a per-call variable"

    def test_the_form_stays_short_enough_to_finish(self):
        """Ten minutes, in a clinic, standing up. A form that grows past a
        handful of questions is the canvas again with different styling."""
        from api.services.workflow.launch_templates import build_all

        for name, _description, definition in build_all():
            count = len(setup_fields_for(definition))
            assert count <= 6, f"{name} asks {count} setup questions"

"""A field that lists its options must render as a picker, not a text box.

``spec_field(default="free_text", options=[...])`` on a plain ``str`` used to
resolve to ``PropertyType.string``: the options rode along to the client and
were ignored, so the editor drew a free-text input for a field with a fixed set
of legal values. Nothing errored at any point.

Two things made that worse than a cosmetic slip. The operator types the value a
dropdown was meant to pick, so a near-miss ("Predefined", "numeric ") is stored
and only shows up in what the LLM returns. And ``display_options`` keyed on such
a field — ``show={"answer_type": ["predefined"]}`` — only reveals its dependent
field when the typed string matches the literal exactly, so the Options box
appears or does not appear for reasons the operator cannot see.

Three fields had it at once, in two different nodes, which is the argument for
asserting the property over the whole registry rather than fixing the three.
"""

from __future__ import annotations

from api.services.workflow.node_specs import REGISTRY, PropertySpec, get_spec


def _all_properties() -> list[tuple[str, PropertySpec]]:
    """Every property in every core node spec, including collection rows."""
    # Registration is lazy; asking for any core spec loads the rest.
    get_spec("qa")

    found: list[tuple[str, PropertySpec]] = []

    def walk(properties: list[PropertySpec], path: str) -> None:
        for prop in properties:
            found.append((f"{path}.{prop.name}", prop))
            if prop.properties:
                walk(prop.properties, f"{path}.{prop.name}")

    for name in sorted(REGISTRY):
        walk(REGISTRY[name].properties, name)
    return found


class TestOptionsImplyAPicker:
    def test_no_property_declares_options_and_renders_as_something_else(self):
        offenders = [
            (path, prop.type.value)
            for path, prop in _all_properties()
            if prop.options and prop.type.value not in ("options", "multi_options")
        ]

        assert not offenders, (
            "These properties list options but do not render as a picker, so "
            "the editor draws a free-text box and the options are never shown: "
            f"{offenders}"
        )

    def test_the_qa_extraction_selectors_are_pickers(self):
        """The two that sent us looking, named so a regression says which.

        ``answer_type`` also gates ``predefined_options`` through
        ``display_options``, so it being a text box took the Options field with
        it.
        """
        prop = next(p for p in get_spec("qa").properties if p.name == "qa_extractions")
        rows = {p.name: p for p in prop.properties or []}

        assert rows["answer_type"].type.value == "options"
        assert {o.value for o in rows["answer_type"].options or []} == {
            "free_text",
            "predefined",
        }
        assert rows["expected_format"].type.value == "options"

    def test_the_branch_operator_is_a_picker(self):
        """The third one, in a different node — and the reason this is a
        registry-wide assertion rather than two line edits."""
        prop = next(p for p in get_spec("branch").properties if p.name == "rules")
        rows = {p.name: p for p in prop.properties or []}

        assert rows["operator"].type.value == "options"
        assert rows["operator"].options

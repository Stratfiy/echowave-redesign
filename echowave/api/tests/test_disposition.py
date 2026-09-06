"""What the call achieved, as a label somebody can sort a hundred calls by.

Two risks shape these tests. A hallucinated code silently under-counts a
filter without ever appearing in the options list, so anything outside the
workflow's own taxonomy has to be dropped rather than stored. And an empty
result is indistinguishable from "not scored yet", so there is always at least
one label.
"""

import pytest

from api.services.workflow.disposition import (
    DEFAULT_DISPOSITIONS,
    MAX_DISPOSITIONS,
    UNCLEAR,
    allowed_codes,
    build_prompt,
    coerce_result,
    merge_taxonomies,
    normalise_code,
    parse_taxonomy,
)

DEFAULT = parse_taxonomy(None)


class TestTheTaxonomy:
    def test_nothing_configured_falls_back_to_the_defaults(self):
        """An agent that never touched this still classifies.

        Doing nothing would leave the Analysis screen empty for the same reason
        QA was empty for months — built, mounted, and never asked to run.
        """
        assert [entry["code"] for entry in DEFAULT] == [
            entry["code"] for entry in DEFAULT_DISPOSITIONS
        ]

    def test_reads_the_configured_list(self):
        """What `workflow_configurations.call_outcomes` stores."""
        taxonomy = parse_taxonomy(
            [{"code": "paid", "label": "Paid", "when": "Money arrived"}]
        )
        assert "paid" in allowed_codes(taxonomy)

    @pytest.mark.parametrize("key", ["call_outcomes", "dispositions"])
    def test_accepts_the_whole_configuration_block(self, key):
        """A caller that passed the config rather than the list inside it."""
        taxonomy = parse_taxonomy({key: [{"code": "paid", "label": "Paid"}]})
        assert "paid" in allowed_codes(taxonomy)

    def test_a_configuration_block_with_no_outcomes_falls_back(self):
        """An agent whose settings were saved before this field existed."""
        taxonomy = parse_taxonomy({"max_call_duration": 300})
        assert [e["code"] for e in taxonomy] == [
            e["code"] for e in DEFAULT_DISPOSITIONS
        ]

    def test_accepts_a_bare_list_of_codes(self):
        taxonomy = parse_taxonomy(["paid", "promised"])
        assert allowed_codes(taxonomy) == {"paid", "promised", UNCLEAR}

    def test_unclear_is_always_present(self):
        """Without it the model has nowhere to put a call it cannot read.

        It will then pick the nearest real outcome, which is how a bad line
        becomes a "not interested" in somebody's report.
        """
        taxonomy = parse_taxonomy([{"code": "paid"}])
        assert UNCLEAR in allowed_codes(taxonomy)

    def test_duplicates_collapse(self):
        taxonomy = parse_taxonomy(["paid", "Paid", "PAID"])
        assert [e["code"] for e in taxonomy].count("paid") == 1

    def test_is_capped(self):
        """Each entry costs prompt tokens on every call."""
        raw = [{"code": f"code_{i}"} for i in range(MAX_DISPOSITIONS + 20)]
        assert len(parse_taxonomy(raw)) <= MAX_DISPOSITIONS + 1  # +1 for unclear

    @pytest.mark.parametrize(
        "raw", [None, {}, [], "nonsense", [None], [{}], [{"code": ""}], [{"code": 7}]]
    )
    def test_unusable_configuration_falls_back_rather_than_raising(self, raw):
        assert len(parse_taxonomy(raw)) == len(DEFAULT_DISPOSITIONS)


class TestCodes:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("booked", "booked"),
            ("Call Back", "call_back"),
            ("not-interested", "not_interested"),
            ("  BOOKED  ", "booked"),
            ("wrong number!", "wrong_number"),
        ],
    )
    def test_normalises_what_a_person_would_type(self, raw, expected):
        assert normalise_code(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "123", "_leading", None, 7, "!!!"])
    def test_rejects_what_cannot_be_a_field_name(self, raw):
        """A code ends up as a CRM field, a URL parameter and a CSV heading."""
        assert normalise_code(raw) is None


class TestReadingTheModelsAnswer:
    def test_takes_several_labels(self):
        """One call can genuinely be two outcomes.

        Booked, and asked to be called back about something else — forcing one
        would lose the half somebody follows up on.
        """
        result = coerce_result({"dispositions": ["booked", "callback"]}, DEFAULT)
        assert result == ["booked", "callback"]

    @pytest.mark.parametrize(
        "raw",
        [
            {"dispositions": ["booked"]},
            {"disposition": ["booked"]},
            {"codes": ["booked"]},
            {"outcome": ["booked"]},
            ["booked"],
            "booked",
            "booked, booked",
        ],
    )
    def test_accepts_the_shapes_a_model_actually_returns(self, raw):
        assert coerce_result(raw, DEFAULT) == ["booked"]

    def test_drops_a_code_the_workflow_does_not_have(self):
        """A hallucinated code is worse than no code.

        It never appears in the filter's option list, so it silently makes
        every count wrong rather than showing up as a problem.
        """
        assert coerce_result(["booked", "sold_them_a_boat"], DEFAULT) == ["booked"]

    def test_deduplicates(self):
        assert coerce_result(["booked", "booked"], DEFAULT) == ["booked"]

    def test_unclear_never_sits_beside_a_real_outcome(self):
        """The model both could and could not tell. The real answer wins."""
        assert coerce_result(["booked", UNCLEAR], DEFAULT) == ["booked"]

    @pytest.mark.parametrize(
        "raw",
        [None, [], {}, "", "nonsense", ["nothing_valid"], 42, {"dispositions": []}],
    )
    def test_always_returns_at_least_one_label(self, raw):
        """An empty result cannot be told apart from "not scored yet"."""
        assert coerce_result(raw, DEFAULT) == [UNCLEAR]

    def test_respects_a_custom_taxonomy(self):
        taxonomy = parse_taxonomy(["paid", "promised"])
        assert coerce_result(["paid", "booked"], taxonomy) == ["paid"]


class TestThePrompt:
    def test_lists_every_code(self):
        """A model asked to classify without seeing the options invents them."""
        prompt = build_prompt(DEFAULT)
        for entry in DEFAULT:
            assert entry["code"] in prompt

    def test_says_more_than_one_is_allowed(self):
        assert "more than one" in build_prompt(DEFAULT)

    def test_explains_when_to_use_unclear(self):
        assert UNCLEAR in build_prompt(DEFAULT)


class TestOneMenuForTheWholeOrganization:
    """The calls list is org-wide; a taxonomy is per agent."""

    def test_the_defaults_are_always_offered(self):
        """An agent nobody configured is still producing these labels."""
        codes = {entry["code"] for entry in merge_taxonomies([])}
        assert codes == {entry["code"] for entry in DEFAULT_DISPOSITIONS}

    def test_every_agents_codes_are_offered(self):
        """Otherwise a box is missing for calls that can only match it."""
        merged = merge_taxonomies([["paid"], ["address_confirmed"]])
        codes = {entry["code"] for entry in merged}
        assert {"paid", "address_confirmed"} <= codes

    def test_a_code_two_agents_share_appears_once(self):
        merged = merge_taxonomies([["paid"], ["paid"]])
        assert [entry["code"] for entry in merged].count("paid") == 1

    def test_the_first_label_wins_rather_than_the_last(self):
        """Two agents can label one code differently and neither is wrong.

        Deciding deterministically at least stops the menu reshuffling between
        page loads.
        """
        merged = merge_taxonomies(
            [
                [{"code": "paid", "label": "Paid in full"}],
                [{"code": "paid", "label": "Payment taken"}],
            ]
        )
        label = next(e["label"] for e in merged if e["code"] == "paid")
        assert label == "Paid in full"

    @pytest.mark.parametrize("raw", [None, [], {}, ""])
    def test_an_unconfigured_agent_adds_nothing_and_raises_nothing(self, raw):
        merged = merge_taxonomies([raw])
        assert len(merged) == len(DEFAULT_DISPOSITIONS)

"""The outcome board: what each bot achieved, counted.

The classifier has been writing outcomes onto every completed run for months
and nothing read them back. These tests pin the reading, and in particular
the three places a count like this quietly goes wrong: a zero that is not
shown, a renamed code that deletes history, and a malformed row that takes
the whole tally with it.
"""

from api.services.workflow.outcome_board import (
    WINDOWS,
    BoardEntry,
    OutcomeCount,
    codes_on,
    is_configured,
    tally,
)

TAXONOMY = [
    {"code": "booked", "label": "Booked", "when": "An appointment is confirmed"},
    {"code": "callback", "label": "Call back", "when": "Asked for another time"},
]


def annotated(*codes: str) -> dict:
    return {"disposition": {"dispositions": list(codes)}}


class TestCodesOnOneRun:
    def test_reads_the_codes_the_classifier_wrote(self):
        assert codes_on(annotated("booked", "callback")) == ["booked", "callback"]

    def test_an_unclassified_run_has_none(self):
        assert codes_on({}) == []

    def test_a_null_annotations_column_costs_that_run_and_nothing_else(self):
        assert codes_on(None) == []

    def test_a_disposition_that_is_not_an_object_is_not_a_crash(self):
        assert codes_on({"disposition": "booked"}) == []

    def test_a_dispositions_value_that_is_not_a_list_is_not_a_crash(self):
        assert codes_on({"disposition": {"dispositions": "booked"}}) == []

    def test_non_string_entries_are_dropped_and_the_rest_kept(self):
        assert codes_on({"disposition": {"dispositions": ["booked", 7, None, ""]}}) == [
            "booked"
        ]


class TestTally:
    def test_counts_each_configured_outcome(self):
        rows, _, _ = tally(
            TAXONOMY, [annotated("booked"), annotated("booked"), annotated("callback")]
        )
        assert rows == [
            OutcomeCount(code="booked", label="Booked", count=2),
            OutcomeCount(code="callback", label="Call back", count=1),
        ]

    def test_shows_an_outcome_that_never_happened_rather_than_hiding_it(self):
        # The zero is the finding: nobody has ever been booked.
        rows, _, _ = tally(TAXONOMY, [annotated("callback")])
        assert rows[0] == OutcomeCount(code="booked", label="Booked", count=0)

    def test_one_run_with_the_same_code_twice_counts_once(self):
        rows, _, _ = tally(TAXONOMY, [annotated("booked", "booked")])
        assert rows[0].count == 1

    def test_a_run_can_carry_two_outcomes_and_counts_for_both(self):
        rows, _, _ = tally(TAXONOMY, [annotated("booked", "callback")])
        assert [row.count for row in rows] == [1, 1]

    def test_runs_counts_every_run_classified_or_not(self):
        _, runs, _ = tally(TAXONOMY, [annotated("booked"), {}, None])
        assert runs == 3

    def test_classified_counts_only_the_runs_that_carry_an_outcome(self):
        _, _, classified = tally(TAXONOMY, [annotated("booked"), {}, None])
        assert classified == 1

    def test_a_code_no_longer_in_the_taxonomy_still_counts_as_classified(self):
        # Renaming an outcome does not un-happen the calls it labelled. If the
        # run left the denominator, every surviving rate would read better
        # than it is.
        rows, runs, classified = tally(TAXONOMY, [annotated("retired_code")])
        assert [row.count for row in rows] == [0, 0]
        assert (runs, classified) == (1, 1)

    def test_no_runs_is_zeroes_and_not_an_empty_board(self):
        rows, runs, classified = tally(TAXONOMY, [])
        assert [row.count for row in rows] == [0, 0]
        assert (runs, classified) == (0, 0)

    def test_an_empty_taxonomy_yields_no_rows_but_still_counts_runs(self):
        rows, runs, classified = tally([], [annotated("booked")])
        assert rows == []
        assert (runs, classified) == (1, 1)


class TestTheWindowsWeOffer:
    def test_are_the_same_three_the_analytics_range_picker_offers(self):
        # Two range pickers with different windows is how a reader compares
        # two numbers that do not cover the same days.
        assert WINDOWS == (7, 30, 90)


class TestABoardEntry:
    def test_defaults_to_no_outcomes_rather_than_a_shared_list(self):
        one = BoardEntry(workflow_id=1, name="a")
        two = BoardEntry(workflow_id=2, name="b")
        one.outcomes.append(OutcomeCount(code="x", label="X", count=1))
        assert two.outcomes == []


class TestWhetherTheOutcomesAreTheBotsOwn:
    """``parse_taxonomy`` never returns empty, so "has a taxonomy" cannot be
    the question. The screen needs to know whether anybody here decided what
    a win is."""

    def test_a_bot_with_its_own_outcomes_is_configured(self):
        assert is_configured({"call_outcomes": [{"code": "paid", "label": "Paid"}]})

    def test_a_bot_nobody_configured_is_not(self):
        assert not is_configured({})

    def test_an_empty_list_is_not_a_decision(self):
        assert not is_configured({"call_outcomes": []})

    def test_a_missing_configuration_block_is_not_a_crash(self):
        assert not is_configured(None)

    def test_entries_with_no_code_do_not_count_as_a_decision(self):
        assert not is_configured({"call_outcomes": [{"label": "Paid"}]})

    def test_the_bare_string_shape_older_clients_wrote_still_counts(self):
        assert is_configured({"call_outcomes": ["paid"]})

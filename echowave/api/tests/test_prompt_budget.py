"""What a prompt may cost, named per source.

The defect: every block in front of a model grows with the account that
owns it, and nothing said how much a prompt may carry. The ceiling was
whichever customer grew fastest, and the way we would have found out is a
400 on their biggest workspace.

The voice path already learned this -- MAX_REMEMBERED caps confirmed facts
at forty. The chat path reads the same table through a different function
and had no cap at all.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from api.services import prompt_budget
from api.services.workflow import decibyl


def _fact(key="policy", value="we open at nine"):
    return SimpleNamespace(key=key, value=value, kind="fact")


def _event(summary="asked about a refund"):
    return SimpleNamespace(workflow_id=1, at=datetime.now(UTC), summary=summary)


class TestEstimating:
    def test_an_empty_string_costs_nothing(self):
        assert prompt_budget.estimate("") == 0
        assert prompt_budget.estimate(None) == 0

    def test_it_errs_high_rather_than_low(self):
        """Being wrong towards "smaller" is the direction that fails."""
        text = "x" * 3500
        assert prompt_budget.estimate(text) >= 1000

    def test_it_grows_with_the_text(self):
        assert prompt_budget.estimate("x" * 100) < prompt_budget.estimate("x" * 200)


class TestClipping:
    def test_a_short_value_is_untouched(self):
        assert prompt_budget.clip("we open at nine", 300) == "we open at nine"

    def test_a_long_value_is_cut_visibly(self):
        """A model handed half a sentence with no sign of it finishes the
        sentence itself, and what it invents reads like what was confirmed."""
        clipped = prompt_budget.clip("x" * 900, 50)
        assert len(clipped) == 50
        assert clipped.endswith("…")

    def test_nothing_is_not_an_error(self):
        assert prompt_budget.clip(None, 10) == ""
        assert prompt_budget.clip("   ", 10) == ""


class TestSayingWhatWasLeftOut:
    def test_a_short_list_is_whole(self):
        assert prompt_budget.lines(["- a", "- b"], max_items=5, noun="fact") == [
            "- a",
            "- b",
        ]

    def test_what_was_left_out_is_said_not_dropped(self):
        """A model silently given forty of two hundred believes it has them
        all. AGENTS.md's silent-absence rule, where a blocklist cannot apply.
        """
        out = prompt_budget.lines(
            [f"- {n}" for n in range(200)], max_items=40, noun="fact"
        )
        assert len(out) == 41
        assert out[-1] == "- and 160 more facts not shown here"

    def test_exactly_one_left_out_reads_as_one(self):
        """ "1 more facts" is the line that tells a reader nobody ran it."""
        out = prompt_budget.lines(["- a", "- b", "- c"], max_items=2, noun="fact")
        assert out[-1] == "- and 1 more fact not shown here"

    def test_an_irregular_plural_can_be_given(self):
        out = prompt_budget.lines(
            ["- a", "- b", "- c"], max_items=1, noun="entry", plural="entries"
        )
        assert out[-1] == "- and 2 more entries not shown here"


class TestTheChatContextIsBounded:
    def test_two_hundred_facts_no_longer_cost_what_they_did(self):
        """Measured at 28,089 characters before this, against 5,842 for the
        same two hundred on the voice side."""
        block = decibyl.memory_block(
            [_fact(f"policy {n}", "detail " * 20) for n in range(200)]
        )
        assert len(block) < 8_000
        assert "160 more facts" in block

    def test_one_enormous_fact_cannot_spend_the_prompt(self):
        """A row count is only half a bound: one 60,000-character value
        passes any number of rows."""
        block = decibyl.memory_block([_fact("notes", "x" * 60_000)])
        assert len(block) < prompt_budget.MAX_FACT_CHARS + 100

    def test_an_empty_memory_still_says_so(self):
        assert decibyl.memory_block([]) == "Nothing confirmed yet."

    def test_a_learned_fact_is_still_excluded(self):
        """The cap must not quietly change what is eligible."""
        rows = [_fact(), SimpleNamespace(key="guess", value="maybe", kind="learned")]
        assert "guess" not in decibyl.memory_block(rows)

    def test_a_pasted_transcript_in_one_event_is_clipped(self):
        block = decibyl.recent_block([_event("x" * 40_000)], {1: "Front desk"})
        assert len(block) < prompt_budget.MAX_EVENT_CHARS + 200

    def test_an_empty_timeline_still_says_so(self):
        assert decibyl.recent_block([], {}) == "Nothing recorded lately."

    def test_the_bot_name_survives_the_clipping(self):
        """Clipping the summary must not cost the line its subject."""
        block = decibyl.recent_block([_event("x" * 40_000)], {1: "Front desk"})
        assert "Front desk" in block

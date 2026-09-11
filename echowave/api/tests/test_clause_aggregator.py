"""A clause at a time: earlier than a sentence, safer than a token.

Sarvam is fed whole sentences because tokens clipped its Tamil. The cost is
that the caller hears nothing until the brain finishes its first sentence, and
gpt-5-mini opens with long ones. A clause is the grain a person actually pauses
at, and a Tamil clause carries enough context to pronounce.

The tests that matter are the ones about *not* splitting: inside a number
being read back, inside Indian digit grouping, and before there is anything
worth saying. A voice that says "Yes" and then waits is worse than one that
waits for the sentence.
"""

from __future__ import annotations

import pytest
from pipecat.utils.text.base_text_aggregator import AggregationType

from api.services.pipecat.clause_aggregator import (
    LONG_CLAUSE_CHARS,
    MIN_CLAUSE_CHARS,
    ClauseTextAggregator,
)


async def feed(aggregator: ClauseTextAggregator, *chunks: str) -> list[str]:
    """Push text as an LLM would -- in pieces -- and collect what comes out."""
    out: list[str] = []
    for chunk in chunks:
        async for aggregation in aggregator.aggregate(chunk):
            out.append(aggregation.text)
    return out


def clause() -> ClauseTextAggregator:
    return ClauseTextAggregator(aggregation_type=AggregationType.SENTENCE)


@pytest.mark.asyncio
class TestItSpeaksAtTheComma:
    async def test_a_long_clause_is_released_at_its_comma(self):
        out = await feed(
            clause(),
            "Thanks for calling Narayani Dental Clinic in Hosur, ",
            "how can I help you today?",
        )
        # The first clause is spoken while the brain is still writing the
        # second, which is the whole point.
        assert out[0] == "Thanks for calling Narayani Dental Clinic in Hosur,"

    async def test_the_sentence_end_still_ends_it(self):
        out = await feed(clause(), "We are open from nine to six. ", "Next")
        assert out[0] == "We are open from nine to six."

    async def test_semicolons_and_dashes_count_too(self):
        out = await feed(clause(), "Your slot is confirmed for Tuesday morning; ", "x")
        assert out and out[0].endswith(";")

    async def test_token_mode_passes_straight_through(self):
        """A vendor fed tokens must not suddenly get clauses."""
        aggregator = ClauseTextAggregator(aggregation_type=AggregationType.TOKEN)
        out = await feed(aggregator, "Hel", "lo, ", "there")
        assert out == ["Hel", "lo, ", "there"]


@pytest.mark.asyncio
class TestItHoldsOnWhenItShould:
    async def test_a_syllable_before_a_comma_is_not_a_clause(self):
        """\"Yes,\" alone is a voice that says one word and then waits."""
        out = await feed(clause(), "Yes, ", "I can book that for you now. ", "x")
        # Nothing was released at the first comma; the whole sentence came out
        # together at the full stop.
        assert out[0].startswith("Yes, I can book")

    async def test_indian_digit_grouping_is_not_a_boundary(self):
        """1,00,000 has a comma after every two digits. Each one is a digit
        on both sides, and none of them is a pause."""
        out = await feed(
            clause(),
            "The total for the shipment comes to rupees 1,",
            "00,",
            "000 including tax. ",
            "x",
        )
        assert len(out) == 1
        assert "1,00,000" in out[0]

    async def test_a_number_being_read_back_is_not_split(self):
        """The digit-spacing transform runs on aggregated text; a number split
        across two clauses would be spaced in one half and not the other."""
        out = await feed(
            clause(),
            "Your registration is T N 7 0 A V 8 4 8 0, ",
            "is that right? ",
            "x",
        )
        # Held past the comma because the tail was digits; released at the
        # question mark as one piece.
        assert len(out) == 1
        assert "8 4 8 0, is that right?" in out[0]

    async def test_but_prose_that_merely_ends_in_a_number_is_released_eventually(self):
        """A very long run without a sentence end is prose, and the caller is
        waiting. Past the long threshold a boundary is taken regardless."""
        long_prose = "we can take the delivery at the Hosur depot any day next week after ten in the morning and before four in the afternoon on 2026, "
        assert len(long_prose) >= LONG_CLAUSE_CHARS
        out = await feed(clause(), long_prose, "x")
        assert out and out[0].endswith(",")

    async def test_the_threshold_is_a_real_clause_length(self):
        """Sanity on the constant: a greeting's first clause clears it, a
        one-word interjection does not."""
        assert len("Thanks for calling Narayani,") >= MIN_CLAUSE_CHARS
        assert len("Yes,") < MIN_CLAUSE_CHARS


@pytest.mark.asyncio
class TestItInheritsTheParentsCare:
    async def test_a_decimal_point_is_not_a_sentence_end(self):
        """The parent's lookahead is what keeps 'Rs. 500' and '$29.' from being
        cut; building on it rather than beside it keeps that."""
        out = await feed(clause(), "That will be Rs. 500 for the visit. ", "x")
        assert out[0] == "That will be Rs. 500 for the visit."

    async def test_nothing_is_lost_between_clauses(self):
        text = "First we confirm your number, then we send the code, then you read it back to me. "
        out = await feed(clause(), text, "x")
        assert " ".join(out).replace("  ", " ").strip() == text.strip()


@pytest.mark.asyncio
class TestIndianAbbreviationsAreNotSentenceEnds:
    """Pipecat's own sentence detector splits at "Rs." -- measured, not
    supposed -- so every agent quoting a rupee amount pauses in the middle of
    it. The clause aggregator checks before the parent gets the chance."""

    async def test_rupees_stay_with_their_amount(self):
        out = await feed(clause(), "That will be Rs. 500 for the visit. ", "x")
        assert out[0] == "That will be Rs. 500 for the visit."

    async def test_a_numbered_thing_stays_whole(self):
        out = await feed(clause(), "Bus No. 27 stops right outside the clinic. ", "x")
        assert out[0] == "Bus No. 27 stops right outside the clinic."

    async def test_a_word_that_merely_ends_in_rs_is_untouched(self):
        """ "bars." is a sentence end; the guard wants a word boundary."""
        out = await feed(clause(), "We sell chocolate bars. ", "Next one.")
        assert out[0] == "We sell chocolate bars."

    async def test_a_comma_after_a_year_is_still_a_pause(self):
        """Lookahead: a digit before the comma only *might* be grouping. A
        space after settles it as a pause."""
        out = await feed(
            clause(),
            "We reopened after the renovation in 2026, ",
            "and the new wing is ready.",
        )
        assert out[0].endswith("2026,")

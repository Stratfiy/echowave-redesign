"""Hand the voice a clause at a time, not a sentence and not a token.

Sarvam is fed whole sentences, deliberately. Token feeding handed it two or
three Tamil words at a time with no context and the words came out clipped and
mispronounced, in every language -- clarity is the product, so the sentence
default stayed. The cost is that the caller hears nothing until the brain has
finished its first sentence, and a brain like gpt-5-mini opens with long ones.
On the Default tier that wait is most of the reply time.

Pipecat offers exactly two grains, token and sentence. This is the one in
between. A clause is the natural unit of spoken delivery -- a person pauses at
a comma -- and a Tamil clause carries enough context for Sarvam to pronounce
it, which a token does not. So the first byte moves from "first sentence" to
"first clause", and pronunciation keeps everything it had.

**What counts as a boundary, and what does not.** A comma, semicolon, colon or
dash, when the buffer already holds enough to be worth saying. Not a comma in
"1,00,000" -- that is a digit on both sides, and Indian number grouping puts
commas everywhere. Not a comma in the first few characters, because "Yes," on
its own is a syllable that then waits for the rest. And never inside a number
being read back: the digit-spacing transform runs on aggregated text, so a
registration number split across two clauses would be spaced in one half and
not the other.

Sentence boundaries still end a clause, exactly as before; this only adds
earlier ones. Built on the sentence aggregator rather than beside it so the
lookahead logic that keeps "$29. Next" and "Rs. 500" straight is inherited
rather than reimplemented.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

#: Punctuation a speaker pauses at. The Devanagari danda ``।`` ends a sentence
#: and is handled upstream; these are the mid-sentence pauses.
CLAUSE_PUNCTUATION = frozenset({",", ";", ":", "—", "–"})

#: Full stops that do not end a sentence, and that pipecat's own sentence
#: detector does not know about. NLTK's English model knows "Dr." and "Mr."
#: and splits at "Rs." -- so today, on every Indian agent, "That will be
#: Rs. 500 for the visit" is spoken as "That will be Rs." *pause* "500 for the
#: visit." Measured, not supposed: the parent aggregator alone does exactly
#: that. These are checked before the parent gets a chance to.
ABBREVIATIONS = frozenset(
    {"rs.", "re.", "no.", "nos.", "pvt.", "ltd.", "sh.", "smt.", "km.", "kg.", "gm."}
)

#: A clause has to be worth saying on its own. Below this the aggregator holds
#: on: "Yes," is a syllable, and shipping it alone means a voice that says
#: "Yes" and then waits for the brain to finish the thought.
MIN_CLAUSE_CHARS = 24

#: Once the buffer is this long a clause boundary is taken even if the tail
#: looks like it might be mid-number: a very long run without a sentence end
#: is prose, and the caller is waiting.
LONG_CLAUSE_CHARS = 120


def _is_digit_grouping(text: str) -> bool:
    """Is the last comma a digit separator, as in ``1,00,000``?

    The buffer ends with the comma; a digit before it makes it a *candidate*
    for grouping, and the character after decides -- the same lookahead the
    parent uses for a full stop, for the same reason.
    """
    if len(text) < 2:
        return False
    return text[-2].isdigit()


#: Same width as the full stop it stands in for, and not something NLTK
#: treats as an end of sentence.
_MASK = "\u2024"  # one dot leader

_ABBREVIATION_PATTERN = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(re.escape(a[:-1]) for a in sorted(ABBREVIATIONS))
    + r")\.",
    re.IGNORECASE,
)


def _mask_abbreviations(text: str) -> str:
    """``Rs. 500`` becomes ``Rs‸ 500`` for the detector's eyes only.

    The look-behind wants a word boundary before it, so "bars." is a sentence
    end and "Rs." is not.
    """
    return _ABBREVIATION_PATTERN.sub(lambda m: m.group(1) + _MASK, text)


def _mid_number(text: str) -> bool:
    """Is the buffer in the middle of reading a number back?

    The tail is a run of digits, spaced digits and single capital letters --
    "T N 7 0 A V 8 4 8 0" -- which is what a caller is writing down. A clause
    break here would hand the voice half a number. Four digits on their own
    is a year, and a comma after a year is a pause.
    """
    run: list[str] = []
    for char in reversed(text.rstrip()):
        if char.isdigit() or char == " " or (char.isalpha() and char.isupper()):
            run.append(char)
        else:
            break
    compact = "".join(run).replace(" ", "")
    # A year is four digits and a pause after it is a pause. A phone number,
    # a registration, a tracking id is longer than that, and mostly digits.
    return len(compact) >= 5 and sum(c.isdigit() for c in compact) >= 3


class ClauseTextAggregator(SimpleTextAggregator):
    """The sentence aggregator, with clause boundaries added.

    Everything the parent does is kept: sentence detection with lookahead,
    token mode passing straight through, interruption handling. Only the
    per-character check gains a second way to end an aggregation.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        #: A clause that ended on a comma after a digit, held until the next
        #: character says whether that comma was a pause or a digit separator.
        self._pending_comma: str | None = None

    async def reset(self):
        await super().reset()
        self._pending_comma = None

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        if self._aggregation_type == AggregationType.TOKEN:
            async for aggregation in super().aggregate(text):
                yield aggregation
            return

        for char in text:
            # A comma after a digit was left undecided last time round: this
            # character settles it. A digit means grouping ("1,00,000") and the
            # comma was never a pause; anything else and it was.
            if self._pending_comma is not None:
                held = self._pending_comma
                self._pending_comma = None
                if not char.isdigit():
                    self._text = ""
                    yield Aggregation(text=held, type=AggregationType.SENTENCE)

            self._text += char

            # A sentence end wins, exactly as before. The abbreviation guard
            # lives inside the lookahead, not here: skipping the full stop is
            # not enough, because the detector re-reads the whole buffer at
            # the *next* sentence end and would find "Rs." then.
            result = await self._check_sentence_with_lookahead(char)
            if result:
                yield result
                continue

            if char in CLAUSE_PUNCTUATION and self._clause_is_ready():
                clause = self._text.strip(" ")
                if char == "," and _is_digit_grouping(self._text):
                    # Decide when the next character arrives.
                    self._pending_comma = clause
                    continue
                self._text = ""
                yield Aggregation(text=clause, type=AggregationType.SENTENCE)

    async def _check_sentence_with_lookahead(self, char: str) -> Aggregation | None:
        """The parent's lookahead, run on a buffer whose abbreviations are hidden.

        NLTK reads the whole buffer every time, so a full stop in "Rs." is a
        candidate boundary at every later check, not only when it arrives.
        The abbreviation full stops are swapped for a same-width placeholder
        before the parent looks, and swapped back in whatever it returns.
        Same width matters: the parent slices the buffer by position.
        """
        if not (self._needs_lookahead and char.strip()):
            return await super()._check_sentence_with_lookahead(char)

        original = self._text
        masked = _mask_abbreviations(original)
        if masked == original:
            return await super()._check_sentence_with_lookahead(char)

        self._text = masked
        result = await super()._check_sentence_with_lookahead(char)
        consumed = len(original) - len(self._text)
        self._text = original[consumed:]
        if result is None:
            return None
        return Aggregation(
            text=original[:consumed].strip(" "), type=AggregationType.SENTENCE
        )

    def _clause_is_ready(self) -> bool:
        buffered = self._text
        if len(buffered) < MIN_CLAUSE_CHARS:
            return False
        if _mid_number(buffered[:-1]) and len(buffered) < LONG_CLAUSE_CHARS:
            return False
        return True

"""Stage directions never reach the speaker.

A model told to sound warm writes warmth the way a script does — ``*pauses*``,
``(softly)``, ``[laughs]``, ``<sighs>`` — and a TTS engine has no idea that is
an instruction rather than a line. It reads it out. The customer hears
"asterisk pauses warmly asterisk", or worse, hears it pronounced, and the call
is over as a demo.

The chain already had one filter, ``XMLFunctionTagFilter``, for the other
thing models emit that is not speech: ``<function=end_call>``. This is the
same idea for the other half.

WHAT IT REMOVES, and only this:

* ``*...*`` and ``_..._`` — markdown emphasis, which in a spoken reply is
  almost always a stage direction rather than stress.
* ``(...)`` and ``[...]`` and ``<...>`` — but ONLY when the contents look like
  a direction: one to four words, no digits, no sentence punctuation.

WHAT IT LEAVES, which is the harder half:

* "(see below)" style asides are gone, yes — but a parenthetical carrying a
  number, a date, a price or a full clause is kept, because that is content a
  caller asked for. "Your balance is ₹1,240 (as of this morning)" survives.
* An asterisk that is not paired, and a lone underscore in an identifier, are
  left alone: a bare ``*`` is arithmetic or a typo, not markup.
* "(one moment)", "(see the email)" and anything else that is simply short
  are kept for the same reason.

The bias is deliberate: leaving a direction in is embarrassing, but cutting a
caller's actual answer out is worse, so every rule here is narrow and refuses
when unsure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from loguru import logger

from pipecat.utils.text.base_text_filter import BaseTextFilter

#: Inside ``*...*`` the rule can be loose: an asterisk pair in a spoken reply
#: is markup by definition, whatever it wraps. Four words, no line breaks.
_EMPHASISED = r"[A-Za-z][A-Za-z'\u2019\- ]{0,40}"

#: Inside brackets it has to be tight, because a parenthetical is often the
#: answer the caller asked for. "(as of this morning)" is four words with no
#: digits and would pass a length rule -- it did, on the first draft of this
#: file, and cut the qualifier off a balance.
#:
#: So a bracket is only a direction when it READS like one: an -ing or -ly
#: word (laughing, softly, warmly), or one of the handful of bare nouns and
#: verbs that carry a direction without either ending. Anything else stays.
_DIRECTION_WORDS = (
    "laughs|laugh|laughing|chuckles|chuckling|sighs|sigh|sighing|pause|pauses"
    "|paused|pausing|beat|silence|whispers|whispering|smiles|smiling|nods"
    "|nodding|coughs|clears throat|clearing throat|breathes|breathing"
)
#: Two shapes, and the difference between them is the whole safety margin.
#:
#: A word off the list may take a plain modifier in front of it -- "[long
#: pause]", "[awkward silence]" -- because the list is what makes it a
#: direction and the modifier is only describing it.
#:
#: A bare -ing or -ly word may NOT. "morning" ends in -ing, so allowing a
#: modifier there would make "(this morning)" a direction, and that is
#: content. It may only pair with another -ing or -ly word: "(pausing
#: briefly)".
_DIRECTIONAL = (
    rf"(?:[A-Za-z]+\s+)?(?:{_DIRECTION_WORDS})(?:\s+[A-Za-z]+(?:ing|ly))?"
    rf"|[A-Za-z]+(?:ing|ly)(?:\s+[A-Za-z]+(?:ing|ly))?"
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # *pauses warmly*  /  _softly_ -- paired, and never across a line, so an
    # unmatched asterisk in arithmetic cannot swallow a whole reply.
    re.compile(rf"\*{_EMPHASISED}\*"),
    re.compile(rf"(?<![A-Za-z0-9_])_{_EMPHASISED}_(?![A-Za-z0-9_])"),
    # (softly) / [laughs] / <sighs> -- only when the contents read as a
    # direction, never merely because they are short.
    re.compile(rf"\(\s*(?:{_DIRECTIONAL})\s*\)", re.IGNORECASE),
    re.compile(rf"\[\s*(?:{_DIRECTIONAL})\s*\]", re.IGNORECASE),
    re.compile(rf"<\s*(?:{_DIRECTIONAL})\s*>", re.IGNORECASE),
)


def strip_stage_directions(text: str) -> str:
    """Remove stage directions from ``text``, leaving speech.

    Pure and synchronous so it can be tested without a pipeline, and so the
    rules can be read in one place.
    """
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(" ", out)
    # A removal leaves a gap, and two spaces before a full stop is audible on
    # some engines as a longer pause than the writer meant.
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


class StageDirectionFilter(BaseTextFilter):
    """Drop stage directions before the TTS engine reads them aloud."""

    def __init__(self, *, extra_patterns: list[str] | None = None) -> None:
        """Build the filter.

        Args:
            extra_patterns: Additional regexes to remove, for a deployment that
                finds a shape this does not cover. They are applied after the
                built-in ones.
        """
        self._extra = [re.compile(p) for p in (extra_patterns or [])]

    async def update_settings(self, settings: Mapping[str, Any]) -> None:
        """Apply a settings change. Only ``extra_patterns`` is understood."""
        if "extra_patterns" in settings:
            self._extra = [re.compile(p) for p in (settings["extra_patterns"] or [])]

    async def filter(self, text: str) -> str:
        """Return ``text`` with stage directions removed."""
        spoken = strip_stage_directions(text)
        for pattern in self._extra:
            spoken = pattern.sub(" ", spoken)
        spoken = re.sub(r"\s{2,}", " ", spoken).strip()
        if spoken != text:
            # Worth a line in the log: a model that keeps writing directions is
            # a prompt problem, and this is the only place it is visible.
            logger.info(
                "StageDirectionFilter removed a direction: %r -> %r",
                text[:120],
                spoken[:120],
            )
        return spoken

    async def handle_interruption(self) -> None:
        """Nothing to do: the filter holds no state between utterances."""

    async def reset_interruption(self) -> None:
        """Nothing to do: the filter holds no state between utterances."""


__all__ = ["StageDirectionFilter", "strip_stage_directions"]

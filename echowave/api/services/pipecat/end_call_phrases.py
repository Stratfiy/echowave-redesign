"""Hang up when the caller says goodbye.

"Okay bye", "thanks, that's all" — a caller who says one of these is done,
and the worst thing the agent can do next is take a model turn to reply at
length while they hold the phone away from their ear. This watches the
caller's final transcriptions for the agent's configured phrases and, on a
match, has the engine say its farewell and end the call, with no model turn
in between.

Off unless the agent lists phrases: the match is deliberately literal, and
a phrase list nobody wrote would hang up on nobody's idea of goodbye.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# A short utterance that *contains* a phrase counts; a long one has to *be*
# it. "Okay thanks bye" should hang up; "bye the way, one more question"
# should not — and neither should a sentence that merely mentions goodbye.
CONTAINS_MAX_WORDS = 5

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def matches(text: str, phrases: Sequence[str]) -> str | None:
    """The phrase the caller's utterance matched, if any."""
    said = normalise(text)
    if not said:
        return None
    words = said.split()
    for phrase in phrases:
        wanted = normalise(phrase)
        if not wanted:
            continue
        if said == wanted:
            return phrase
        if len(words) <= CONTAINS_MAX_WORDS and f" {wanted} " in f" {said} ":
            return phrase
    return None


def end_call_phrases(run_configs: Mapping[str, Any] | None) -> list[str]:
    if not run_configs:
        return []
    raw = run_configs.get("end_call_phrases")
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return []
    return [p.strip() for p in raw if isinstance(p, str) and p.strip()]


def end_call_farewell(run_configs: Mapping[str, Any] | None) -> str | None:
    if not run_configs:
        return None
    text = run_configs.get("end_call_farewell")
    return text.strip() if isinstance(text, str) and text.strip() else None


class EndCallPhraseWatcher(FrameProcessor):
    """Watch final transcriptions for a goodbye and end the call on one.

    Sits after STT and before the context aggregator. The matching frame is
    still forwarded, so the transcript keeps the caller's goodbye — but the
    engine is told first, so by the time the frame reaches the aggregator
    the pipeline is muted and no model turn starts.
    """

    def __init__(
        self,
        *,
        phrases: Sequence[str],
        on_match: Callable[[str], Awaitable[None]],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._phrases = list(phrases)
        self._on_match = on_match
        self.matched: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            isinstance(frame, TranscriptionFrame)
            and self.matched is None
            and self._phrases
        ):
            phrase = matches(frame.text, self._phrases)
            if phrase:
                self.matched = phrase
                logger.info(f"Caller said {frame.text!r}; end-call phrase {phrase!r}")
                await self._on_match(phrase)

        await self.push_frame(frame, direction)

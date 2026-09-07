"""The pipeline half of spoken-digit handling.

Split from ``spoken_digits`` so the rules themselves import nothing from
pipecat. That is not tidiness: the same normalisation is wanted by extraction,
QA and anything reading a stored transcript, none of which have a pipeline, and
a rule set that can only be exercised inside a running pipeline is one nobody
writes a test for.
"""

from __future__ import annotations

from loguru import logger

from api.services.pipecat.spoken_digits import (
    MIN_RUN_TOKENS,
    normalise_spoken_digits,
)
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class SpokenDigitNormaliser(FrameProcessor):
    """Rewrite spoken digit runs on transcriptions as they leave STT.

    Placed before anything that consumes a transcription — the language
    follower, the context aggregator, extraction, QA — so every one of them
    sees the same corrected text rather than each re-deriving it. The number
    only has to be got right once, and this is the earliest point where it can
    be.

    It edits ``frame.text`` in place rather than constructing a replacement
    frame: a ``TranscriptionFrame`` carries a user id, a timestamp and the
    detected language, and rebuilding it here would mean re-deriving all three
    and silently dropping any field a future pipecat adds.
    """

    def __init__(self, min_run_tokens: int = MIN_RUN_TOKENS, **kwargs):
        super().__init__(**kwargs)
        self._min_run_tokens = min_run_tokens

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            original = getattr(frame, "text", "") or ""
            try:
                rewritten = normalise_spoken_digits(original, self._min_run_tokens)
            except Exception as error:  # noqa: BLE001 - never drop a transcript
                # A transcript reaching the LLM slightly wrong is recoverable;
                # one that never arrives is a call that stops responding.
                logger.warning(f"Digit normalisation failed, passing through: {error}")
                rewritten = original
            if rewritten != original:
                logger.debug(f"Digits normalised: {original!r} → {rewritten!r}")
                frame.text = rewritten

        await self.push_frame(frame, direction)

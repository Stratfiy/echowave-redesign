"""Hold the caller's first turn until the agent has its instructions.

A static greeting is now spoken the moment the line is up, before the start
node has built its system prompt and tool table (see
``PipecatEngine.queue_static_opening_early``). That ordering opens a window
that did not exist before: a caller could finish a sentence after the greeting
and before ``set_node`` had finished, and the model would answer it with no
system prompt and no tools -- the agent's first reply written by nobody.

The window is narrow (the caller is muted until the greeting finishes playing,
and the start node usually finishes well inside that), but narrow is not
closed. This processor sits directly in front of the LLM and holds a context
frame until the engine says the context is ready. Everything else passes
through untouched, and the hold has a ceiling: a start node that never
finishes must not turn into a caller who is never answered.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: Longest a caller's turn is held. Past this the turn goes through with
#: whatever context exists, which is what happened every time before this gate.
DEFAULT_MAX_HOLD_SECS = 8.0


class LLMContextReadyGate(FrameProcessor):
    """Pass every frame, except hold an ``LLMContextFrame`` until ``ready``."""

    def __init__(
        self,
        ready: asyncio.Event,
        *,
        max_hold_secs: float = DEFAULT_MAX_HOLD_SECS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._ready = ready
        self._max_hold_secs = max_hold_secs

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, LLMContextFrame)
            and not self._ready.is_set()
        ):
            logger.info(
                "Holding the caller's turn until the agent's instructions are set"
            )
            try:
                await asyncio.wait_for(self._ready.wait(), self._max_hold_secs)
            except TimeoutError:
                logger.warning(
                    f"The start node was not ready after {self._max_hold_secs}s; "
                    "answering the caller with the context as it stands"
                )
        await self.push_frame(frame, direction)


__all__ = ["DEFAULT_MAX_HOLD_SECS", "LLMContextReadyGate"]

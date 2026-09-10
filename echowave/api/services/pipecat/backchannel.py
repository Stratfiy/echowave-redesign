"""A small sound while the agent thinks.

A person on the phone hears silence after their question as "did the line
drop?" — and on a slow model, a knowledge-base lookup or a tool call, the
silence is long enough for exactly that. A human agent fills it with an
"hmm" or a "one moment"; this does the same. The filler is spoken once per
caller turn, only when the first word of the real reply has not arrived by
``delay_secs``, and never lands in the LLM context — it is a noise, not a
line the model said.

Placed after the LLM so it sees both ends of the wait: the caller's turn
ending upstream, and the first token or spoken frame coming out of the
model. Realtime speech-to-speech pipelines carry their own backchannel and
do not use this.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DEFAULT_DELAY_SECS = 1.2
MIN_DELAY_SECS = 0.5
MAX_DELAY_SECS = 5.0
DEFAULT_PHRASES: tuple[str, ...] = ("Hmm.", "Okay.", "One moment.")


def backchannel_settings(
    run_configs: Mapping[str, Any] | None,
) -> tuple[float, list[str]] | None:
    """The (delay, phrases) for this agent, or None when the filler is off.

    Off unless switched on: a filler in the wrong language, on an agent whose
    operator never chose one, is worse than the silence it replaces.
    """
    if not run_configs:
        return None
    block = run_configs.get("backchannel_configuration")
    if not isinstance(block, Mapping) or not block.get("enabled"):
        return None
    try:
        delay = float(block.get("delay_secs") or DEFAULT_DELAY_SECS)
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY_SECS
    delay = min(max(delay, MIN_DELAY_SECS), MAX_DELAY_SECS)
    raw = block.get("phrases")
    phrases = (
        [p.strip() for p in raw if isinstance(p, str) and p.strip()]
        if isinstance(raw, Sequence) and not isinstance(raw, str)
        else []
    )
    return delay, phrases or list(DEFAULT_PHRASES)


class Backchannel(FrameProcessor):
    """Speak a filler when the reply to a caller's turn is slow to start."""

    def __init__(
        self,
        *,
        delay_secs: float = DEFAULT_DELAY_SECS,
        phrases: Sequence[str] = DEFAULT_PHRASES,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._delay = delay_secs
        self._phrases = list(phrases) or list(DEFAULT_PHRASES)
        self._next = 0
        self._timer: asyncio.Task | None = None
        #: How many fillers this call spoke. Diagnostic: a call with many is a
        #: slow model, not a chatty caller.
        self.spoken = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._arm()
        elif isinstance(frame, UserStartedSpeakingFrame):
            # The caller is still going; whatever we were waiting for is not
            # the reply to a finished turn any more.
            self._disarm()
        elif isinstance(frame, (LLMTextFrame, TTSSpeakFrame)):
            # The reply (or a scripted line) has started; no filler needed.
            self._disarm()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._disarm()

        await self.push_frame(frame, direction)

    def _arm(self) -> None:
        self._disarm()
        self._timer = asyncio.create_task(self._wait_then_speak())

    def _disarm(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _wait_then_speak(self) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return
        phrase = self._phrases[self._next % len(self._phrases)]
        self._next += 1
        self.spoken += 1
        logger.debug(f"Reply is {self._delay}s late; backchannel: {phrase!r}")
        # Not appended to the context: the model did not say this, and a
        # transcript full of "hmm" teaches it to say "hmm".
        await self.push_frame(
            TTSSpeakFrame(phrase, append_to_context=False), FrameDirection.DOWNSTREAM
        )

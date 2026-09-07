"""Turn keypresses into something the agent hears.

The rules for what an entry *is* live in ``dtmf_rules``; this is the part that
needs a pipeline. It watches ``InputDTMFFrame``, which every telephony
serializer already produces and nothing has ever read, and hands the completed
entry to the LLM as a user turn.

**The pause is a timer, so it lives here.** A caller who does not end with
``#`` has only stopped typing to say they are finished, and noticing that means
waiting — which needs a clock, a task, and somewhere to cancel it when the call
ends. Everything except the waiting is in ``dtmf_rules`` and tested without any
of this.

The entry is pushed as an ``LLMMessagesAppendFrame`` with ``run_llm=True``
rather than as a ``TranscriptionFrame``. A transcription is aggregated into the
caller's turn and only flushed when they stop *speaking* — so a caller who
typed and said nothing would have their digits sit in the aggregator until they
happened to talk, which on an IVR-style prompt is never.

Keypresses do not interrupt the agent. A caller pressing while it talks is
usually answering the question being asked, and cutting the sentence off is a
larger behaviour change than this needs to make; the entry lands as soon as
the current utterance is done either way.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from api.services.pipecat.dtmf_rules import (
    DEFAULT_QUIET_SECONDS,
    DigitBuffer,
    describe,
)
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputDTMFFrame,
    LLMMessagesAppendFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class DtmfCollector(FrameProcessor):
    """Collect keypresses and give the agent the finished entry.

    Args:
        quiet_seconds: Silence after the last key that ends an entry, for a
            caller who does not finish with ``#``.
    """

    def __init__(self, *, quiet_seconds: float = DEFAULT_QUIET_SECONDS, **kwargs):
        super().__init__(**kwargs)
        self._buffer = DigitBuffer()
        self._quiet_seconds = quiet_seconds
        self._timer: asyncio.Task | None = None

        #: Every completed entry, in order. Read at teardown — "did this caller
        #: use the keypad, and for what" is not answerable from the transcript,
        #: where a typed entry and a spoken one look the same.
        self.entries: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputDTMFFrame):
            await self._press(frame)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            # Whatever was half-typed goes now or never, and the timer must not
            # outlive the call.
            self._cancel_timer()
            await self._emit(self._buffer.flush())

        # Always forwarded. This processor observes; nothing downstream should
        # lose a frame because the keypad was in use.
        await self.push_frame(frame, direction)

    async def _press(self, frame: InputDTMFFrame) -> None:
        button = getattr(frame, "button", None)
        digit = getattr(button, "value", button)

        entry = self._buffer.press(digit)
        if entry is not None:
            self._cancel_timer()
            await self._emit(entry)
            return

        # Still typing, as far as anyone can tell. Restart the clock from this
        # key rather than the first one: the pause that matters is the one
        # after the caller stops, not the time they took in total.
        self._restart_timer()

    def _restart_timer(self) -> None:
        self._cancel_timer()
        self._timer = asyncio.create_task(self._wait_then_emit())

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _wait_then_emit(self) -> None:
        try:
            await asyncio.sleep(self._quiet_seconds)
        except asyncio.CancelledError:
            return
        await self._emit(self._buffer.flush())

    async def _emit(self, entry: str | None) -> None:
        if not entry:
            return

        self.entries.append(entry)
        logger.info(f"Caller entered {len(entry)} keys on the keypad")
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": describe(entry)}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )

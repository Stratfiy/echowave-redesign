"""Read the caller's words for escalation before the model does.

Sits after STT and before the context aggregator, like the end-call phrase
watcher. Each final transcription goes to the call's ``EscalationRuntime``
first: a transfer mutes the pipeline before the frame moves on, so "let me
talk to a person" does not also start a model turn; a repair note is pushed
ahead of the transcription, so the model's very next reply is the repair.

Only built when ``escalation_v2`` is on for the workspace (see
``services/escalation/runtime.py``); a call without it never sees this.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class EscalationWatcher(FrameProcessor):
    def __init__(self, *, runtime: Any, **kwargs):
        super().__init__(**kwargs)
        self._runtime = runtime

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and (frame.text or "").strip():
            try:
                for ahead in self._runtime.on_user_text(frame.text):
                    await self.push_frame(ahead, direction)
            except Exception as exc:  # noqa: BLE001 - never drop the caller's words
                logger.warning("Escalation watcher failed on a turn: {}", exc)

        await self.push_frame(frame, direction)


__all__ = ["EscalationWatcher"]

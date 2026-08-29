"""Hold the agent's next words briefly after the caller cuts in.

Interrupting an agent and having it answer the instant you draw breath is the
complaint this exists for: the caller says "wait —", the agent stops, and then
starts again before they have finished the thought. A short pause after an
interruption gives them the floor.

It usually costs nothing. By the time the agent speaks again the caller has
finished, the turn has been detected, the model has answered and speech has
been synthesised, which together are normally longer than any sensible pause.
The setting bites only where the interruption was brief, which is exactly the
case it is meant for.
"""

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, SystemFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InterruptionBackoff(FrameProcessor):
    """Delay the frames that make the agent speak, for a moment after a cut-in.

    Sits between the language model and speech synthesis, so what it holds is
    the reply on its way to being spoken.

    Two rules keep this safe on a live call:

    **System frames are never held.** An interruption, a cancel or an end has
    to take effect while the pause is running, not after it. They are also why
    this buffers rather than sleeping: a ``sleep`` in ``process_frame`` stalls
    the processor's whole queue, so the second interruption would be stuck
    behind the pause caused by the first.

    **Everything else is held in arrival order and released in arrival order.**
    Speech synthesis reads a response as a sequence bounded by its start and
    end frames, so a buffer that let one class of frame overtake another would
    produce a reply assembled in the wrong order rather than a delayed one.
    """

    def __init__(self, seconds: float, **kwargs):
        """Initialize the back-off.

        Args:
            seconds: How long after an interruption to hold the agent's reply.
            **kwargs: Additional arguments passed to ``FrameProcessor``.
        """
        super().__init__(**kwargs)
        self._seconds = seconds
        self._held: list[tuple[Frame, FrameDirection]] = []
        self._closed_until: float | None = None
        self._release_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Pass, hold, or release, depending on how recently the caller cut in.

        Args:
            frame: The frame to process.
            direction: The direction the frame is travelling.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # Whatever was held is speech the caller has already talked over.
            # Releasing it after the pause would answer a question they
            # interrupted to change.
            self._held.clear()
            self._closed_until = time.monotonic() + self._seconds
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, SystemFrame) or not self._is_closed():
            await self.push_frame(frame, direction)
            return

        self._held.append((frame, direction))
        if self._release_task is None:
            self._release_task = self.create_task(
                self._release_when_open(), f"{self}::_release_when_open"
            )

    async def cleanup(self):
        """Release nothing and stop waiting; the call is over."""
        await self._cancel_release()
        self._held.clear()
        await super().cleanup()

    def _is_closed(self) -> bool:
        return self._closed_until is not None and time.monotonic() < self._closed_until

    async def _cancel_release(self):
        if self._release_task is not None:
            await self.cancel_task(self._release_task)
            self._release_task = None

    async def _release_when_open(self):
        """Wait out the remainder of the pause, then let the reply through."""
        remaining = (self._closed_until or 0) - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

        self._closed_until = None
        self._release_task = None

        held, self._held = self._held, []
        if held:
            logger.debug(
                "{}: releasing {} held frame(s) after the interruption pause",
                self,
                len(held),
            )
        for frame, direction in held:
            await self.push_frame(frame, direction)

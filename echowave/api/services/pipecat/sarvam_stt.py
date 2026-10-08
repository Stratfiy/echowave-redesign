"""Sarvam's transcriber, connected beside the call rather than in front of it.

Pipecat starts a pipeline by passing one ``StartFrame`` down it, and every
processor handles that frame before the next one sees it. Sarvam's STT opens its
websocket inside ``start()``, so everything after it -- the context aggregator,
the LLM, the voice -- waits for that handshake before it has started at all. On
run 23 on staging that was 3.06 seconds (16.467 RNNoise up, 19.527 "Connected to
Sarvam successfully"), and the voice's own handshake only began after it. The
greeting cannot be spoken by a voice that has not started, so the caller heard
the transcriber's handshake as silence.

Nothing about the transcriber needs to be connected for the agent to speak
first. So ``start()`` here returns at once and the handshake runs as a task of
the service. The caller's audio that arrives before the socket is up is held,
in order, and sent the moment it is -- never dropped. A caller who says
"hello?" over the greeting is still heard, a moment later, exactly as before.

The class name matters to billing, as it does for the voice: usage keys carry
the processor's class name, and ``provider_from_processor`` maps
``decibylsarvam`` to ``sarvam``.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    StartFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.sarvam.stt import SarvamSTTService

#: How much caller audio to hold while the socket opens. A handshake that takes
#: longer than this has failed in every way that matters; past it the oldest
#: audio goes first, with a log line, so a stuck connect cannot grow memory for
#: the length of the call.
MAX_HELD_SECONDS = 15.0


class DecibylSarvamSTTService(SarvamSTTService):
    """``SarvamSTTService`` whose websocket handshake does not hold up the call."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connect_task: asyncio.Task | None = None
        self._held_audio: deque[bytes] = deque()
        self._held_bytes = 0
        self._held_trimmed = False
        # The caller stopped speaking while the socket was still opening. The
        # flush that tells Sarvam to finish the utterance had nowhere to go, so
        # it is sent after the held audio instead.
        self._flush_after_held = False

    @property
    def connecting(self) -> bool:
        """True while the background handshake is still running."""
        return self._connect_task is not None and not self._connect_task.done()

    async def start(self, frame: StartFrame):
        """Start the service and open the websocket in the background.

        ``SarvamSTTService.start`` is the base ``start`` followed by an awaited
        ``_connect``. The base is called directly so the frame moves on now; the
        connect is the same method, run as a task the service owns, so it is
        cancelled with the service like every other task it makes.
        """
        await super(SarvamSTTService, self).start(frame)
        self._connect_task = self.create_task(
            self._connect(), name="background-connect"
        )

    async def stop(self, frame: EndFrame):
        await self._abandon_background_connect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        await self._abandon_background_connect()
        await super().cancel(frame)

    async def _disconnect(self):
        # A settings change reconnects with _disconnect then _connect. If the
        # first connect were still running it would finish afterwards and
        # leave two sockets open, the first one orphaned. One at a time.
        await self._abandon_background_connect()
        await super()._disconnect()

    async def _abandon_background_connect(self) -> None:
        task = self._connect_task
        self._connect_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        await self.cancel_task(task)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if (
            self.connecting
            and not self._settings.vad_signals
            and isinstance(frame, VADUserStoppedSpeakingFrame)
        ):
            self._flush_after_held = True
        await super().process_frame(frame, direction)

    def _hold(self, audio: bytes) -> None:
        self._held_audio.append(audio)
        self._held_bytes += len(audio)
        limit = int(MAX_HELD_SECONDS * (self.sample_rate or 16000) * 2)
        while self._held_bytes > limit and len(self._held_audio) > 1:
            self._held_bytes -= len(self._held_audio.popleft())
            if not self._held_trimmed:
                self._held_trimmed = True
                logger.warning(
                    f"{self}: the transcriber is still connecting after "
                    f"{MAX_HELD_SECONDS:.0f}s; dropping the oldest held caller audio"
                )

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        """Send the caller's audio, holding it while the socket opens."""
        if self.connecting:
            self._hold(audio)
            yield None
            return

        if self._held_audio:
            held = list(self._held_audio)
            self._held_audio.clear()
            self._held_bytes = 0
            logger.debug(f"{self}: sending {len(held)} chunk(s) held during connect")
            for chunk in held:
                async for frame in super().run_stt(chunk):
                    if frame is not None:
                        yield frame
            if self._flush_after_held and self._socket_client:
                await self._socket_client.flush()
            self._flush_after_held = False

        async for frame in super().run_stt(audio):
            yield frame


__all__ = ["MAX_HELD_SECONDS", "DecibylSarvamSTTService"]

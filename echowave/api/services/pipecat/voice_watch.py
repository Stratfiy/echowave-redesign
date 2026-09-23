"""End a call whose voice has stopped producing audio, and say why.

The mute-agent watchdog in ``event_handlers`` covers a provider that *reports*
a failure. This covers the one that does not. On 23 Sept 2026 ElevenLabs took
our text, sent back no audio and no error, and the agent sat "still speaking"
its greeting until the caller gave up -- it cannot answer while it thinks it
is talking, so it never replied either. Nothing was logged; the run recorded
cleanly as a caller hang-up. The TTS characters were even billed, which is one
of the signals the mute-agent watchdog reads as "the agent spoke".

The signal here is the voice's own: a ``TTSStartedFrame`` says synthesis began,
and a working voice follows it with audio within a second or two. If none
arrives in ``SILENT_AFTER_SECONDS``, the voice is not working. The processor
then raises a fatal pipeline error, so the existing error path records the
cause on the run, counts it against a campaign's breaker and ends the call,
and it notes the failure for the staff screen (``services/voice_failures``).

It sits directly after the voice, so it sees what the voice emits and nothing
else. Every frame passes through untouched.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from api.services import voice_failures
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: How long synthesis may run with no audio before the voice counts as dead.
#:
#: ElevenLabs flash answers in well under a second, and the slowest voices we
#: carry in two or three. Ten seconds is long past any of them, and still short
#: of the minute a caller spent in silence before hanging up.
SILENT_AFTER_SECONDS = 10.0


class VoiceSilenceWatch(FrameProcessor):
    """Watch the voice's output for synthesis that never produces sound."""

    def __init__(
        self,
        *,
        provider: str,
        model: str | None,
        run_id: int | None,
        seconds: float = SILENT_AFTER_SECONDS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._provider = provider
        self._model = model
        self._run_id = run_id
        self._seconds = seconds
        self._timer = None
        self._fired = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            # Only the first start of a stretch arms it: a reply spoken in
            # several sentences starts synthesis more than once, and the clock
            # is about the first sound, not the last sentence. A start is only
            # ever pushed for real text, so a reply that is just a tool call
            # never arms it.
            if self._timer is None and not self._fired:
                self._arm()
        elif isinstance(frame, TTSAudioRawFrame):
            # Sound: the voice works. The only thing that clears the clock.
            await self._disarm()
        elif isinstance(frame, InterruptionFrame):
            # The caller talking over a silent agent is exactly what happened
            # on 23 Sept -- they said "hello?" into the silence. Cancelling here
            # would have missed that call, so a cut-in restarts the clock
            # instead: the next reply gets a full wait to make a sound.
            if self._timer is not None:
                await self._disarm()
                self._arm()
        elif isinstance(frame, EndFrame | CancelFrame):
            await self._disarm()
        # A TTSStoppedFrame is deliberately not here. Speech synthesis pushes
        # one after a few idle seconds whether or not any audio came, so a
        # stop with no sound before it is the failure itself, not its end.

        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._disarm()
        await super().cleanup()

    def _arm(self):
        self._timer = self.create_task(self._expire(), f"{self}::voice_silence")

    async def _disarm(self):
        if self._timer is not None:
            timer, self._timer = self._timer, None
            await self.cancel_task(timer)

    async def _expire(self):
        await asyncio.sleep(self._seconds)
        self._timer = None
        self._fired = True
        reason = (
            f"The voice ({self._provider}"
            f"{'/' + self._model if self._model else ''}) produced no audio "
            f"{self._seconds:.0f}s after it was given text. The provider is "
            "not answering: check its account, and the key's limit and "
            "permissions."
        )
        logger.error("Workflow run {}: {}", self._run_id, reason)
        await voice_failures.record(
            provider=self._provider, model=self._model, run_id=self._run_id
        )
        await self.push_error(error_msg=reason, fatal=True)

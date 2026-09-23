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
arrives in ``SILENT_AFTER_SECONDS``, the voice is not working.

**With a backup voice configured**, the call moves to it. The switcher's own
failover only fires on an error the voice *pushes*, and a silent voice pushes
none, so this does the switch itself -- and then says again whatever the dead
voice swallowed, so the caller hears the greeting rather than the agent's
second line. Each voice gets its own full wait.

**With none left**, it raises a fatal pipeline error, so the existing error
path records the cause on the run, counts it against a campaign's breaker and
ends the call.

Either way the failure is noted for the staff screen
(``services/voice_failures``). It sits directly after the voice, so it sees
what the voice emits and nothing else; every frame passes through untouched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from api.services import voice_failures
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    ManuallySwitchServiceFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: How long synthesis may run with no audio before the voice counts as dead.
#:
#: ElevenLabs flash answers in well under a second, and the slowest voices we
#: carry in two or three. Ten seconds is long past any of them, and still short
#: of the minute a caller spent in silence before hanging up.
SILENT_AFTER_SECONDS = 10.0


@dataclass(frozen=True)
class Voice:
    """What to call a voice in a log line and on the staff screen."""

    provider: str
    model: str | None = None

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}" if self.model else self.provider


class VoiceSilenceWatch(FrameProcessor):
    """Watch the voice's output for synthesis that never produces sound."""

    def __init__(
        self,
        *,
        provider: str,
        model: str | None,
        run_id: int | None,
        seconds: float = SILENT_AFTER_SECONDS,
        switcher=None,
        backups: list[Voice] | None = None,
        **kwargs,
    ):
        """Initialize the watch.

        Args:
            provider: The primary voice's provider.
            model: The primary voice's model.
            run_id: The run, for the log line and the staff screen.
            seconds: How long a voice may stay silent after starting.
            switcher: The ``ServiceSwitcher`` the voice and its backups sit
                in, when the agent has backups. Its services are in the order
                ``[primary, *backups]``.
            backups: What to call each backup, in that order.
            **kwargs: Passed to ``FrameProcessor``.
        """
        super().__init__(**kwargs)
        self._voices = [Voice(provider, model), *(backups or [])]
        self._active = 0
        self._run_id = run_id
        self._seconds = seconds
        self._switcher = switcher
        self._timer = None
        self._fired = False
        # What the active voice has been asked to say since it last made a
        # sound: the words a failover has to say again.
        self._unspoken: list[str] = []
        if switcher is not None:
            for index, service in enumerate(switcher.strategy.services):
                self._listen(service, index)

    def _listen(self, service, index: int) -> None:
        async def on_tts_request(_service, _context_id, text):
            if index == self._active and text and text.strip():
                self._unspoken.append(text.strip())

        try:
            service.add_event_handler("on_tts_request", on_tts_request)
        except Exception:  # noqa: BLE001 - a voice without the event just isn't replayed
            logger.debug("{} has no on_tts_request event", service)

    @property
    def voice(self) -> Voice:
        """The voice currently speaking."""
        return self._voices[self._active]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Pass every frame on, starting or clearing the clock as it goes."""
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
            self._unspoken.clear()
            await self._disarm()
        elif isinstance(frame, InterruptionFrame):
            # The caller talking over a silent agent is exactly what happened
            # on 23 Sept -- they said "hello?" into the silence. Cancelling here
            # would have missed that call, so a cut-in restarts the clock
            # instead: the next reply gets a full wait to make a sound. What
            # was interrupted is not worth saying again.
            self._unspoken.clear()
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
        """Stop the clock; the call is over."""
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
        failed = self.voice
        reason = (
            f"The voice ({failed}) produced no audio {self._seconds:.0f}s after "
            "it was given text. The provider is not answering: check its "
            "account, and the key's limit and permissions."
        )
        await voice_failures.record(
            provider=failed.provider, model=failed.model, run_id=self._run_id
        )

        if self._switcher is not None and self._active + 1 < len(self._voices):
            await self._fail_over(reason)
            return

        self._fired = True
        logger.error("Workflow run {}: {}", self._run_id, reason)
        await self.push_error(error_msg=reason, fatal=True)

    async def _fail_over(self, reason: str) -> None:
        """Move to the next voice and say again what the dead one swallowed."""
        self._active += 1
        services = self._switcher.strategy.services
        unspoken = " ".join(self._unspoken)
        self._unspoken.clear()
        logger.warning(
            "Workflow run {}: {} Moving to the backup voice ({}).",
            self._run_id,
            reason,
            self.voice,
        )
        # Both into the switcher's own input, in this order: the switch is
        # handled first, so the words reach the backup and not the dead voice.
        await self._switcher.queue_frame(
            ManuallySwitchServiceFrame(service=services[self._active])
        )
        if unspoken:
            await self._switcher.queue_frame(TTSSpeakFrame(text=unspoken))
        # The backup gets its own full wait. If it is silent too, the next
        # expiry moves on again, or ends the call when none are left.
        self._arm()

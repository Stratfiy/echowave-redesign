"""The tap: what a listener receives, taken from a call without slowing it.

An observer, not a processor. A processor sits in the caller's path and
every frame waits for it; an observer is handed copies of the frames the
pipeline pushes, on its own queue, and the pipeline never waits for it.
This one does only cheap things on each frame -- a type check and a
``put_nowait`` into a bounded queue -- and when a queue is full the frame
is dropped and counted. A slow listener, a slow Redis or a stalled
publisher costs listeners some audio; it never costs the caller a
millisecond, and it never grows memory.

What it takes, matching the research brief's tap points:

- the caller's audio as the input transport hands it on (after any noise
  filter the transport runs, so a listener hears what the agent hears);
- the agent's audio on its way into the output transport;
- the caller's words, interim and final, from speech-to-text;
- the agent's words as the output transport releases them (word timing is
  applied there, so they line up with the audio);
- when the agent stops speaking, and when it is interrupted.

Audio and words are on separate queues, so a flood of audio can never push
a line of transcript out.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport

from api.services.live_takeover.frames import SupervisorAudioFrame

#: About two seconds of 20 ms frames from both sides.
AUDIO_QUEUE_SIZE = 200
#: Words are a few events a second; this is minutes of them.
EVENT_QUEUE_SIZE = 1000
#: Frame ids remembered to see each frame once (it is observed at every hop).
_SEEN = 512

# Queue items. Tuples, not objects: this runs for every frame of a call.
AUDIO = "audio"
CALLER = "caller"
AGENT_TEXT = "agent_text"
AGENT_DONE = "agent_done"
INTERRUPTED = "interrupted"


class LiveCallTap(BaseObserver):
    """Copies a call's audio and words into two bounded queues."""

    def __init__(
        self,
        *,
        audio_wanted: Callable[[], bool] = lambda: True,
        audio_queue_size: int = AUDIO_QUEUE_SIZE,
        event_queue_size: int = EVENT_QUEUE_SIZE,
        **kwargs,
    ):
        """Initialize the tap.

        Args:
            audio_wanted: Asked before audio is queued; False while nobody is
                listening to sound, so a call with no listeners copies none.
            audio_queue_size: Bound on queued audio frames.
            event_queue_size: Bound on queued transcript events.
            **kwargs: Passed to ``BaseObserver``.
        """
        super().__init__(**kwargs)
        self.audio: asyncio.Queue = asyncio.Queue(maxsize=audio_queue_size)
        self.events: asyncio.Queue = asyncio.Queue(maxsize=event_queue_size)
        self.dropped_audio = 0
        self.dropped_events = 0
        self._audio_wanted = audio_wanted
        self._seen_ids: set[int] = set()
        self._seen_order: deque[int] = deque()
        self.closed = False

    def _first_sighting(self, frame_id: int) -> bool:
        if frame_id in self._seen_ids:
            return False
        self._seen_ids.add(frame_id)
        self._seen_order.append(frame_id)
        if len(self._seen_order) > _SEEN:
            self._seen_ids.discard(self._seen_order.popleft())
        return True

    def _put_audio(self, item: tuple) -> None:
        try:
            self.audio.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped_audio += 1
            if self.dropped_audio == 1 or self.dropped_audio % 1000 == 0:
                logger.debug("Live tap dropped {} audio frames", self.dropped_audio)

    def _put_event(self, item: tuple) -> None:
        try:
            self.events.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped_events += 1
            if self.dropped_events == 1 or self.dropped_events % 100 == 0:
                logger.warning(
                    "Live tap dropped {} transcript events", self.dropped_events
                )

    async def on_push_frame(self, data: FramePushed):
        """Never awaits anything: see the module docstring."""
        if self.closed:
            return
        frame = data.frame

        if isinstance(frame, InputAudioRawFrame):
            if (
                data.direction == FrameDirection.DOWNSTREAM
                and isinstance(data.source, BaseInputTransport)
                and self._audio_wanted()
            ):
                self._put_audio(
                    (AUDIO, "c", frame.sample_rate, frame.num_channels, frame.audio)
                )
            return
        if isinstance(frame, OutputAudioRawFrame):
            if (
                isinstance(data.destination, BaseOutputTransport)
                and self._audio_wanted()
            ):
                # A supervisor who joined the call is their own side, so the
                # one speaking can leave themselves out of what they hear.
                side = "s" if isinstance(frame, SupervisorAudioFrame) else "a"
                self._put_audio(
                    (AUDIO, side, frame.sample_rate, frame.num_channels, frame.audio)
                )
            return

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            # A transcription broadcast both ways arrives twice under two ids;
            # the downstream copy is the one to keep. One pushed upstream only
            # (a speech-to-speech model's) has no sibling and is kept.
            if (
                data.direction != FrameDirection.DOWNSTREAM
                and frame.broadcast_sibling_id is not None
            ):
                return
            if not self._first_sighting(frame.id):
                return
            final = isinstance(frame, TranscriptionFrame)
            self._put_event((CALLER, frame.text or "", final))
            return
        if isinstance(frame, TTSTextFrame):
            if not isinstance(data.source, BaseOutputTransport):
                return
            if not self._first_sighting(frame.id):
                return
            self._put_event((AGENT_TEXT, frame.text or ""))
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            if self._first_sighting(frame.id):
                self._put_event((AGENT_DONE,))
            return
        if isinstance(frame, InterruptionFrame):
            if self._first_sighting(frame.id):
                self._put_event((INTERRUPTED,))
            return

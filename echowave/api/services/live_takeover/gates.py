"""The two processors that silence the agent while a supervisor has the call.

Added to a call's pipeline only when ``live_takeover`` is on for its
organisation; a call without the feature runs the frames it always ran.
Both read one ``GateState`` that the call's ``TakeoverController`` sets.
While nobody has joined, both pass every frame untouched.

**``LLMGate``** sits directly in front of the model. While the agent is
silenced it drops ``LLMContextFrame`` -- the frame that asks the model for a
reply -- so the agent does not *compose* answers nobody will hear. Everything
else passes: the caller's words still reach the context through the user
aggregator in front of it, so the agent knows what was said when it comes
back.

**``OutputGate``** sits directly in front of the output transport, after the
voice. It is the guarantee: whatever reaches it while the agent is silenced
-- a reply already being synthesised when the supervisor joined, a line the
engine speaks on its own (a node's transition speech, an idle prompt) -- is
dropped, audio and words alike, so neither the caller nor the listen panel
gets agent speech that was never meant to be heard. Three more duties:

- it is where the supervisor's voice enters the call (``inject``), as
  ``SupervisorAudioFrame``, which the transport plays but does not count as
  the bot speaking;
- it swallows interruptions while the agent is silenced, because the only
  thing playing is the supervisor and a caller saying "mm-hm" must not wipe
  the supervisor's queued audio (or, on Plivo, send ``clearAudio``). The one
  interruption the controller queues on purpose -- to cut the agent off at
  the moment the supervisor joins -- is let through by id;
- it drops a context frame pushed *upstream* past it (the assistant
  aggregator, after the transport, asks for a reply that way when a tool
  result comes back), for the same reason ``LLMGate`` drops one downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from api.services.live_takeover.frames import SupervisorAudioFrame
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    OutputAudioRawFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: Who has the call.
AI = "ai"
BARGE = "barge"
TAKEOVER = "takeover"
MODES = (AI, BARGE, TAKEOVER)

#: Interruption ids remembered as let through; a handful is plenty.
_PASS_LIMIT = 16


@dataclass
class GateState:
    """What the gates read. Set only by the controller."""

    mode: str = AI
    #: In a barge, the supervisor let the agent answer; cleared again the
    #: moment the supervisor speaks.
    ai_may_speak: bool = False
    dropped_audio: int = 0
    dropped_replies: int = 0
    swallowed_interruptions: int = 0
    _pass_ids: list[int] = field(default_factory=list)

    @property
    def supervised(self) -> bool:
        return self.mode != AI

    @property
    def ai_silenced(self) -> bool:
        return self.mode != AI and not self.ai_may_speak

    def let_through(self, frame: Frame) -> None:
        self._pass_ids.append(frame.id)
        del self._pass_ids[:-_PASS_LIMIT]

    def passes(self, frame: Frame) -> bool:
        return frame.id in self._pass_ids


class LLMGate(FrameProcessor):
    """In front of the model: no new reply is asked for while silenced."""

    def __init__(self, state: GateState, **kwargs):
        super().__init__(**kwargs)
        self._state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and self._state.ai_silenced:
            self._state.dropped_replies += 1
            logger.debug("Live take-over: the agent is paused; no reply asked for")
            return
        await self.push_frame(frame, direction)


class OutputGate(FrameProcessor):
    """In front of the output transport: nothing of the agent's is heard
    while silenced, and the supervisor's voice comes in here."""

    def __init__(self, state: GateState, **kwargs):
        super().__init__(**kwargs)
        self._state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        state = self._state
        if state.ai_silenced:
            if isinstance(frame, OutputAudioRawFrame) and not isinstance(
                frame, SupervisorAudioFrame
            ):
                state.dropped_audio += 1
                return
            if isinstance(frame, TTSTextFrame):
                return
            if isinstance(frame, LLMContextFrame):
                state.dropped_replies += 1
                return
        if (
            isinstance(frame, InterruptionFrame)
            and state.supervised
            and not state.ai_may_speak
            and not state.passes(frame)
        ):
            state.swallowed_interruptions += 1
            return
        await self.push_frame(frame, direction)

    async def inject(self, frame: SupervisorAudioFrame) -> bool:
        """Play a slice of the supervisor's voice to the caller."""
        if not self._state.supervised:
            return False
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
        return True

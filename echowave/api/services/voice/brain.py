"""Decibyl, spoken: the turn-taking between the voice pipeline and the brain.

Two pipecat processors share one ``TurnLedger`` per session:

* ``DecibylVoiceBrain`` sits where an LLM would. When the person's turn ends
  (the user aggregator's ``LLMContextFrame``) it records their line on the
  thread and asks Decibyl -- the same ``decibyl.answer`` the text path uses,
  so memory, tools and cards are one system -- streaming each new piece to
  the voice as it forms. A new turn or an interruption cancels the turn in
  flight: generation stops, and the voice's queue is cleared by the
  pipeline's own interruption handling (handoff 12: "cancel generation and
  audio queues on interruption").
* ``HeardTracker`` sits after the output transport, where a frame means the
  audio before it was sent. It collects the words actually spoken and, when
  the reply finishes or is cut off, writes *that* to the thread -- with the
  interruption marked -- so the next turn never assumes the person heard
  an answer they talked over (handoff 12, "track what was actually played").

Nothing here approves anything. A card Decibyl proposes mid-conversation is
on the thread and on the voice screen for a tap; a spoken "yes" is a turn of
conversation, not a confirmation (handoff 21: approvals stay a visible exact
preview).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

Sender = Callable[[dict], Awaitable[None]]
Answerer = Callable[
    ["TurnLedger", "Turn", Callable[[str], Awaitable[None]]], Awaitable[str]
]


@dataclass
class Turn:
    index: int
    text: str
    #: Monotonic seconds on the server's clock.
    started_at: float
    user_stopped_at: float | None = None
    first_text_at: float | None = None
    first_audio_at: float | None = None
    said: str = ""
    heard: list[str] = field(default_factory=list)
    tool_turn: bool = False
    interrupted: bool = False
    closed: bool = False

    def heard_text(self) -> str:
        return " ".join(part.strip() for part in self.heard if part.strip()).strip()

    def stages(self) -> dict[str, float | None]:
        """Server durations in ms, each on the monotonic clock alone."""

        def since(start: float | None, end: float | None) -> float | None:
            if start is None or end is None or end < start:
                return None
            return (end - start) * 1000

        return {
            "stt_final": since(self.user_stopped_at, self.started_at),
            "brain_first_text": since(self.started_at, self.first_text_at),
            "tts_first_audio": since(self.started_at, self.first_audio_at),
        }


@dataclass
class TurnLedger:
    session_id: int
    organization_id: int
    user_id: int
    thread_id: str | None
    language: str | None = None
    stt_provider: str | None = None
    tts_provider: str | None = None
    send: Sender | None = None
    current: Turn | None = None
    next_index: int = 0
    last_user_stopped_at: float | None = None
    #: Where a finished turn is written. None is Decibyl's thread
    #: (``record_heard``); a huddle writes its own transcript instead
    #: (services/huddle/voice.py).
    on_heard: Callable[["TurnLedger", "Turn"], Awaitable[None]] | None = None
    #: Background writes (thread rows, latency), awaited at shutdown.
    pending: set[asyncio.Task] = field(default_factory=set)

    def new_turn(self, text: str) -> Turn:
        turn = Turn(
            index=self.next_index,
            text=text,
            started_at=time.monotonic(),
            user_stopped_at=self.last_user_stopped_at,
        )
        self.next_index += 1
        self.current = turn
        return turn

    async def tell(self, message: dict) -> None:
        """A message to the voice screen. Never raises."""
        if self.send is None:
            return
        try:
            await self.send(message)
        except Exception as exc:  # noqa: BLE001 - a closed socket is not an error here
            logger.debug("Voice screen message not sent: {}", exc)

    def later(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def drain(self) -> None:
        if self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)


def _last_user_text(frame: LLMContextFrame) -> str:
    """The words of the person's turn that just ended."""
    try:
        messages = frame.context.get_messages()
    except Exception:  # noqa: BLE001
        return ""
    for message in reversed(messages or []):
        role = message.get("role") if isinstance(message, dict) else None
        if role != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return " ".join(parts).strip()
    return ""


async def record_line(ledger: TurnLedger, text: str) -> None:
    """The person's spoken line, on the thread like a typed one."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline, decibyl

    await agent_timeline.record(
        organization_id=ledger.organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=text[:500],
        payload={
            "body": text,
            "author_id": ledger.user_id,
            "asked": [],
            "subjects": [],
            "attachments": [],
            "preset": None,
            "to": decibyl.NAME,
            "via": "voice",
            "voice_session_id": ledger.session_id,
        },
        in_channel=False,
        thread_id=ledger.thread_id,
    )


async def answer_with_decibyl(
    ledger: TurnLedger, turn: Turn, on_words: Callable[[str], Awaitable[None]]
) -> str:
    """One spoken turn through Decibyl's own brain."""
    from api.services.workflow import decibyl

    await record_line(ledger, turn.text)
    spoken = decibyl.VoiceTurn(on_words=on_words)
    with decibyl.voice_turn(spoken):
        body = await decibyl.answer(
            ledger.organization_id,
            turn.text,
            author_id=ledger.user_id,
            thread_id=ledger.thread_id,
        )
    turn.tool_turn = spoken.tool_turn
    return body


async def record_heard(ledger: TurnLedger, turn: Turn) -> None:
    """Decibyl's reply as the person heard it. Never raises."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline, decibyl

    heard = turn.heard_text()
    if turn.interrupted:
        body = f"{heard} … (interrupted)" if heard else "(interrupted before speaking)"
    else:
        body = heard or turn.said.strip()
    if not body:
        return
    try:
        await agent_timeline.record(
            organization_id=ledger.organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=body[:500],
            payload={
                "body": body,
                "from": decibyl.NAME,
                "via": "voice",
                "voice_session_id": ledger.session_id,
                "interrupted": turn.interrupted,
            },
            in_channel=False,
            thread_id=ledger.thread_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Voice reply not recorded on the thread: {}", exc)


async def close_turn(ledger: TurnLedger, turn: Turn, *, interrupted: bool) -> None:
    """The reply finished or was cut off: record it once, measure it once."""
    from api.services.voice import latency

    if turn.closed:
        return
    turn.closed = True
    turn.interrupted = interrupted
    if ledger.current is turn:
        ledger.current = None
    await ledger.tell(
        {
            "type": "voice-turn-closed",
            "payload": {
                "turn_index": turn.index,
                "interrupted": interrupted,
                "tool_turn": turn.tool_turn,
            },
        }
    )
    ledger.later((ledger.on_heard or record_heard)(ledger, turn))
    ledger.later(
        latency.record_server(
            session_id=ledger.session_id,
            turn_index=turn.index,
            stages=turn.stages(),
            tool_turn=turn.tool_turn,
            stt_provider=ledger.stt_provider,
            tts_provider=ledger.tts_provider,
        )
    )


class DecibylVoiceBrain(FrameProcessor):
    """Where the LLM would sit: turns in, Decibyl's words out."""

    def __init__(self, ledger: TurnLedger, answer: Answerer = answer_with_decibyl):
        super().__init__(name="DecibylVoiceBrain")
        self._ledger = ledger
        self._answer = answer
        self._task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._ledger.last_user_stopped_at = time.monotonic()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            await self._cancel()
            await self.push_frame(frame, direction)
            return
        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            text = _last_user_text(frame)
            if text:
                await self._start(text)
            return
        await self.push_frame(frame, direction)

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _start(self, text: str) -> None:
        await self._cancel()
        previous = self._ledger.current
        if previous is not None and not previous.closed:
            # Talked over before the tracker saw an interruption: still cut.
            await close_turn(self._ledger, previous, interrupted=True)
        turn = self._ledger.new_turn(text)
        await self._ledger.tell(
            {
                "type": "voice-phase",
                "payload": {"phase": "processing", "turn_index": turn.index},
            }
        )
        self._task = asyncio.ensure_future(self._run(turn))

    async def _run(self, turn: Turn) -> None:
        async def on_words(piece: str) -> None:
            if turn.first_text_at is None:
                turn.first_text_at = time.monotonic()
            turn.said += piece
            await self.push_frame(LLMTextFrame(piece))

        await self.push_frame(LLMFullResponseStartFrame())
        try:
            await self._answer(self._ledger, turn, on_words)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - say something rather than nothing
            logger.error("Decibyl voice turn failed: {}", exc)
            if not turn.said:
                await on_words(
                    "Sorry, I could not answer that just now. Please try again."
                )
        await self.push_frame(LLMFullResponseEndFrame())

    async def cleanup(self):
        await self._cancel()
        await super().cleanup()


class HeardTracker(FrameProcessor):
    """After the output transport: what was actually played."""

    def __init__(self, ledger: TurnLedger):
        super().__init__(name="HeardTracker")
        self._ledger = ledger

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        turn = self._ledger.current
        if turn is not None and not turn.closed:
            if (
                isinstance(frame, BotStartedSpeakingFrame)
                and turn.first_audio_at is None
            ):
                turn.first_audio_at = time.monotonic()
            elif (
                isinstance(frame, TTSTextFrame)
                and direction == FrameDirection.DOWNSTREAM
            ):
                turn.heard.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                await close_turn(self._ledger, turn, interrupted=False)
            elif isinstance(frame, InterruptionFrame):
                await close_turn(self._ledger, turn, interrupted=True)
        await self.push_frame(frame, direction)

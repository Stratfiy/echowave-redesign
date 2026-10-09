"""A take-over, on the worker running the call.

``prepare`` is the first half of the pipeline hook: before the pipeline is
built it returns the controller whose two gates go into it -- or None, and
nothing goes in, unless ``live_takeover`` (and ``live_supervision``) are on
for the call's organisation. ``attach`` is the second half, once the task
and the live-supervision session exist; ``close`` ends it with the call.

What it does, all of it off the caller's audio path:

- **Commands** arrive on ``live:call:<run>:tko`` from the API: join (barge or
  take-over), switch, let the agent answer, hand back, and the talking
  socket's presence ping.
- **The supervisor's voice** arrives on ``live:call:<run>:mic`` and is played
  into the call by the ``OutputGate``. Its loudness is read on the way past:
  in a barge where the agent was let answer, the supervisor speaking cuts
  the agent off again; and every stretch of the supervisor speaking is
  recorded as one labelled span in the call's transcript.
- **The watchdog**: a supervisor who has the call and stops being heard from
  (no ping, no audio) for ``recovery_seconds`` has dropped off. The agent
  takes the call back with a short line, so the caller is never left in
  silence, and that is recorded and audited like a hand-back.
- **The record**: every join, switch, hand-back and recovery goes into the
  call's own record (``realtime_feedback_events``, which the transcript is
  drawn from) and to everybody listening, in order with the words.

Every Redis or bridge failure is logged and survived; the one thing a
failure here must never do is leave the agent silenced with nobody on the
line, which is why the watchdog runs on its own clock.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants
from api.services import features
from api.services.live_supervision import channels as live_channels
from api.services.live_takeover import bridge as bridges
from api.services.live_takeover import channels
from api.services.live_takeover.frames import SupervisorAudioFrame
from api.services.live_takeover.gates import (
    AI,
    BARGE,
    MODES,
    TAKEOVER,
    GateState,
    LLMGate,
    OutputGate,
)
from api.services.workflow import audit_log
from pipecat.frames.frames import InterruptionFrame, LLMMessagesAppendFrame

FEATURE = "live_takeover"
NEEDS = "live_supervision"
#: Run modes that are not a call.
NOT_A_CALL = frozenset({"textchat", "CHAT"})

#: The call's own record: a join, switch, hand-back or recovery...
EVENT_TAKEOVER = "rtf-supervisor-takeover"
#: ...and one stretch of the supervisor speaking to the caller.
EVENT_SPEECH = "rtf-supervisor-speech"

#: Root-mean-square level (of 32768) above which a slice of the
#: supervisor's microphone counts as speech. About -36 dBFS: well above a
#: quiet room through a browser's noise suppression, well below a voice.
SPEECH_RMS = 500.0
#: Quiet for this long ends a stretch of the supervisor speaking.
SPEECH_HANGOVER_SECONDS = 1.0
#: How often the watchdog looks.
WATCH_SECONDS = 0.25

# Actions, as recorded.
JOINED = "joined"
SWITCHED = "switched"
AGENT_ANSWERING = "agent_answering"
AGENT_PAUSED = "agent_paused"
HANDED_BACK = "handed_back"
RECOVERED = "recovered"
FAILED = "failed"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def rms(pcm: bytes) -> float:
    """The level of a slice of 16-bit little-endian mono PCM."""
    if len(pcm) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _who(name: str | None) -> str:
    return (name or "").strip() or "A member of the team"


def handback_message(by: str | None, mode: str, seconds: int) -> dict[str, Any]:
    joined = "took over" if mode == TAKEOVER else "joined"
    return {
        "role": "system",
        "content": (
            f"[supervisor-handback] {_who(by)} from the team {joined} this call "
            f"for about {seconds} seconds and spoke with the caller directly. "
            "Their words were not transcribed; anything the caller said "
            "meanwhile is above. The call is yours again: pick up from where "
            "the conversation now is, in one short sentence, without repeating "
            "what was settled and without saying you were paused."
        ),
    }


def recovery_message(by: str | None) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            f"[supervisor-recovery] {_who(by)} from the team, who was speaking "
            "with the caller, has been cut off. The call is yours again. In one "
            "short sentence, in the language of the conversation, apologise "
            "for the interruption and offer to carry on helping; then continue "
            "from where the conversation is."
        ),
    }


def answer_message(by: str | None) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            f"[supervisor-answer] {_who(by)} from the team is on this call with "
            "you and has asked you to answer the caller now. Reply to the "
            "caller's last words. Their own words were not transcribed."
        ),
    }


class TakeoverController:
    def __init__(
        self,
        *,
        run_id: int,
        organization_id: int,
        workflow_run: Any = None,
        recovery_seconds: float | None = None,
        bridge_name: str | None = None,
        redis: Any = None,
    ):
        self.run_id = run_id
        self.organization_id = organization_id
        self.workflow_run = workflow_run
        self.recovery_seconds = float(
            recovery_seconds
            if recovery_seconds is not None
            else constants.LIVE_TAKEOVER_RECOVERY_SECONDS
        )
        self.bridge_name = bridge_name or bridges.configured()
        self.state = GateState()
        self.llm_gate = LLMGate(self.state)
        self.output_gate = OutputGate(self.state)
        self.holder: dict[str, Any] | None = None
        self.bridge: bridges.SupervisorBridge | None = None
        self.speech: dict[str, Any] | None = None
        self.played_frames = 0
        self._redis = redis
        self._task: Any = None
        self._logs: Any = None
        self._live: Any = None
        self._pubsub = None
        self._loops: list[asyncio.Task] = []
        self._last_seen = 0.0
        self._lock = asyncio.Lock()
        self._errors = 0
        self.closed = False

    # -- plumbing -----------------------------------------------------------

    @property
    def holds_the_call(self) -> bool:
        """A supervisor has the call: the agent's own timers (the idle
        prompt that would hang up on a quiet caller) stand down."""
        return self.state.supervised

    def redis(self):
        return self._redis if self._redis is not None else live_channels.redis()

    def _warn(self, what: str, exc: BaseException) -> None:
        self._errors += 1
        if self._errors == 1 or self._errors % 100 == 0:
            logger.warning(
                "Live take-over for run {}: {} failed ({} so far): {}",
                self.run_id,
                what,
                self._errors,
                exc,
            )

    # -- lifecycle ----------------------------------------------------------

    async def attach(self, task: Any, *, logs_buffer: Any = None, live: Any = None):
        """Start taking commands. Never raises."""
        self._task = task
        self._logs = logs_buffer
        self._live = live
        try:
            self._pubsub = self.redis().pubsub()
            await self._pubsub.subscribe(
                channels.command_channel(self.run_id), channels.mic_channel(self.run_id)
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            self._warn("subscribe", exc)
            self._pubsub = None
        self._loops = [
            asyncio.create_task(self._listen_loop(), name=f"takeover-{self.run_id}"),
            asyncio.create_task(self._watch_loop(), name=f"takeover-w-{self.run_id}"),
        ]

    async def close(self) -> None:
        """The call ended. Safe to call twice."""
        if self.closed:
            return
        self.closed = True
        for loop in self._loops:
            loop.cancel()
        for loop in self._loops:
            try:
                await loop
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._loops = []
        if self.state.supervised:
            await self._end_speech()
            await self._leave_bridge()
            self.state.mode = AI
            self.holder = None
        try:
            if self._pubsub is not None:
                await self._pubsub.unsubscribe()
                await self._pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            self._warn("unsubscribe", exc)
        try:
            await self.redis().delete(channels.state_key(self.run_id))
        except Exception as exc:  # noqa: BLE001
            self._warn("clear state", exc)

    # -- loops --------------------------------------------------------------

    async def _listen_loop(self) -> None:
        if self._pubsub is None:
            return
        mic = channels.mic_channel(self.run_id).encode()
        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._warn("listen", exc)
                await asyncio.sleep(0.5)
                continue
            if not message or message.get("type") != "message":
                continue
            try:
                if message.get("channel") == mic:
                    await self.on_mic(message["data"])
                else:
                    await self.on_command(json.loads(message["data"]))
            except Exception as exc:  # noqa: BLE001
                self._warn("command", exc)

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCH_SECONDS)
            try:
                await self.watch()
            except Exception as exc:  # noqa: BLE001
                self._warn("watchdog", exc)

    async def watch(self) -> None:
        """One look: end a finished stretch of speech, and take the call back
        from a supervisor who has dropped off."""
        now = time.monotonic()
        if (
            self.speech is not None
            and now - self.speech["last_voiced"] > SPEECH_HANGOVER_SECONDS
        ):
            await self._end_speech()
        if self.state.supervised and now - self._last_seen > self.recovery_seconds:
            logger.info(
                "Live take-over for run {}: supervisor gone for {:.1f}s; the agent "
                "takes the call back",
                self.run_id,
                now - self._last_seen,
            )
            await self.resume(action=RECOVERED)

    # -- commands -----------------------------------------------------------

    async def on_command(self, command: dict[str, Any]) -> None:
        kind = command.get("kind")
        user_id = command.get("by_user_id")
        if kind == "ping":
            if self.holder and self.holder["by_user_id"] == user_id:
                self._last_seen = time.monotonic()
            return
        async with self._lock:
            if kind == "join":
                await self.join(
                    str(command.get("mode") or ""),
                    by=str(command.get("by") or ""),
                    by_user_id=user_id,
                    phone=command.get("phone"),
                )
            elif kind == "let_agent_answer":
                await self.let_agent_answer(by_user_id=user_id)
            elif kind == "hand_back":
                await self.resume(
                    action=HANDED_BACK,
                    by=str(command.get("by") or ""),
                    by_user_id=user_id,
                )
            else:
                logger.warning("Live take-over: unknown command {!r}", kind)

    async def join(
        self, mode: str, *, by: str, by_user_id: int | None, phone: str | None = None
    ) -> bool:
        """A supervisor joins, or switches between barge and take-over."""
        if self.closed or self._task is None or mode not in (BARGE, TAKEOVER):
            return False
        if self.holder and self.holder["by_user_id"] != by_user_id:
            # The API's SET NX keeps this from happening; if it does, the
            # person on the call keeps it.
            logger.warning("Live take-over: a second supervisor was refused")
            return False
        if self.holder:
            return await self._switch(mode)
        try:
            self.bridge = await bridges.make(
                self.bridge_name,
                workflow_run=self.workflow_run,
                organization_id=self.organization_id,
            )
            await self.bridge.join(
                mode,
                bridges.SupervisorLeg(
                    by=by, by_user_id=int(by_user_id or 0), phone=phone
                ),
            )
        except Exception as exc:  # noqa: BLE001 - told to the person joining
            detail = (
                str(exc)
                if isinstance(exc, bridges.BridgeUnavailable)
                else "Could not put you on the call."
            )
            logger.warning(
                "Live take-over: join failed for run {}: {}", self.run_id, exc
            )
            self.bridge = None
            await self._record(
                FAILED, mode=AI, by=by, by_user_id=by_user_id, detail=detail
            )
            await self._clear_state()
            return False
        self.holder = {
            "by": by,
            "by_user_id": by_user_id,
            "since": _now(),
            "started": time.monotonic(),
            "first_mode": mode,
        }
        self._last_seen = time.monotonic()
        self.state.ai_may_speak = False
        self.state.mode = mode
        await self._cut_agent_off()
        await self._mute_agent(True)
        await self._record(JOINED, mode=mode, by=by, by_user_id=by_user_id)
        return True

    async def _switch(self, mode: str) -> bool:
        if mode == self.state.mode:
            return True
        was_speaking = self.state.ai_may_speak
        self.state.mode = mode
        self.state.ai_may_speak = False
        if was_speaking:
            await self._cut_agent_off()
            await self._mute_agent(True)
        try:
            if self.bridge is not None:
                await self.bridge.set_mode(mode)
        except Exception as exc:  # noqa: BLE001
            self._warn("bridge mode", exc)
        holder = self.holder or {}
        await self._record(
            SWITCHED,
            mode=mode,
            by=holder.get("by"),
            by_user_id=holder.get("by_user_id"),
        )
        return True

    async def let_agent_answer(self, *, by_user_id: int | None) -> bool:
        """In a barge: the agent answers the caller, until the supervisor
        next speaks."""
        if (
            self.state.mode != BARGE
            or not self.holder
            or self.holder["by_user_id"] != by_user_id
        ):
            return False
        self.state.ai_may_speak = True
        await self._mute_agent(False)
        await self._task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[answer_message(self.holder["by"])], run_llm=True
                )
            ]
        )
        await self._record(
            AGENT_ANSWERING, mode=BARGE, by=self.holder["by"], by_user_id=by_user_id
        )
        return True

    async def resume(
        self,
        *,
        action: str,
        by: str | None = None,
        by_user_id: int | None = None,
    ) -> bool:
        """The agent has the call again: handed back, or recovered from a
        supervisor who dropped off."""
        if not self.state.supervised or self.holder is None:
            return False
        holder = self.holder
        mode = self.state.mode
        seconds = int(time.monotonic() - holder["started"])
        await self._end_speech()
        self.state.mode = AI
        self.state.ai_may_speak = False
        self.holder = None
        await self._leave_bridge()
        message = (
            recovery_message(holder["by"])
            if action == RECOVERED
            else handback_message(holder["by"], mode, seconds)
        )
        if self._task is not None and not self.closed:
            await self._task.queue_frames(
                [LLMMessagesAppendFrame(messages=[message], run_llm=True)]
            )
        await self._record(
            action,
            mode=AI,
            by=by or holder["by"],
            by_user_id=by_user_id if by_user_id is not None else holder["by_user_id"],
            supervisor=holder["by"],
            seconds=seconds,
        )
        await self._clear_state()
        if action == RECOVERED:
            await audit_log.record(
                self.organization_id,
                action="call_takeover_recovered",
                subject_kind="call",
                subject_id=self.run_id,
                subject="Live call",
                after={"supervisor": holder["by"], "mode": mode, "seconds": seconds},
                note=(
                    f"The supervisor was not heard from for "
                    f"{self.recovery_seconds:g}s; the agent took the call back."
                ),
            )
        return True

    # -- the supervisor's voice ----------------------------------------------

    async def on_mic(self, packet: bytes) -> bool:
        """One slice of the supervisor's microphone. Played to the caller
        while they have the call; dropped otherwise."""
        if (
            not self.state.supervised
            or self.bridge is None
            or not self.bridge.browser_audio
            or not channels.valid_mic_packet(packet)
        ):
            return False
        _side, rate, chans, pcm = live_channels.decode_audio(packet)
        now = time.monotonic()
        self._last_seen = now
        if rms(pcm) >= SPEECH_RMS:
            if self.state.ai_may_speak:
                # The supervisor talks over the agent they let answer: it
                # stops, as a person would.
                self.state.ai_may_speak = False
                await self._cut_agent_off()
                await self._mute_agent(True)
                holder = self.holder or {}
                await self._record(
                    AGENT_PAUSED,
                    mode=self.state.mode,
                    by=holder.get("by"),
                    by_user_id=holder.get("by_user_id"),
                )
            if self.speech is None:
                self.speech = {
                    "id": uuid.uuid4().hex,
                    "by": (self.holder or {}).get("by"),
                    "at": _now(),
                    "started": now,
                    "last_voiced": now,
                }
                await self._publish(
                    "supervisor",
                    id=self.speech["id"],
                    by=self.speech["by"],
                    final=False,
                    seconds=0,
                )
            else:
                self.speech["last_voiced"] = now
        frame = SupervisorAudioFrame(audio=pcm, sample_rate=rate, num_channels=chans)
        if await self.output_gate.inject(frame):
            self.played_frames += 1
            return True
        return False

    async def _end_speech(self) -> None:
        span, self.speech = self.speech, None
        if span is None:
            return
        seconds = max(1, round(span["last_voiced"] - span["started"]))
        payload = {
            "id": span["id"],
            "by": span["by"],
            "seconds": seconds,
            "started_at": span["at"],
            "spoken": True,
            "transcribed": False,
        }
        await self._append_record(EVENT_SPEECH, payload)
        await self._publish(
            "supervisor", id=span["id"], by=span["by"], final=True, seconds=seconds
        )

    # -- effects ---------------------------------------------------------------

    async def _cut_agent_off(self) -> None:
        """Stop whatever the agent is saying, right now, including audio
        already queued at the transport (and Plivo's buffer)."""
        if self._task is None:
            return
        interruption = InterruptionFrame()
        self.state.let_through(interruption)
        await self._task.queue_frames([interruption])

    async def _mute_agent(self, muted: bool) -> None:
        if self.bridge is None:
            return
        try:
            await self.bridge.set_agent_muted(muted)
        except Exception as exc:  # noqa: BLE001 - the gates silence it anyway
            self._warn("bridge mute", exc)

    async def _leave_bridge(self) -> None:
        bridge, self.bridge = self.bridge, None
        if bridge is None:
            return
        try:
            await bridge.leave()
        except Exception as exc:  # noqa: BLE001
            self._warn("bridge leave", exc)

    async def _clear_state(self) -> None:
        try:
            await self.redis().delete(channels.state_key(self.run_id))
        except Exception as exc:  # noqa: BLE001
            self._warn("clear state", exc)

    async def _record(self, action: str, *, mode: str, **fields: Any) -> None:
        """Into the call's record, out to listeners, and (while somebody has
        the call) into the state the API reads."""
        payload = {"action": action, "mode": mode, "at": _now(), **fields}
        await self._append_record(EVENT_TAKEOVER, payload)
        await self._publish("takeover", **payload)
        if self.holder is not None and action != FAILED:
            state = {
                "mode": self.state.mode,
                "by": self.holder["by"],
                "by_user_id": self.holder["by_user_id"],
                "since": self.holder["since"],
                "agent_answering": self.state.ai_may_speak,
                "bridge": self.bridge.name if self.bridge else None,
            }
            try:
                await self.redis().set(
                    channels.state_key(self.run_id),
                    json.dumps(state),
                    ex=channels.STATE_TTL_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                self._warn("state", exc)

    async def _append_record(self, kind: str, payload: dict[str, Any]) -> None:
        if self._logs is None:
            return
        try:
            await self._logs.append({"type": kind, "payload": payload})
        except Exception as exc:  # noqa: BLE001
            self._warn("call record", exc)

    async def _publish(self, kind: str, **fields: Any) -> None:
        if self._live is None:
            return
        try:
            await self._live.publish_event(kind, **fields)
        except Exception as exc:  # noqa: BLE001
            self._warn("publish", exc)


def prepare(*, workflow_run: Any, workflow: Any) -> TakeoverController | None:
    """The controller for a call, before its pipeline is built; None (and
    nothing added to the pipeline) unless the feature is on. Never raises."""
    try:
        organization_id = getattr(workflow, "organization_id", None)
        if organization_id is None:
            return None
        if not features.is_on(FEATURE, organization_id) or not features.is_on(
            NEEDS, organization_id
        ):
            return None
        if str(getattr(workflow_run, "mode", "") or "") in NOT_A_CALL:
            return None
        return TakeoverController(
            run_id=int(workflow_run.id),
            organization_id=int(organization_id),
            workflow_run=workflow_run,
        )
    except Exception as exc:  # noqa: BLE001 - a take-over must not stop a call
        logger.warning("Live take-over not prepared for this call: {}", exc)
        return None


__all__ = ["MODES", "TakeoverController", "prepare"]

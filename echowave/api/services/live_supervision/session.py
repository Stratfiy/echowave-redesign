"""One live call's side of supervision, on the worker running the call.

``attach`` is the whole of the pipeline hook: ``run_pipeline`` calls it
once the task exists and closes what it returns when the call ends. It
returns None -- and the call runs exactly as before -- unless the feature is
on for the call's organisation.

The session owns four small loops, none of them in the caller's path:

- **audio** and **events** drain the tap's queues and publish to Redis
  (``channels``). Audio is only copied while somebody is listening to it:
  ``PUBLISH`` says how many received a packet, and at zero the tap stops
  copying for ``AUDIO_IDLE_SECONDS`` before trying again.
- **heartbeat** keeps the call in the organisation's live list, with the
  step it is on.
- **control** receives whispers and hands them to the pipeline (``whisper``).

Every Redis failure is logged and survived. Supervision going dark is a
missing panel; it must never be a dropped call.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.services import features
from api.services.live_supervision import channels, tap, whisper
from api.services.live_supervision.lines import LineBuilder

FEATURE = "live_supervision"
#: Not listened to: stop copying audio for this long, then try once more.
AUDIO_IDLE_SECONDS = 2.0
#: Audio frames joined into one packet, at most (20 ms each: 200 ms).
MAX_FRAMES_PER_PACKET = 10
#: Run modes that are not a call.
NOT_A_CALL = frozenset({"textchat", "CHAT"})


def _direction(workflow_run: Any) -> str:
    """``inbound``, ``outbound`` or ``web``: what the list says about a call."""
    mode = str(getattr(workflow_run, "mode", "") or "")
    if mode in ("webrtc", "smallwebrtc"):
        return "web"
    call_type = getattr(workflow_run, "call_type", None)
    value = getattr(call_type, "value", call_type)
    return "inbound" if value == "inbound" else "outbound"


class LiveSession:
    def __init__(
        self,
        *,
        run_id: int,
        organization_id: int,
        workflow_id: int,
        agent_name: str,
        direction: str,
        task: Any = None,
        context: Any = None,
        logs_buffer: Any = None,
        step: Callable[[], str | None] = lambda: None,
        redis: Any = None,
    ):
        self.run_id = run_id
        self.organization_id = organization_id
        self.workflow_id = workflow_id
        self.agent_name = agent_name
        self.direction = direction
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._task = task
        self._context = context
        self._logs = logs_buffer
        self._step = step
        self._redis = redis
        self._audio_idle_until = 0.0
        self.tap = tap.LiveCallTap(audio_wanted=self.audio_wanted)
        self.lines = LineBuilder()
        self._loops: list[asyncio.Task] = []
        self._pubsub = None
        self._last_step: str | None = None
        self._errors = 0
        self.whispers_applied = 0
        self.closed = False

    # -- plumbing -----------------------------------------------------------

    def redis(self):
        return self._redis if self._redis is not None else channels.redis()

    def audio_wanted(self) -> bool:
        return time.monotonic() >= self._audio_idle_until

    def _warn(self, what: str, exc: BaseException) -> None:
        self._errors += 1
        if self._errors == 1 or self._errors % 100 == 0:
            logger.warning(
                "Live supervision for run {}: {} failed ({} so far): {}",
                self.run_id,
                what,
                self._errors,
                exc,
            )

    def meta(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "organization_id": self.organization_id,
            "workflow_id": self.workflow_id,
            "agent_name": self.agent_name,
            "direction": self.direction,
            "started_at": self.started_at,
            "step": self._current_step(),
        }

    def _current_step(self) -> str | None:
        try:
            return self._step()
        except Exception:  # noqa: BLE001 - a step we cannot read is shown as none
            return None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Subscribe for whispers, then appear in the live list. In that
        order, so a call that can be seen can always be whispered to."""
        try:
            self._pubsub = self.redis().pubsub()
            await self._pubsub.subscribe(channels.control_channel(self.run_id))
            await self._beat()
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            self._warn("start", exc)
        self._loops = [
            asyncio.create_task(self._audio_loop(), name=f"live-audio-{self.run_id}"),
            asyncio.create_task(self._event_loop(), name=f"live-events-{self.run_id}"),
            asyncio.create_task(
                self._heartbeat_loop(), name=f"live-beat-{self.run_id}"
            ),
            asyncio.create_task(self._control_loop(), name=f"live-ctl-{self.run_id}"),
        ]

    async def close(self) -> None:
        """The call ended: leave the list, tell the listeners, forget the
        whispers. Safe to call twice."""
        if self.closed:
            return
        self.closed = True
        self.tap.closed = True
        for loop in self._loops:
            loop.cancel()
        for loop in self._loops:
            try:
                await loop
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._loops = []
        whisper.strip_whispers(self._context)
        try:
            if self._pubsub is not None:
                await self._pubsub.unsubscribe(channels.control_channel(self.run_id))
                await self._pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            self._warn("unsubscribe", exc)
        try:
            r = self.redis()
            await r.zrem(channels.org_key(self.organization_id), str(self.run_id))
            await r.delete(
                channels.meta_key(self.run_id), channels.backlog_key(self.run_id)
            )
            await r.publish(
                channels.events_channel(self.run_id),
                json.dumps(self.lines.event("ended")),
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("close", exc)

    # -- loops --------------------------------------------------------------

    async def _beat(self) -> None:
        r = self.redis()
        meta = self.meta()
        await r.set(
            channels.meta_key(self.run_id),
            json.dumps(meta),
            ex=channels.STALE_SECONDS,
        )
        await r.zadd(
            channels.org_key(self.organization_id), {str(self.run_id): time.time()}
        )
        step = meta.get("step")
        if step != self._last_step:
            self._last_step = step
            await self._publish_event(self.lines.event("step", step=step))

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(channels.HEARTBEAT_SECONDS)
            try:
                await self._beat()
            except Exception as exc:  # noqa: BLE001
                self._warn("heartbeat", exc)

    async def _audio_loop(self) -> None:
        queue = self.tap.audio
        while True:
            first = await queue.get()
            batch = [first]
            while len(batch) < MAX_FRAMES_PER_PACKET and not queue.empty():
                batch.append(queue.get_nowait())
            try:
                await self._publish_audio(batch)
            except Exception as exc:  # noqa: BLE001
                self._warn("audio", exc)
                await asyncio.sleep(1.0)

    async def _publish_audio(self, batch: list[tuple]) -> None:
        packets: list[bytes] = []
        current: list = []
        for _kind, side, rate, chans, pcm in batch:
            if current and (current[0], current[1], current[2]) != (side, rate, chans):
                packets.append(self._packet(current))
                current = []
            if not current:
                current = [side, rate, chans, []]
            current[3].append(pcm)
        if current:
            packets.append(self._packet(current))
        channel = channels.audio_channel(self.run_id)
        for packet in packets:
            receivers = await self.redis().publish(channel, packet)
            if not receivers:
                # Nobody is listening to sound: stop copying it for a while
                # and throw away what was already copied.
                self._audio_idle_until = time.monotonic() + AUDIO_IDLE_SECONDS
                while not self.tap.audio.empty():
                    self.tap.audio.get_nowait()
                return

    @staticmethod
    def _packet(current: list) -> bytes:
        side = channels.SIDE_CALLER if current[0] == "c" else channels.SIDE_AGENT
        return channels.encode_audio(side, current[1], current[2], b"".join(current[3]))

    async def _event_loop(self) -> None:
        queue = self.tap.events
        while True:
            item = await queue.get()
            kind = item[0]
            if kind == tap.CALLER:
                events = self.lines.caller(item[1], item[2])
            elif kind == tap.AGENT_TEXT:
                events = self.lines.agent_text(item[1])
            elif kind == tap.AGENT_DONE:
                events = self.lines.agent_done()
            elif kind == tap.INTERRUPTED:
                events = self.lines.interrupted()
            else:
                logger.warning("Live tap queued an unknown item: {}", kind)
                events = []
            for event in events:
                try:
                    await self._publish_event(event)
                except Exception as exc:  # noqa: BLE001
                    self._warn("events", exc)

    async def _publish_event(self, event: dict) -> None:
        r = self.redis()
        body = json.dumps(event)
        keep = (event.get("type") == "line" and event.get("final")) or event.get(
            "type"
        ) == "whisper"
        if keep:
            key = channels.backlog_key(self.run_id)
            await r.rpush(key, body)
            await r.ltrim(key, -channels.BACKLOG_LINES, -1)
            await r.expire(key, channels.BACKLOG_TTL_SECONDS)
        await r.publish(channels.events_channel(self.run_id), body)

    async def _control_loop(self) -> None:
        if self._pubsub is None:
            return
        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._warn("control", exc)
                await asyncio.sleep(1.0)
                continue
            if not message or message.get("type") != "message":
                continue
            try:
                await self.apply_whisper(json.loads(message["data"]))
            except Exception as exc:  # noqa: BLE001
                self._warn("whisper", exc)

    # -- whispers -------------------------------------------------------------

    async def apply_whisper(self, payload: dict[str, Any]) -> bool:
        """Put one instruction into the running call. Returns whether it was."""
        if self.closed or self._task is None:
            return False
        text = whisper.clean_text(payload.get("text"))
        if not text:
            return False
        by = str(payload.get("by") or "").strip() or "Supervisor"
        urgent = bool(payload.get("urgent"))
        whisper_id = str(payload.get("id") or uuid.uuid4().hex)
        message = whisper.supervisor_message(by, text)
        await self._task.queue_frames(whisper.frames_for(message, urgent=urgent))
        self.whispers_applied += 1
        logger.info(
            "Supervisor whisper applied to run {} (urgent={})", self.run_id, urgent
        )
        if self._logs is not None:
            try:
                await self._logs.append(
                    whisper.record_event(
                        by=by, text=text, urgent=urgent, whisper_id=whisper_id
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._warn("record whisper", exc)
        event = self.lines.event(
            "whisper",
            id=whisper_id,
            by=by,
            by_user_id=payload.get("by_user_id"),
            text=text,
            urgent=urgent,
        )
        try:
            await self._publish_event(event)
        except Exception as exc:  # noqa: BLE001 - it is in the call already
            self._warn("publish whisper", exc)
        return True


async def attach(
    task: Any,
    *,
    workflow_run: Any,
    workflow: Any,
    logs_buffer: Any = None,
    context: Any = None,
) -> LiveSession | None:
    """Start supervision for a call, when the feature is on for its
    organisation. Never raises: a failure here leaves the call as it was."""
    try:
        organization_id = getattr(workflow, "organization_id", None)
        if organization_id is None or not features.is_on(FEATURE, organization_id):
            return None
        if str(getattr(workflow_run, "mode", "") or "") in NOT_A_CALL:
            return None
        session = LiveSession(
            run_id=int(workflow_run.id),
            organization_id=int(organization_id),
            workflow_id=int(workflow.id),
            agent_name=str(getattr(workflow, "name", "") or "Agent"),
            direction=_direction(workflow_run),
            task=task,
            context=context,
            logs_buffer=logs_buffer,
            step=(lambda: getattr(logs_buffer, "current_node_name", None))
            if logs_buffer is not None
            else (lambda: None),
        )
        task.add_observer(session.tap)
        await session.start()
        return session
    except Exception as exc:  # noqa: BLE001 - supervision must not stop a call
        logger.warning("Live supervision not started for this call: {}", exc)
        return None

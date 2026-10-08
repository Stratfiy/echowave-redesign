"""A person's live voice session with Decibyl: start, move, reconnect, end.

Screen 05 and handoff 21 ("Live voice interaction"). The audio itself runs in
the pipeline (``services/voice/pipeline.py``); this module is the record the
screen and the pipeline agree on:

* **One live session per person** (handoff 9): a partial unique index on
  ``voice_sessions``; a second start answers ``AlreadyLive`` with the live
  one's id, so the screen can offer to end it rather than fail.
* **Stale moves lose** (design "Event delivery"): every move names the
  ``state_version`` it read; the update is conditional on it.
* **Reconnect discloses loss**: going back to live after ``reconnecting``
  closes a gap measured on the server's own clock and records that the audio
  in it was not heard -- nothing is buffered across a dropped connection, so
  it is always lost, and the screen says so.
* **Ending releases**: an ended session is never live again; its minutes are
  settled against the person's daily voice allowance (operational quotas),
  the first minute having been taken when the audio connected.
* **Private**: every read and write is by organisation *and* person.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api import constants
from api.db import db_client
from api.db.voice_models import VoiceSessionModel

FLAG = "decibyl_voice"

CONNECTING = "connecting"
LIVE = "live"
RECONNECTING = "reconnecting"
ENDED = "ended"
FAILED = "failed"
LIVE_STATES = (CONNECTING, LIVE, RECONNECTING)
TERMINAL = (ENDED, FAILED)

#: What the client may say it is doing while live (display only).
PHASES = ("listening", "processing", "speaking")

ALLOWED: dict[str, tuple[str, ...]] = {
    CONNECTING: (LIVE, FAILED, ENDED),
    LIVE: (LIVE, RECONNECTING, FAILED, ENDED),
    RECONNECTING: (LIVE, FAILED, ENDED),
    ENDED: (),
    FAILED: (),
}

#: Why a session ended, as codes (analytics allows codes only).
END_REASONS = (
    "user_ended",
    "mic_denied",
    "device_missing",
    "connect_timeout",
    "lost",
    "voice_limit_reached",
    "needs_setup",
    "failed",
    "replaced",
)


class SessionError(Exception):
    code = "session_error"


class NotFound(SessionError):
    code = "not_found"


class AlreadyLive(SessionError):
    code = "already_live"

    def __init__(self, session_id: int | None):
        super().__init__("You already have a live voice session.")
        self.session_id = session_id


class Stale(SessionError):
    code = "stale"

    def __init__(self, current: dict[str, Any]):
        super().__init__("This session changed since you looked at it.")
        self.current = current


class NotAllowed(SessionError):
    code = "not_allowed"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def as_dict(row: VoiceSessionModel) -> dict[str, Any]:
    gaps = list(row.gaps or [])
    lost_ms = sum(int(g.get("lost_ms") or 0) for g in gaps if g.get("ended_at"))
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "state": row.state,
        "phase": row.phase,
        "state_version": row.state_version,
        "muted": bool(row.muted),
        "language": row.language,
        "voice": row.voice,
        "config": dict(row.config or {}),
        "reconnects": row.reconnects,
        "gaps": gaps,
        "lost_ms": lost_ms,
        "end_reason": row.end_reason,
        "created_at": _iso(row.created_at),
        "connected_at": _iso(row.connected_at),
        "ended_at": _iso(row.ended_at),
    }


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def gap_sentence(gap: dict[str, Any]) -> str:
    """What the person is told after a reconnect (handoff 21)."""
    seconds = max(1, round(int(gap.get("lost_ms") or 0) / 1000))
    unit = "second" if seconds == 1 else "seconds"
    return (
        f"Reconnected. About {seconds} {unit} of audio was lost; anything you "
        "said then was not heard."
    )


async def _live_for(session, user_id: int) -> VoiceSessionModel | None:
    return (
        await session.scalars(
            select(VoiceSessionModel).where(
                VoiceSessionModel.user_id == user_id,
                VoiceSessionModel.state.in_(LIVE_STATES),
            )
        )
    ).first()


async def start(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    language: str | None,
    voice: str | None,
    config: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open a session in ``connecting``. Raises AlreadyLive."""
    now = now or _now()
    await sweep_stale(user_id=user_id, now=now)
    async with db_client.async_session() as session:
        row = VoiceSessionModel(
            organization_id=organization_id,
            user_id=user_id,
            thread_id=thread_id,
            state=CONNECTING,
            state_version=1,
            language=language,
            voice=voice,
            config=config,
            gaps=[],
            created_at=now,
            last_seen_at=now,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            live = await _live_for(session, user_id)
            raise AlreadyLive(live.id if live else None)
        await session.refresh(row)
        return as_dict(row)


async def get(
    *, organization_id: int, user_id: int, session_id: int
) -> dict[str, Any] | None:
    async with db_client.async_session() as session:
        row = await _scoped(session, organization_id, user_id, session_id)
        return as_dict(row) if row else None


async def _scoped(
    session, organization_id: int, user_id: int, session_id: int
) -> VoiceSessionModel | None:
    return (
        await session.scalars(
            select(VoiceSessionModel).where(
                VoiceSessionModel.id == session_id,
                VoiceSessionModel.organization_id == organization_id,
                VoiceSessionModel.user_id == user_id,
            )
        )
    ).first()


async def move(
    *,
    organization_id: int,
    user_id: int,
    session_id: int,
    expected_version: int,
    to: str,
    phase: str | None = None,
    muted: bool | None = None,
    end_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move the session, if ``expected_version`` is still current.

    ``reconnecting`` opens a gap at now; ``live`` after it closes the gap
    with its length on the server's clock and counts a reconnect. Raises
    NotFound, Stale or NotAllowed.
    """
    now = now or _now()
    if phase is not None and phase not in PHASES:
        raise NotAllowed(f"{phase} is not a phase")
    async with db_client.async_session() as session:
        row = await _scoped(session, organization_id, user_id, session_id)
        if row is None:
            raise NotFound("No such session.")
        if row.state_version != expected_version:
            raise Stale(as_dict(row))
        if to not in ALLOWED.get(row.state, ()):
            raise NotAllowed(f"A {row.state} session cannot become {to}.")
        was = row.state
        values: dict[str, Any] = {
            "state": to,
            "state_version": row.state_version + 1,
            "last_seen_at": now,
        }
        gaps = list(row.gaps or [])
        if to == RECONNECTING:
            gaps.append({"started_at": now.isoformat(), "ended_at": None})
            values["gaps"] = gaps
            values["phase"] = None
        elif to == LIVE and row.state == RECONNECTING:
            gaps = _close_gap(gaps, now)
            values["gaps"] = gaps
            values["reconnects"] = (row.reconnects or 0) + 1
        if to == LIVE and row.connected_at is None:
            values["connected_at"] = now
        if to == LIVE and phase is not None:
            values["phase"] = phase
        if muted is not None:
            values["muted"] = bool(muted)
        if to in TERMINAL:
            values["ended_at"] = now
            values["phase"] = None
            values["end_reason"] = _reason(end_reason, to)
            if row.state == RECONNECTING:
                values["gaps"] = _close_gap(gaps, now)
        result = await session.execute(
            update(VoiceSessionModel)
            .where(
                VoiceSessionModel.id == row.id,
                VoiceSessionModel.state_version == expected_version,
            )
            .values(**values)
            .returning(VoiceSessionModel.id)
        )
        if result.first() is None:
            await session.rollback()
            fresh = await get(
                organization_id=organization_id, user_id=user_id, session_id=session_id
            )
            raise Stale(fresh or {})
        await session.commit()
    moved = await get(
        organization_id=organization_id, user_id=user_id, session_id=session_id
    )
    assert moved is not None
    if to in TERMINAL and was not in TERMINAL:
        await _settle(moved, organization_id=organization_id, user_id=user_id)
    return moved


def _close_gap(gaps: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    out = list(gaps)
    if out and not out[-1].get("ended_at"):
        opened = datetime.fromisoformat(out[-1]["started_at"])
        out[-1] = {
            **out[-1],
            "ended_at": now.isoformat(),
            "lost_ms": max(0, int((now - _aware(opened)).total_seconds() * 1000)),
            # Nothing is buffered across a dropped connection.
            "audio_lost": True,
        }
    return out


def _reason(reason: str | None, to: str) -> str:
    if reason in END_REASONS:
        return reason
    return "failed" if to == FAILED else "user_ended"


async def end(
    *,
    organization_id: int,
    user_id: int,
    session_id: int,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """End the session whatever its version: End must always work.

    Idempotent: ending an ended session returns it unchanged.
    """
    for _ in range(3):
        current = await get(
            organization_id=organization_id, user_id=user_id, session_id=session_id
        )
        if current is None:
            raise NotFound("No such session.")
        if current["state"] in TERMINAL:
            return current
        try:
            return await move(
                organization_id=organization_id,
                user_id=user_id,
                session_id=session_id,
                expected_version=current["state_version"],
                to=ENDED,
                end_reason=reason or "user_ended",
                now=now,
            )
        except Stale:
            continue
    raise NotAllowed("The session kept changing; try again.")


async def heartbeat(
    *, organization_id: int, user_id: int, session_id: int
) -> dict[str, Any]:
    """The screen is still open. Keeps a live session from being swept."""
    async with db_client.async_session() as session:
        result = await session.execute(
            update(VoiceSessionModel)
            .where(
                VoiceSessionModel.id == session_id,
                VoiceSessionModel.organization_id == organization_id,
                VoiceSessionModel.user_id == user_id,
                VoiceSessionModel.state.in_(LIVE_STATES),
            )
            .values(last_seen_at=_now())
            .returning(VoiceSessionModel.id)
        )
        await session.commit()
        hit = result.first() is not None
    current = await get(
        organization_id=organization_id, user_id=user_id, session_id=session_id
    )
    if current is None:
        raise NotFound("No such session.")
    if not hit and current["state"] not in TERMINAL:
        raise NotAllowed("This session is not live.")
    return current


async def server_lost_audio(session_id: int) -> None:
    """The pipeline's audio ended without the person pressing End: a dropped
    connection. A live session waits in ``reconnecting`` for the screen to
    reconnect or end; one never answered is swept. Never raises."""
    try:
        async with db_client.async_session() as session:
            row = await session.get(VoiceSessionModel, session_id)
            if row is None or row.state != LIVE:
                return
            organization_id, user_id, version = (
                row.organization_id,
                row.user_id,
                row.state_version,
            )
        await move(
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            expected_version=version,
            to=RECONNECTING,
        )
    except SessionError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not mark voice session {} lost: {}", session_id, exc)


async def sweep_stale(
    *, user_id: int | None = None, now: datetime | None = None
) -> int:
    """End live sessions nobody has heard from in VOICE_SESSION_STALE_SECONDS.

    Runs before every start (so a crashed tab cannot block a person's next
    session) and from the worker every minute. Returns how many it ended.
    """
    now = now or _now()
    cutoff = now - timedelta(seconds=constants.VOICE_SESSION_STALE_SECONDS)
    async with db_client.async_session() as session:
        query = select(
            VoiceSessionModel.id,
            VoiceSessionModel.organization_id,
            VoiceSessionModel.user_id,
        ).where(
            VoiceSessionModel.state.in_(LIVE_STATES),
            VoiceSessionModel.last_seen_at < cutoff,
        )
        if user_id is not None:
            query = query.where(VoiceSessionModel.user_id == user_id)
        stale = (await session.execute(query)).all()
    ended = 0
    for session_id, organization_id, owner in stale:
        try:
            await end(
                organization_id=organization_id,
                user_id=owner,
                session_id=session_id,
                reason="lost",
                now=now,
            )
            ended += 1
        except SessionError:
            continue
    return ended


async def _settle(
    session: dict[str, Any], *, organization_id: int, user_id: int
) -> None:
    """Voice minutes and the catalogue event for an ended session. Never
    raises: neither may change how the session ended."""
    from api.services import events, quotas

    try:
        connected = session.get("connected_at")
        ended = session.get("ended_at")
        seconds = 0.0
        if connected and ended:
            seconds = max(
                0.0,
                (
                    datetime.fromisoformat(ended) - datetime.fromisoformat(connected)
                ).total_seconds(),
            )
        if connected:
            # The first minute was taken when the audio connected
            # (routes/webrtc_signaling.py); the rest is recorded even past the
            # limit -- a conversation is not cut off mid-sentence.
            minutes = max(1, math.ceil(seconds / 60))
            if minutes > 1:
                await quotas.consume(
                    user_id,
                    quotas.VOICE_MINUTES,
                    min(minutes - 1, quotas.MAX_AMOUNT),
                    force=True,
                )
        name = (
            "voice_session_failed"
            if session["state"] == FAILED
            else "voice_session_ended"
        )
        await events.emit(
            name,
            user_id=user_id,
            organization_id=organization_id,
            task_id=f"voice:{session['id']}",
            properties={
                "channel": "web",
                "language": session.get("language"),
                "duration_ms": int(seconds * 1000),
                "reason_code": session.get("end_reason"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Settling voice session {} failed: {}", session.get("id"), exc)

"""Borrowed sessions under local sign-in (phase 3, `staff`).

``POST /superuser/impersonate`` only knew how to ask Stack Auth for a
session. Under ``AUTH_PROVIDER=local`` -- what staging and production run --
it called Stack anyway and answered 500, so neither "view as" nor
"impersonate" worked anywhere it mattered.

Here a local borrowed session is an ordinary local JWT for the customer with
three extra claims:

* ``imp``: the staff member's user id (who is really there);
* ``imp_mode``: ``read_only`` or ``full``;
* ``imp_start``: the ``impersonation_started`` audit row it belongs to.

Every request made with it goes through :func:`check`:

* a ``read_only`` session may read and nothing else -- any other method is a
  403, except ``POST /api/v1/impersonation/stop`` so the way out always works;
* once an ``impersonation_stopped`` row follows its start (the staffer pressed
  Stop, or staff ended it from the console), the token is refused. Ending
  assisted access revokes it on the server (handoff 44), not only in the
  browser that held it;
* it lives one hour, the same as a Stack borrowed session.

Read-only is enforced here, on every route, rather than on the screens --
a hidden button is not a permission.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

import jwt
from fastapi import HTTPException
from sqlalchemy import or_, select

from api.constants import OSS_JWT_SECRET
from api.db.models import AdminActionLogModel
from api.services.auth.impersonation_audit import SESSION_LIFETIME, STARTED, STOPPED

Mode = Literal["read_only", "full"]
MODES: tuple[str, ...] = ("read_only", "full")

#: Methods a read-only borrowed session may use.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: The one write a read-only session may make: ending itself.
ALWAYS_ALLOWED = frozenset({("POST", "/api/v1/impersonation/stop")})

READ_ONLY_DETAIL = (
    "This is a read-only view of the account. Nothing can be changed from it."
)
ENDED_DETAIL = "This view of the account has ended."

_NOTE = re.compile(r"^mode=(?P<mode>read_only|full)\b")


def note_for(mode: str, reason: str, email: str | None) -> str:
    """The start row's note: mode first (parsed back by :func:`mode_of`),
    then the reason staff gave, then who."""
    return f"mode={mode} | reason={reason.strip()} | {email or ''}"[:500]


def mode_of(note: str | None) -> str:
    """The mode a start row recorded. Rows from before modes existed were
    full impersonations."""
    match = _NOTE.match(note or "")
    return match.group("mode") if match else "full"


def mint(
    *, user_id: int, email: str | None, actor_user_id: int, mode: str, start_id: int
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "email": email or "",
            "iat": now,
            "exp": now + SESSION_LIFETIME,
            "imp": actor_user_id,
            "imp_mode": mode,
            "imp_start": start_id,
        },
        OSS_JWT_SECRET,
        algorithm="HS256",
    )


def is_borrowed(payload: dict) -> bool:
    return "imp" in payload


def refuse_write(payload: dict, method: str | None, path: str | None) -> None:
    """Synchronous half of :func:`check`: the read-only rule."""
    if payload.get("imp_mode") != "read_only":
        return
    method = (method or "GET").upper()
    if method in SAFE_METHODS or (method, path or "") in ALWAYS_ALLOWED:
        return
    raise HTTPException(status_code=403, detail=READ_ONLY_DETAIL)


async def ended(session, payload: dict) -> bool:
    """Whether the impersonation this token belongs to has been stopped (or
    its start row is missing, which is never a session to honour)."""
    start = await session.get(AdminActionLogModel, int(payload.get("imp_start") or 0))
    if start is None or start.action != STARTED:
        return True
    log = AdminActionLogModel
    target = log.target_user_id == int(payload["sub"])
    if start.target_provider_id:
        target = or_(target, log.target_provider_id == start.target_provider_id)
    stop = (
        await session.scalars(
            select(log.id)
            .where(log.action == STOPPED, target, log.created_at >= start.created_at)
            .limit(1)
        )
    ).first()
    return stop is not None


async def check(payload: dict, method: str | None, path: str | None) -> None:
    """Refuse a borrowed token that is read-only and writing, or ended."""
    if not is_borrowed(payload):
        return
    refuse_write(payload, method, path)
    from api.db import db_client

    async with db_client.async_session() as session:
        if await ended(session, payload):
            raise HTTPException(status_code=401, detail=ENDED_DETAIL)


async def open_for(session, user_id: int, now: datetime | None = None) -> dict | None:
    """The impersonation open on this user right now, for the console:
    ``{"mode", "since", "by", "start_id"}`` or None."""
    now = now or datetime.now(UTC)
    log = AdminActionLogModel
    start = (
        await session.scalars(
            select(log)
            .where(
                log.action == STARTED,
                log.target_user_id == user_id,
                log.created_at >= now - SESSION_LIFETIME,
            )
            .order_by(log.created_at.desc(), log.id.desc())
            .limit(1)
        )
    ).first()
    if start is None:
        return None
    stopped = (
        await session.scalars(
            select(log.id)
            .where(
                log.action == STOPPED,
                log.target_user_id == user_id,
                log.created_at >= start.created_at,
            )
            .limit(1)
        )
    ).first()
    if stopped is not None:
        return None
    return {
        "mode": mode_of(start.note),
        "since": start.created_at.isoformat() if start.created_at else None,
        "ends_at": (start.created_at + SESSION_LIFETIME).isoformat()
        if start.created_at
        else None,
        "by": start.actor_user_id,
        "start_id": start.id,
    }


__all__ = [
    "ENDED_DETAIL",
    "MODES",
    "READ_ONLY_DETAIL",
    "check",
    "ended",
    "is_borrowed",
    "mint",
    "mode_of",
    "note_for",
    "open_for",
    "refuse_write",
]

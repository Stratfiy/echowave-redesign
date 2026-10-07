"""Operational quotas: per-person daily limits that hold in free mode too.

Handoff 9 ("Free beta with operating limits") and 30: free mode
(services/billing/free_mode.py) deliberately bypasses every plan limit, so
cost and abuse control cannot live in billing. This is the separate
boundary. Four allowances, per person per day:

* ``model_turns`` -- a person asking Decibyl or an agent something. One turn
  is the person's line, not each internal model call it causes.
* ``voice_minutes`` -- live voice in the browser. One minute is taken when a
  session starts, so a stream of instant reconnects cannot each be free, and
  the rest is settled from the session's length when it ends.
* ``outbound_messages`` -- a confirmed card that reaches somebody (a mail
  sent, a document delivered). A draft or a read is not one.
* ``browser_minutes`` -- Decibyl's private browser. Nothing spends it yet;
  the `browser` stream calls ``consume`` per minute of a session.

The day is the UTC day. Not the person's own: a day that followed their
timezone would let changing the timezone start a fresh one, which is a
bypass. The message a person reads gives the reset in their local time.

**No bypass.** ``consume`` is a single conditional upsert: the row only
grows when the result stays within the limit, so two requests at once
cannot both see "one left" and both proceed. Amounts are positive integers,
so nothing can be "refunded" into a larger allowance. The only way past a
limit is a staff grant, which has a reason, an expiry, and an audit row.

Off (the default), nothing is counted and nothing is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, select, text, update

from api import constants
from api.db import db_client
from api.db.controls_models import QuotaAllowanceModel
from api.db.models import AdminActionLogModel
from api.services import features

FLAG = "operational_quotas"

MODEL_TURNS = "model_turns"
VOICE_MINUTES = "voice_minutes"
OUTBOUND_MESSAGES = "outbound_messages"
BROWSER_MINUTES = "browser_minutes"
KINDS = (MODEL_TURNS, VOICE_MINUTES, OUTBOUND_MESSAGES, BROWSER_MINUTES)

#: How each allowance is said to a person.
UNITS = {
    MODEL_TURNS: ("message", "messages"),
    VOICE_MINUTES: ("voice minute", "voice minutes"),
    OUTBOUND_MESSAGES: ("send", "sends"),
    BROWSER_MINUTES: ("browser minute", "browser minutes"),
}

#: The largest single spend. A minute count larger than a day is a bug.
MAX_AMOUNT = 24 * 60
#: The longest a staff grant may last, and its largest daily extra.
MAX_GRANT_DAYS = 31
MAX_GRANT_EXTRA = 10_000

DEFAULT_TIMEZONE = "Asia/Kolkata"


def enabled() -> bool:
    return features.is_on(FLAG)


def base_limit(kind: str) -> int:
    """The daily allowance before any grant, from the environment."""
    return {
        MODEL_TURNS: constants.OPERATIONAL_QUOTA_MODEL_TURNS,
        VOICE_MINUTES: constants.OPERATIONAL_QUOTA_VOICE_MINUTES,
        OUTBOUND_MESSAGES: constants.OPERATIONAL_QUOTA_OUTBOUND_MESSAGES,
        BROWSER_MINUTES: constants.OPERATIONAL_QUOTA_BROWSER_MINUTES,
    }[_known(kind)]


def _known(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown allowance: {kind}")
    return kind


def _person(user_id: Any) -> int:
    # bool is an int; a True here is a bug, not person 1.
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("An allowance belongs to a person")
    return user_id


def today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def resets_at(now: datetime | None = None) -> datetime:
    return datetime.combine(today(now) + timedelta(days=1), time(0), tzinfo=UTC)


@dataclass(frozen=True)
class Usage:
    kind: str
    used: int
    limit: int
    resets_at: datetime

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def as_dict(self) -> dict[str, Any]:
        one, many = UNITS[self.kind]
        return {
            "kind": self.kind,
            "unit": many,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "resets_at": self.resets_at.isoformat(),
        }


class QuotaExceeded(Exception):
    """A person has used today's allowance of one kind."""

    def __init__(self, usage: Usage):
        self.usage = usage
        super().__init__(message(usage))


def message(usage: Usage, timezone: str | None = None) -> str:
    """What the person reads, in the thread, when a limit is reached."""
    one, many = UNITS[usage.kind]
    try:
        zone = ZoneInfo(timezone or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001 - a bad stored zone must not hide the limit
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    local = usage.resets_at.astimezone(zone)
    when = local.strftime("%-I:%M %p").lower()
    what = one if usage.limit == 1 else many
    return (
        f"You have used today's {usage.limit} {what} in the early-access beta. "
        f"This resets at {when} your time. If you need more today, ask us "
        "from Help and we can raise it for a while."
    )


async def _extra(session, user_id: int, kind: str, now: datetime) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(QuotaAllowanceModel.extra), 0)).where(
            QuotaAllowanceModel.user_id == user_id,
            QuotaAllowanceModel.kind == kind,
            QuotaAllowanceModel.revoked_at.is_(None),
            QuotaAllowanceModel.expires_at > now,
            QuotaAllowanceModel.created_at <= now,
        )
    )
    return int(total or 0)


async def _used(session, user_id: int, kind: str, day: date) -> int:
    value = await session.scalar(
        text(
            "SELECT used FROM operational_usage "
            "WHERE user_id = :u AND kind = :k AND day = :d"
        ),
        {"u": user_id, "k": kind, "d": day},
    )
    return int(value or 0)


async def usage(user_id: int, kind: str, *, now: datetime | None = None) -> Usage:
    """Today's usage of one allowance, read-only."""
    user_id, kind = _person(user_id), _known(kind)
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        limit = base_limit(kind) + await _extra(session, user_id, kind, now)
        used = await _used(session, user_id, kind, today(now))
    return Usage(kind=kind, used=used, limit=limit, resets_at=resets_at(now))


async def check(user_id: int, kind: str, need: int = 1) -> Usage | None:
    """Raise QuotaExceeded unless ``need`` more fit today. None while off."""
    if not enabled():
        return None
    current = await usage(user_id, kind)
    if current.remaining < need:
        raise QuotaExceeded(current)
    return current


async def consume(
    user_id: int,
    kind: str,
    amount: int = 1,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> Usage | None:
    """Spend ``amount`` of today's allowance, or raise QuotaExceeded.

    ``force`` records usage that already happened (a voice session's
    length, settled when it ends) even past the limit: the call is not cut
    off mid-sentence, but the next one is refused. None while off.
    """
    if not enabled():
        return None
    user_id, kind = _person(user_id), _known(kind)
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError("An amount is a whole number")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValueError("An amount is positive and at most a day")
    now = now or datetime.now(UTC)
    day = today(now)
    async with db_client.async_session() as session:
        limit = base_limit(kind) + await _extra(session, user_id, kind, now)
        if not force and amount > limit:
            used = await _used(session, user_id, kind, day)
            raise QuotaExceeded(Usage(kind, used, limit, resets_at(now)))
        # One statement: insert today's row, or grow it only while the sum
        # stays within the limit. A refused grow returns no row.
        guard = "" if force else "WHERE operational_usage.used + :n <= :limit"
        row = (
            await session.execute(
                text(
                    "INSERT INTO operational_usage (user_id, kind, day, used, updated_at) "
                    "VALUES (:u, :k, :d, :n, :now) "
                    "ON CONFLICT (user_id, kind, day) DO UPDATE "
                    "SET used = operational_usage.used + :n, updated_at = :now "
                    f"{guard} RETURNING used"
                ),
                {
                    "u": user_id,
                    "k": kind,
                    "d": day,
                    "n": amount,
                    "now": now,
                    "limit": limit,
                },
            )
        ).first()
        if row is None:
            await session.rollback()
            used = await _used(session, user_id, kind, day)
            raise QuotaExceeded(Usage(kind, used, limit, resets_at(now)))
        await session.commit()
        return Usage(kind=kind, used=int(row[0]), limit=limit, resets_at=resets_at(now))


async def status(user_id: int) -> list[dict[str, Any]]:
    """Every allowance for one person, for their own screen."""
    return [(await usage(user_id, kind)).as_dict() for kind in KINDS]


# --- staff grants -----------------------------------------------------------


class GrantRefused(ValueError):
    pass


async def grant(
    *,
    user_id: int,
    kind: str,
    extra: int,
    reason: str,
    expires_at: datetime,
    granted_by: int,
    now: datetime | None = None,
) -> QuotaAllowanceModel:
    """A temporary raise for one person, with a reason, audited."""
    user_id, kind = _person(user_id), _known(kind)
    now = now or datetime.now(UTC)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise GrantRefused("Say why, in a few words.")
    if isinstance(extra, bool) or not isinstance(extra, int):
        raise GrantRefused("The extra allowance is a whole number.")
    if extra <= 0 or extra > MAX_GRANT_EXTRA:
        raise GrantRefused("The extra allowance must be positive.")
    if expires_at.tzinfo is None:
        raise GrantRefused("The expiry needs a timezone.")
    if expires_at <= now:
        raise GrantRefused("The expiry must be in the future.")
    if expires_at > now + timedelta(days=MAX_GRANT_DAYS):
        raise GrantRefused(f"A grant lasts at most {MAX_GRANT_DAYS} days.")
    async with db_client.async_session() as session:
        if await session.get(_user_model(), user_id) is None:
            raise GrantRefused("No such person.")
        row = QuotaAllowanceModel(
            user_id=user_id,
            kind=kind,
            extra=extra,
            reason=reason[:500],
            granted_by=granted_by,
            created_at=now,
            expires_at=expires_at,
        )
        session.add(row)
        session.add(
            AdminActionLogModel(
                actor_user_id=granted_by,
                action="quota_allowance_granted",
                target_user_id=user_id,
                note=(
                    f"kind={kind}; extra={extra}; until={expires_at.isoformat()}; "
                    f"reason={reason}"
                )[:500],
            )
        )
        await session.commit()
        await session.refresh(row)
    logger.info("Quota grant {} for user {} by {}", kind, user_id, granted_by)
    return row


async def revoke(*, allowance_id: int, revoked_by: int) -> bool:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        result = await session.execute(
            update(QuotaAllowanceModel)
            .where(
                QuotaAllowanceModel.id == allowance_id,
                QuotaAllowanceModel.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_by=revoked_by)
            .returning(QuotaAllowanceModel.user_id, QuotaAllowanceModel.kind)
        )
        hit = result.first()
        if hit is None:
            await session.rollback()
            return False
        session.add(
            AdminActionLogModel(
                actor_user_id=revoked_by,
                action="quota_allowance_revoked",
                target_user_id=hit[0],
                note=f"kind={hit[1]}; allowance={allowance_id}",
            )
        )
        await session.commit()
        return True


async def grants(user_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(QuotaAllowanceModel)
                    .where(QuotaAllowanceModel.user_id == user_id)
                    .order_by(QuotaAllowanceModel.created_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    now = datetime.now(UTC)
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "extra": r.extra,
            "reason": r.reason,
            "granted_by": r.granted_by,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            "live": r.revoked_at is None and r.expires_at > now,
        }
        for r in rows
    ]


def _user_model():
    from api.db.models import UserModel

    return UserModel

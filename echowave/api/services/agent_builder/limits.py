"""The daily cap on how much of our own model spend one account can use.

The builder runs on Decibyl's provider key, which makes it the only place in
the product where a customer action spends our money directly rather than
theirs. Every other path is either prepaid or passed through.

Three decisions shape this.

**Counted per account, not globally.** A global ceiling lets the first
enthusiastic account exhaust the day for everyone else, which turns one
person's exploration into everybody's outage.

**Counted in messages, not tokens.** Tokens are the real cost, but a person
cannot see them and a limit nobody can predict reads as a fault. A message is
what someone sends and what a refusal can honestly count.

**Counted in IST.** "Today" for an Indian account resets at midnight where they
are, not at 05:30 local because the counter is in UTC. The window is derived
from a fixed offset rather than a timezone database because India has one zone
and no daylight saving.

Redis rather than a table: the counter is per-day, expires on its own, and
nothing needs to reconcile it. A row would need a migration and a cleanup job
to do the same job worse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from loguru import logger

from api import constants

#: India Standard Time. Fixed — India has one zone and does not observe DST, so
#: a tzdata lookup would be a dependency for an arithmetic constant.
_IST = timedelta(hours=5, minutes=30)

#: Two days, so a counter set just before midnight still expires on its own
#: rather than lingering. The value is the day's count; the key carries the day.
_KEY_TTL_SECONDS = 172_800


@dataclass(frozen=True)
class LimitState:
    """Where an account stands against today's cap."""

    used: int
    limit: int
    #: When the counter resets, as a UTC instant, so a client can render it in
    #: whatever zone it likes without re-deriving the IST boundary.
    resets_at: datetime

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def ist_day(now: datetime | None = None) -> str:
    """The IST calendar day containing ``now``, as ``YYYY-MM-DD``.

    Pure, so the day-boundary behaviour is testable without freezing a clock.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment.astimezone(UTC) + _IST).strftime("%Y-%m-%d")


def next_ist_midnight(now: datetime | None = None) -> datetime:
    """The UTC instant at which the IST day rolls over."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    shifted = moment.astimezone(UTC) + _IST
    midnight = (shifted + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight - _IST


def _key(organization_id: int, day: str) -> str:
    return f"decibyl:agent_builder:{organization_id}:{day}"


async def check_and_consume(
    organization_id: int, *, now: datetime | None = None
) -> LimitState:
    """Take one message from today's allowance, if there is one left.

    Increments **before** the model is called, not after. A turn that fails
    halfway still cost us tokens, and a counter that only recorded successes
    would let a failing loop run all day for free.

    Redis being unreachable fails **closed** — the request is refused rather
    than allowed. This is the one limiter in the system guarding our own spend,
    and an unbounded fallback would turn a cache outage into a bill.
    """
    limit = constants.AGENT_BUILDER_DAILY_MESSAGE_LIMIT
    resets_at = next_ist_midnight(now)

    if limit <= 0:
        return LimitState(used=0, limit=0, resets_at=resets_at)

    day = ist_day(now)
    redis = None
    try:
        redis = aioredis.from_url(constants.REDIS_URL)
        used = await redis.incr(_key(organization_id, day))
        if used == 1:
            # First message of the day — give the key its lifetime. Set after
            # the increment rather than with it so a crash between the two
            # leaves a counted message rather than an uncounted one.
            await redis.expire(_key(organization_id, day), _KEY_TTL_SECONDS)
        return LimitState(used=used, limit=limit, resets_at=resets_at)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error(
            "Agent builder limit check failed for organization {}: {}. "
            "Refusing the request rather than spending unbounded.",
            organization_id,
            exc,
        )
        return LimitState(used=limit, limit=limit, resets_at=resets_at)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass


async def peek(organization_id: int, *, now: datetime | None = None) -> LimitState:
    """Today's usage without consuming any of it.

    Used by the screen to show what is left before someone types. Unlike
    :func:`check_and_consume` this fails **open** — reporting "0 used" when
    Redis is down is a cosmetic inaccuracy, and refusing to render the panel
    over a cache blip would be worse than a wrong number on it.
    """
    limit = constants.AGENT_BUILDER_DAILY_MESSAGE_LIMIT
    resets_at = next_ist_midnight(now)

    if limit <= 0:
        return LimitState(used=0, limit=0, resets_at=resets_at)

    redis = None
    try:
        redis = aioredis.from_url(constants.REDIS_URL)
        raw = await redis.get(_key(organization_id, ist_day(now)))
        used = int(raw) if raw else 0
        return LimitState(used=used, limit=limit, resets_at=resets_at)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Agent builder limit peek failed: {}", exc)
        return LimitState(used=0, limit=limit, resets_at=resets_at)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass

"""The monthly allowance of builder messages, by plan.

The builder runs on Decibyl's provider key, which makes it the only place in
the product where a customer action spends our money directly rather than
theirs. It used to be a flat daily cap; since KAN-56 it is an allowance the
plan includes (``plan_limits`` ``builder_messages``: 30 / 100 / 300 a month,
unlimited on Scale) and a price past it — five credits a message — so a
customer who builds a lot pays for it rather than being stopped.

Three decisions shape this.

**Counted per account, not globally.** A global ceiling lets the first
enthusiastic account exhaust the month for everyone else, which turns one
person's exploration into everybody's outage.

**Counted in messages, not tokens.** Tokens are the real cost, but a person
cannot see them and a limit nobody can predict reads as a fault. A message is
what someone sends and what a refusal can honestly count.

**Counted in IST.** "This month" for an Indian account rolls over at midnight
where they are, not at 05:30 local because the counter is in UTC. The window
is derived from a fixed offset rather than a timezone database because India
has one zone and no daylight saving.

Redis rather than a table: the counter is per-month, expires on its own, and
nothing needs to reconcile it — the charge past the allowance is a ledger row,
which is the record that matters.
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

#: Two months, so a counter set on the last day of a month still expires on
#: its own rather than lingering. The value is the month's count; the key
#: carries the month.
_KEY_TTL_SECONDS = 62 * 86_400

#: What a message past the allowance costs. Decided on KAN-47 (study §11).
PAST_ALLOWANCE_CREDITS = 5


@dataclass(frozen=True)
class LimitState:
    """Where an account stands against this month's allowance.

    ``limit`` is the allowance; ``None`` is unlimited. ``used`` counts the
    message being sent, so ``past_allowance`` is true for the first message
    the account pays for.
    """

    used: int
    limit: int | None
    #: When the counter resets, as a UTC instant, so a client can render it in
    #: whatever zone it likes without re-deriving the IST boundary.
    resets_at: datetime
    #: The counter could not be read. The caller refuses rather than spends.
    unavailable: bool = False

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)

    @property
    def past_allowance(self) -> bool:
        return self.limit is not None and self.used > self.limit

    @property
    def exhausted(self) -> bool:
        """Kept for callers that read the old daily cap: past the allowance."""
        return self.past_allowance


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


def ist_month(now: datetime | None = None) -> str:
    """The IST calendar month containing ``now``, as ``YYYY-MM``."""
    return ist_day(now)[:7]


def next_ist_month_start(now: datetime | None = None) -> datetime:
    """The UTC instant at which the IST month rolls over."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    shifted = moment.astimezone(UTC) + _IST
    year, month = shifted.year, shifted.month
    first_of_next = shifted.replace(
        year=year + (month == 12),
        month=1 if month == 12 else month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return first_of_next - _IST


def _key(organization_id: int, month: str) -> str:
    return f"decibyl:agent_builder:{organization_id}:{month}"


async def check_and_consume(
    organization_id: int,
    *,
    allowance: int | None,
    now: datetime | None = None,
) -> LimitState:
    """Count one message against this month, and say where that leaves it.

    Increments **before** the model is called, not after. A turn that fails
    halfway still cost us tokens, and a counter that only recorded successes
    would let a failing loop run all month for free.

    Redis being unreachable fails **closed** — the state says so and the
    caller refuses rather than allows. This is the one limiter in the system
    guarding our own spend, and an unbounded fallback would turn a cache
    outage into a bill.
    """
    resets_at = next_ist_month_start(now)
    month = ist_month(now)
    redis = None
    try:
        redis = aioredis.from_url(constants.REDIS_URL)
        used = await redis.incr(_key(organization_id, month))
        if used == 1:
            # First message of the month — give the key its lifetime. Set
            # after the increment rather than with it so a crash between the
            # two leaves a counted message rather than an uncounted one.
            await redis.expire(_key(organization_id, month), _KEY_TTL_SECONDS)
        return LimitState(used=used, limit=allowance, resets_at=resets_at)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error(
            "Agent builder allowance check failed for organization {}: {}. "
            "Refusing the request rather than spending unbounded.",
            organization_id,
            exc,
        )
        return LimitState(
            used=0, limit=allowance, resets_at=resets_at, unavailable=True
        )
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass


async def peek(
    organization_id: int, *, allowance: int | None, now: datetime | None = None
) -> LimitState:
    """This month's usage without consuming any of it.

    Used by the screen to show what is left before someone types. Unlike
    :func:`check_and_consume` this fails **open** — reporting "0 used" when
    Redis is down is a cosmetic inaccuracy, and refusing to render the panel
    over a cache blip would be worse than a wrong number on it.
    """
    resets_at = next_ist_month_start(now)
    redis = None
    try:
        redis = aioredis.from_url(constants.REDIS_URL)
        raw = await redis.get(_key(organization_id, ist_month(now)))
        used = int(raw) if raw else 0
        return LimitState(used=used, limit=allowance, resets_at=resets_at)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Agent builder allowance peek failed: {}", exc)
        return LimitState(used=0, limit=allowance, resets_at=resets_at)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass

"""Infrastructure health for the operations console (handoff 35, screen 39).

``system_status.snapshot`` answers "is the platform up" with ok / degraded.
This answers the operator's next question -- workers, queues, Redis and the
Postgres pool, plus whether the evidence an operator relies on (backups,
restore drills, analytics delivery, monitoring itself) is fresh -- and it is
built around one rule from the handoff:

**Monitoring silence is never shown as healthy.** Every signal has five
possible states, and only one of them is good:

``ok``              measured, recently, and within bounds
``degraded``        measured, and outside bounds
``down``            measured, and failing
``unknown``         could not be measured, or the last measurement is stale
``not_configured``  nothing is set up to measure it

The overall status is ``ok`` only when every core signal is ``ok``. An
unknown or unconfigured core signal makes the whole page ``unknown``, never
green: a dashboard that is green because nothing reported is the outage that
nobody sees.

Every probe is time-boxed and none raises, the same contract as
``system_status``: a hung probe becomes a row that says so.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants

PROBE_TIMEOUT_SECONDS = 2.0

OK = "ok"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"
NOT_CONFIGURED = "not_configured"
STATES = (OK, DEGRADED, DOWN, UNKNOWN, NOT_CONFIGURED)

#: Queue depth and oldest due job age past which the queue is degraded.
QUEUE_DEPTH_LIMIT = 500
QUEUE_AGE_LIMIT_SECONDS = 300
#: Pool checked-out share past which the pool is degraded.
POOL_SATURATION_LIMIT = 0.8
#: The newest backup should be younger than this (nightly + slack).
BACKUP_MAX_AGE_SECONDS = 36 * 3600
#: A restore drill older than this is overdue (monthly + slack).
RESTORE_DRILL_MAX_AGE_SECONDS = 35 * 86400
CAPACITY_REVIEW_MAX_AGE_SECONDS = 95 * 86400

#: The signals that decide the overall status.
CORE_SIGNALS = ("database", "postgres_pool", "redis", "queue", "worker")


@dataclass
class Signal:
    name: str
    status: str
    detail: str
    observed_at: str | None = None
    latency_ms: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(UTC)


async def _timed(name: str, fn: Callable[[], Awaitable[Signal]]) -> Signal:
    started = time.perf_counter()
    try:
        signal = await asyncio.wait_for(fn(), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return Signal(
            name,
            UNKNOWN,
            f"No answer within {PROBE_TIMEOUT_SECONDS:g} s; not counted as healthy.",
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
        logger.warning("Ops probe {} failed: {}", name, type(exc).__name__)
        return Signal(name, UNKNOWN, f"{type(exc).__name__} while probing {name}.")
    signal.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    signal.observed_at = signal.observed_at or _now().isoformat()
    return signal


def classify_age(
    age_seconds: float | None, *, max_age: float, what: str
) -> tuple[str, str]:
    """The status of a piece of evidence by its age. None is unknown."""
    if age_seconds is None:
        return UNKNOWN, f"No {what} on record."
    if age_seconds > max_age:
        return DEGRADED, f"Last {what} was {int(age_seconds // 3600)} h ago; overdue."
    return OK, f"Last {what} {int(age_seconds // 60)} min ago."


# --- probes ----------------------------------------------------------------


async def _database() -> Signal:
    from sqlalchemy import text

    from api.db import db_client

    async with db_client.async_session() as session:
        await session.execute(text("SELECT 1"))
    return Signal("database", OK, "Postgres answers.")


def pool_signal(pool: Any) -> Signal:
    """Pool occupancy from a SQLAlchemy QueuePool. Pure, for tests."""
    try:
        size = int(pool.size())
        checked_out = int(pool.checkedout())
        overflow = int(pool.overflow())
    except Exception as exc:  # noqa: BLE001
        return Signal(
            "postgres_pool", UNKNOWN, f"Pool not readable: {type(exc).__name__}."
        )
    capacity = size + max(int(constants.DB_POOL_MAX_OVERFLOW), 0)
    share = (checked_out / capacity) if capacity else 0.0
    metrics = {
        "size": size,
        "checked_out": checked_out,
        "overflow": overflow,
        "capacity": capacity,
        "saturation": round(share, 3),
    }
    if share >= 1.0:
        return Signal(
            "postgres_pool",
            DOWN,
            "Every connection is checked out; requests are waiting for the pool.",
            metrics=metrics,
        )
    if share >= POOL_SATURATION_LIMIT:
        return Signal(
            "postgres_pool",
            DEGRADED,
            f"{checked_out} of {capacity} connections in use in this process.",
            metrics=metrics,
        )
    return Signal(
        "postgres_pool",
        OK,
        f"{checked_out} of {capacity} connections in use in this process.",
        metrics=metrics,
    )


async def _pool() -> Signal:
    from api.db import db_client

    return pool_signal(db_client.engine.pool)


def _redis_client():
    import redis.asyncio as aioredis

    return aioredis.from_url(constants.REDIS_URL)


async def _redis() -> Signal:
    client = _redis_client()
    try:
        pong = await client.ping()
        info = await client.info(section="memory")
        clients = await client.info(section="clients")
    finally:
        await client.aclose()
    if not pong:
        return Signal("redis", DOWN, "Redis did not answer PING.")
    used = int(info.get("used_memory", 0) or 0)
    limit = int(info.get("maxmemory", 0) or 0)
    metrics = {
        "used_memory_bytes": used,
        "maxmemory_bytes": limit,
        "connected_clients": int(clients.get("connected_clients", 0) or 0),
    }
    if limit and used / limit >= 0.9:
        return Signal(
            "redis", DEGRADED, "Redis memory above 90% of maxmemory.", metrics=metrics
        )
    return Signal("redis", OK, "Redis answers.", metrics=metrics)


def queue_signal(depth: int, oldest_due_age_seconds: float | None) -> Signal:
    """Queue status from depth and the age of the oldest due job. Pure."""
    metrics = {"depth": depth, "oldest_due_age_seconds": oldest_due_age_seconds}
    if (
        oldest_due_age_seconds is not None
        and oldest_due_age_seconds > QUEUE_AGE_LIMIT_SECONDS
    ):
        return Signal(
            "queue",
            DEGRADED,
            f"Oldest due job has waited {int(oldest_due_age_seconds)} s; workers are behind.",
            metrics=metrics,
        )
    if depth > QUEUE_DEPTH_LIMIT:
        return Signal("queue", DEGRADED, f"{depth} jobs queued.", metrics=metrics)
    return Signal("queue", OK, f"{depth} jobs queued.", metrics=metrics)


async def _queue() -> Signal:
    from api.services.system_status import ARQ_QUEUE_KEY

    client = _redis_client()
    try:
        depth = int(await client.zcard(ARQ_QUEUE_KEY) or 0)
        now_ms = time.time() * 1000
        # ARQ scores a job by when it is due, in ms. The oldest score at or
        # before now is the job that has waited longest for a worker.
        oldest = await client.zrangebyscore(
            ARQ_QUEUE_KEY, "-inf", now_ms, start=0, num=1, withscores=True
        )
    finally:
        await client.aclose()
    age = None
    if oldest:
        age = round(max(now_ms - float(oldest[0][1]), 0.0) / 1000, 1)
    return queue_signal(depth, age)


async def _worker() -> Signal:
    from api.services.worker_health import worker_health

    health = await worker_health()
    alive = health.get("alive")
    status = OK if alive is True else DOWN if alive is False else UNKNOWN
    return Signal(
        "worker",
        status,
        str(health.get("detail") or ""),
        metrics={
            "last_seen": health.get("last_seen"),
            "age_seconds": health.get("age_seconds"),
        },
    )


async def _backups() -> Signal:
    from api.services.backup.database import last_successful

    found = await last_successful()
    if found.get("error"):
        return Signal("backups", UNKNOWN, "Backups could not be listed.")
    hours = found.get("age_hours")
    age = None if hours is None else float(hours) * 3600
    status, detail = classify_age(age, max_age=BACKUP_MAX_AGE_SECONDS, what="backup")
    return Signal("backups", status, detail, metrics={"age_seconds": age})


async def _evidence(kind: str, max_age: float, what: str) -> Signal:
    from api.db import db_client
    from api.services.ops import evidence

    async with db_client.async_session() as session:
        latest = await evidence.latest(session, kind)
    if latest is None:
        return Signal(kind, UNKNOWN, f"No {what} on record.")
    age = (_now() - latest.occurred_at).total_seconds()
    status, detail = classify_age(age, max_age=max_age, what=what)
    if latest.outcome != "passed":
        status, detail = DEGRADED, f"Last {what} {latest.outcome}: {latest.summary}"
    return Signal(
        kind, status, detail, metrics={"age_seconds": round(age, 1), **latest.metrics}
    )


async def _restore_drill() -> Signal:
    return await _evidence(
        "restore_drill", RESTORE_DRILL_MAX_AGE_SECONDS, "restore drill"
    )


async def _capacity_review() -> Signal:
    return await _evidence(
        "capacity_review", CAPACITY_REVIEW_MAX_AGE_SECONDS, "capacity review"
    )


async def _analytics() -> Signal:
    from api.db import db_client
    from api.services.events import outbox
    from api.services.ops import telemetry

    if not outbox.enabled():
        return Signal(
            "analytics_outbox", NOT_CONFIGURED, "The event catalogue is switched off."
        )
    async with db_client.async_session() as session:
        health = await telemetry.outbox_health(session)
    if not constants.POSTHOG_API_KEY or not constants.ANALYTICS_PSEUDONYM_KEY:
        # Still measured: events are held, not dropped, and a backlog that
        # grows with nobody able to see its size is the silent kind
        # (api/AGENTS.md, "Silent Absence").
        return Signal(
            "analytics_outbox",
            NOT_CONFIGURED,
            "PostHog key or ANALYTICS_PSEUDONYM_KEY is unset; "
            f"{health['pending']} events held.",
            metrics=health,
        )
    age = health["oldest_age_seconds"]
    if health["stuck"]:
        return Signal(
            "analytics_outbox",
            DEGRADED,
            f"{health['stuck']} events gave up.",
            metrics=health,
        )
    if age is not None and age > constants.OPS_SIGNAL_STALE_SECONDS:
        return Signal(
            "analytics_outbox",
            DEGRADED,
            "Events are not being delivered.",
            metrics=health,
        )
    return Signal(
        "analytics_outbox", OK, f"{health['pending']} waiting.", metrics=health
    )


async def _monitoring() -> Signal:
    """Is anything outside this process watching? The app can only tell
    whether it is configured to report; it says so rather than guessing."""
    from api.observability import sentry

    configured = {
        "sentry": sentry.enabled(),
        "posthog": bool(constants.POSTHOG_API_KEY),
    }
    missing = [name for name, on in configured.items() if not on]
    if missing:
        return Signal(
            "monitoring",
            NOT_CONFIGURED,
            "Not reporting to: " + ", ".join(missing) + ". CloudWatch alarms are "
            "configured in AWS and cannot be seen from here.",
            metrics=configured,
        )
    return Signal(
        "monitoring",
        OK,
        "Sentry and PostHog configured. CloudWatch alarms are checked by "
        "scripts/check_infra.py --aws.",
        metrics=configured,
    )


async def _cost_stop() -> Signal:
    from api.services.ops import cost_stop

    state = await cost_stop.status()
    if not state["enabled"]:
        return Signal(
            "cost_stop", NOT_CONFIGURED, "Cost stop is switched off.", metrics=state
        )
    if state["platform"] or state["organizations"]:
        return Signal("cost_stop", DEGRADED, "A cost stop is engaged.", metrics=state)
    if not state["configured"]:
        return Signal(
            "cost_stop",
            NOT_CONFIGURED,
            "No spend ceiling is set; nothing is watched.",
            metrics=state,
        )
    return Signal("cost_stop", OK, "Spend is under its ceilings.", metrics=state)


async def _laya() -> Signal:
    from api.services.ops import laya_eval

    state = laya_eval.breaker_state()
    if not constants.LAYA_URL:
        return Signal(
            "laya", NOT_CONFIGURED, "No decision model configured; rules decide."
        )
    if state["rolled_back"]:
        return Signal("laya", DEGRADED, "Rolled back: rules decide.", metrics=state)
    if state["open"]:
        return Signal(
            "laya",
            DEGRADED,
            "Circuit open: rules decide until it cools.",
            metrics=state,
        )
    return Signal("laya", OK, f"Laya routing: {constants.LAYA_ROUTING}.", metrics=state)


PROBES: tuple[tuple[str, Callable[[], Awaitable[Signal]]], ...] = (
    ("database", _database),
    ("postgres_pool", _pool),
    ("redis", _redis),
    ("queue", _queue),
    ("worker", _worker),
    ("backups", _backups),
    ("restore_drill", _restore_drill),
    ("capacity_review", _capacity_review),
    ("analytics_outbox", _analytics),
    ("monitoring", _monitoring),
    ("cost_stop", _cost_stop),
    ("laya", _laya),
)


def overall(signals: dict[str, Signal]) -> str:
    """ok only when every core signal is ok. A missing core signal is
    unknown -- absence is not health."""
    core = [
        signals[name].status if name in signals else UNKNOWN for name in CORE_SIGNALS
    ]
    if DOWN in core:
        return DOWN
    if DEGRADED in core:
        return DEGRADED
    if any(state != OK for state in core):
        return UNKNOWN
    return OK


async def snapshot() -> dict[str, Any]:
    from api.services.system_status import build_info

    results = await asyncio.gather(*(_timed(name, fn) for name, fn in PROBES))
    signals = {signal.name: signal for signal in results}
    return {
        "status": overall(signals),
        "observed_at": _now().isoformat(),
        "environment": constants.ENVIRONMENT,
        "build": build_info(),
        "core": list(CORE_SIGNALS),
        "signals": [signal.as_dict() for signal in results],
    }

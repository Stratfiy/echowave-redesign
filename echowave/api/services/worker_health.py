"""Whether the background worker is still running.

`start_services_docker.sh` starts uvicorn, the ARQ workers, the ARI manager and
the campaign orchestrator in **one container**. There is no separate worker to
deploy, which is convenient, and it has one sharp consequence: when the ARQ
worker dies the API keeps answering 200 on every endpoint while

* completed calls stop being costed,
* the billing rollups the whole dashboard reads stop being refreshed,
* monthly tax invoices stop being issued,
* the nightly retention purge and database backup stop running,

and nothing anywhere reports a problem. The dashboard does not go blank — it
serves the last numbers it had, which is worse, because a quiet morning and a
dead worker look the same.

The absence of work is not evidence of a dead worker: a night with no calls
produces no costing either. So the worker states, positively and on a fixed
schedule, that it is alive. The heartbeat is one Redis key holding the time of
the last beat.

**The TTL is deliberately much longer than the staleness threshold.** An
expiring key would answer "the worker is gone" and destroy the more useful
answer — *when* it stopped — which is the first thing anyone debugging asks and
the only way to line the failure up against a deploy or an OOM kill.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from api.constants import (
    REDIS_URL,
    WORKER_HEARTBEAT_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_STALE_AFTER_SECONDS,
)

#: Single key, not one per worker: with `max_jobs` concurrency inside one ARQ
#: process there is exactly one worker process per container, and the question
#: being answered is "is background work happening at all".
HEARTBEAT_KEY = "decibyl:worker:heartbeat"

#: A day. Long enough to still be able to say when the worker stopped, short
#: enough that a key from a decommissioned deployment does not live forever.
HEARTBEAT_TTL_SECONDS = 86_400


async def record_heartbeat(*, now: datetime | None = None) -> None:
    """Record that the background worker is alive, from inside the worker.

    Never raises. A heartbeat that could take the worker down would be a
    liveness probe that causes the outage it reports.
    """
    stamp = (now or datetime.now(UTC)).isoformat()
    redis = None
    try:
        redis = aioredis.from_url(REDIS_URL)
        await redis.set(HEARTBEAT_KEY, stamp, ex=HEARTBEAT_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Could not record worker heartbeat: {}", exc)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass


async def worker_health(*, now: datetime | None = None) -> dict[str, Any]:
    """How long since the background worker last said it was alive.

    Returns a dict rather than raising, because every caller is a health or
    readiness surface and a probe that throws takes down the screen that would
    have told you what was wrong.

    ``alive`` is tri-state on purpose:

    * ``True``  — beat within the staleness window.
    * ``False`` — beat recorded, but too long ago. The worker died, and
      ``last_seen`` says when.
    * ``None``  — no beat on record, or Redis could not be reached. Not the
      same finding as a dead worker: a deployment that has never run the
      heartbeat and one whose worker stopped need different responses, and
      collapsing them would send an operator hunting a worker that was never
      started.
    """
    now = now or datetime.now(UTC)
    redis = None
    try:
        redis = aioredis.from_url(REDIS_URL)
        raw = await redis.get(HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Could not read worker heartbeat: {}", exc)
        return {
            "alive": None,
            "last_seen": None,
            "age_seconds": None,
            "stale_after_seconds": WORKER_HEARTBEAT_STALE_AFTER_SECONDS,
            "detail": "Redis is unreachable, so worker liveness is unknown.",
        }
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass

    if not raw:
        return {
            "alive": None,
            "last_seen": None,
            "age_seconds": None,
            "stale_after_seconds": WORKER_HEARTBEAT_STALE_AFTER_SECONDS,
            "detail": (
                "No heartbeat on record. Expected for the first minute after a "
                "deployment; after that the worker has not started."
            ),
        }

    stamp = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        last_seen = datetime.fromisoformat(stamp)
    except ValueError:
        return {
            "alive": None,
            "last_seen": None,
            "age_seconds": None,
            "stale_after_seconds": WORKER_HEARTBEAT_STALE_AFTER_SECONDS,
            "detail": f"Heartbeat value is not a timestamp: {stamp!r}",
        }

    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)

    age = (now - last_seen).total_seconds()
    alive = age <= WORKER_HEARTBEAT_STALE_AFTER_SECONDS

    return {
        "alive": alive,
        "last_seen": last_seen.isoformat(),
        # Negative ages are clamped: a small clock skew between the worker and
        # the API would otherwise render as "last seen in 3 seconds".
        "age_seconds": round(max(age, 0.0), 1),
        "stale_after_seconds": WORKER_HEARTBEAT_STALE_AFTER_SECONDS,
        "interval_seconds": WORKER_HEARTBEAT_INTERVAL_SECONDS,
        "detail": (
            "Background worker is running."
            if alive
            else (
                f"No heartbeat for {round(max(age, 0.0))}s. Calls are not being "
                "costed, rollups are not refreshing and scheduled jobs are not "
                "running, while the API keeps answering normally."
            )
        ),
    }

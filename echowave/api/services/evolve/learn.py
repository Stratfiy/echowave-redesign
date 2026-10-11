"""The offline pass: collect, propose, gate, offer, watch.

Run by the ARQ job ``evolve_skills_tick`` (tasks/evolve_skills.py), once an
hour, never inside a conversation: learning is a background job with its own
budget, not something a caller waits on. Per workspace with the flag on:

1. record the runs that finished since the last look, and the cards people
   turned down or edited (``experience``) -- idempotent, so the overlap
   between passes costs nothing;
2. propose lessons from the new failures and corrections, gate each one,
   and offer the ones that pass (``lessons``);
3. watch the versions already promoted (``monitor``).

One pass per workspace at a time, held by a short Redis lock, so two
workers never propose the same lesson twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import evolve
from api.services.evolve import evaluate, experience, lessons, monitor

#: How far back each pass looks for finished runs. Longer than the interval,
#: so a pass that failed is covered by the next one.
LOOKBACK = timedelta(hours=48)
LOCK_SECONDS = 15 * 60


async def _lock(organization_id: int) -> bool:
    """True when this pass may run for this workspace. Without Redis it
    runs: every write it makes is idempotent or compare-and-swap anyway."""
    import redis.asyncio as aioredis

    from api import constants

    try:
        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
        return bool(
            await client.set(
                f"evolve:pass:{organization_id}", "1", ex=LOCK_SECONDS, nx=True
            )
        )
    except Exception as exc:  # noqa: BLE001 - without the lock, still safe
        logger.warning("evolve: lock unavailable: {}", exc)
        return True


async def _unlock(organization_id: int) -> None:
    import redis.asyncio as aioredis

    from api import constants

    try:
        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
        await client.delete(f"evolve:pass:{organization_id}")
    except Exception:  # noqa: BLE001 - the lock expires on its own
        pass


async def run_for(
    organization_id: int,
    *,
    now: datetime | None = None,
    runner: evaluate.Runner | None = None,
) -> dict[str, Any]:
    """One pass for one workspace. Returns what it did."""
    done: dict[str, Any] = {"recorded": 0, "cards": 0, "versions": [], "watched": 0}
    if not evolve.enabled(organization_id):
        return done
    if not await _lock(organization_id):
        return done
    try:
        since = (now or datetime.now(UTC)) - LOOKBACK
        for run in await db_client.finished_runs_since(
            organization_id=organization_id, since=since
        ):
            done["recorded"] += await experience.collect_run(organization_id, run.id)
        done["cards"] = await experience.collect_cards(organization_id)
        done["versions"] = await lessons.propose(organization_id, runner=runner)
        done["watched"] = await monitor.check(organization_id)
    finally:
        await _unlock(organization_id)
    return done


async def tick() -> int:
    """Every workspace that has skills and the flag on. Returns how many
    were passed over."""
    from api.services import features

    if not features.on_anywhere(evolve.FLAG):
        return 0
    passed = 0
    for organization_id in await db_client.organisations_with_skills():
        if not evolve.enabled(organization_id):
            continue
        try:
            await run_for(organization_id)
            passed += 1
        except Exception as exc:  # noqa: BLE001 - one workspace is not the rest
            logger.warning("evolve: pass for {} failed: {}", organization_id, exc)
    return passed


__all__ = ["LOOKBACK", "run_for", "tick"]

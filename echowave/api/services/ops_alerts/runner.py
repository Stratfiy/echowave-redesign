"""Run the detectors and fold what they find into incidents.

Two callers:

* the worker's five-minute job (``tasks/ops_alerts.run_ops_alerts``) runs
  every detector;
* the API's watchdog (``watchdog``) runs ``jobs`` every minute, because a
  job inside the worker cannot report that the worker has stopped.

Whichever reaches a detector first in ``EVALUATION_LOCK_SECONDS`` evaluates
it; the other skips, so two processes never fold the same detector at once.
One detector failing is logged and recorded as "could not measure" -- its
incidents are left alone -- and the rest still run.

Everything here is a no-op while ``ops_alerts`` is off.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from api.db import db_client
from api.services import features
from api.services.ops_alerts import detectors, incidents, store
from api.services.ops_alerts.incidents import Evaluation
from api.services.ops_alerts.thresholds import EVALUATION_LOCK_SECONDS

FLAG = "ops_alerts"
ALL = ("calls", "providers", "jobs", "spend", "invites")
#: Each detector, and the evaluation names it reports under (what it can
#: resolve if it fails to measure: nothing).
REPORTS = {
    "calls": ("calls",),
    "providers": ("providers",),
    "jobs": ("jobs.worker", "jobs.ticks"),
    "spend": ("spend",),
    "invites": ("invites",),
}


async def _run(name: str, now: datetime, client) -> list[Evaluation]:
    if name == "providers":
        return [await detectors.detect_providers(now=now, client=client)]
    if name == "jobs":
        return await detectors.detect_jobs(now=now, client=client)
    async with db_client.async_session() as session:
        if name == "calls":
            return [await detectors.detect_calls(session, now=now)]
        if name == "spend":
            return [await detectors.detect_spend(session, now=now, client=client)]
        if name == "invites":
            return [await detectors.detect_invites(session, now=now)]
    raise ValueError(f"unknown detector {name!r}")


async def evaluate(
    names: tuple[str, ...] = ALL,
    *,
    now: datetime | None = None,
    client=None,
    lock: bool = True,
    run: Callable[[str, datetime, Any], Awaitable[list[Evaluation]]] | None = None,
) -> dict[str, Any]:
    """Evaluate ``names``. Returns, per detector, its findings and actions."""
    if not features.is_on(FLAG):
        return {"skipped": "flag_off"}
    now = now or datetime.now(UTC)
    run = run or _run
    out: dict[str, Any] = {}
    async with store.connection(client) as redis:
        for name in names:
            if lock and not await redis.set(
                store.key("eval", name),
                now.isoformat(),
                nx=True,
                ex=EVALUATION_LOCK_SECONDS,
            ):
                out[name] = {"skipped": "evaluated_elsewhere"}
                continue
            try:
                evaluations = await run(name, now, redis)
            except Exception as exc:  # noqa: BLE001 - one detector must not stop the rest
                logger.exception(
                    "ops_alerts: detector {} failed: {}", name, type(exc).__name__
                )
                evaluations = [
                    Evaluation(
                        report, ok=False, note=f"{type(exc).__name__} while measuring"
                    )
                    for report in REPORTS[name]
                ]
            results = []
            for evaluation in evaluations:
                actions = await incidents.apply(evaluation, now=now, client=redis)
                results.append(
                    {
                        "detector": evaluation.detector,
                        "ok": evaluation.ok,
                        "firing": [f.key for f in evaluation.findings],
                        "actions": actions,
                    }
                )
            out[name] = results
    return out

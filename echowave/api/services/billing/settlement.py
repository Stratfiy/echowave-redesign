"""Backstop for calls that finished but were never costed.

``cost_completed_workflow_run`` has one production caller: the ARQ job enqueued
as the last statement of the pipeline teardown handler. That is a single point
of failure with no retry behind it, and every way it fails is silent:

* teardown raises before the enqueue — pipecat swallows handler exceptions, so
  the call simply ends and nothing is scheduled;
* the enqueue itself fails, because Redis blipped;
* costing raises inside the job, which catches and logs it. ARQ then marks the
  job *succeeded*, so it is never retried.

In all three the call happened, we paid the providers, and no receipt exists.
Nothing detected it either: before this module there was no query anywhere in
the codebase for ``costed_at IS NULL``. Worse, the reservation sweeper releases
the hold on any run past its age cut-off whether or not it was ever costed, so
the money is handed back and the call stays free.

This closes the loop. It is deliberately dumb: find completed runs with no
receipt that are old enough to be genuinely finished, and cost them.
``cost_workflow_run`` already skips anything with a ``costed_at``, so running
this against a healthy deployment is a no-op and running it twice is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.billing.costing import cost_workflow_run

#: How long after a call ends before an uncosted run is treated as missed.
#:
#: The completion job normally runs within seconds. This is long enough that a
#: merely slow queue is not swept up as a failure, and short enough that the
#: gap is found while the reservation still stands — the reservation sweeper
#: releases holds at RESERVATION_MAX_AGE_MINUTES (180), and a run costed after
#: its hold is gone still bills correctly but spends money the account was no
#: longer holding.
UNCOSTED_GRACE_MINUTES = 15

#: Ceiling per sweep. A backlog is drained across several runs rather than in
#: one transaction holding hundreds of rows: each costing is independent, and
#: one poisonous run must not roll back the ones that succeeded.
MAX_RUNS_PER_SWEEP = 200


async def find_uncosted_run_ids(
    *, now: datetime | None = None, limit: int = MAX_RUNS_PER_SWEEP
) -> list[int]:
    """Completed runs with no receipt, oldest first.

    Ordered oldest first so a persistent backlog drains in the order the calls
    happened, and so the same head-of-queue rows are retried each sweep rather
    than a random sample.

    ``ended_at IS NOT NULL`` is doing two jobs. It is the clock the grace period
    is measured from, and it is also what scopes this sweep to calls — the
    text-chat path marks a run ``is_completed`` but never stamps ``ended_at``,
    so text sessions are not swept up here.

    That exclusion is deliberate and must stay deliberate. Text chat is not
    costed by any path today, and the public embed route that drives it has no
    quota check at all: billing it from here would debit accounts that were
    never asked whether they could afford it, and would take balances negative
    rather than refusing the work. Text chat needs its own completion enqueue
    *and* an authorisation call before it is billed. If you give text-chat runs
    an ``ended_at``, fix that first — this sweep will start charging for them
    the moment you do.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=UNCOSTED_GRACE_MINUTES)

    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(WorkflowRunModel.id)
                .where(
                    WorkflowRunModel.is_completed.is_(True),
                    WorkflowRunModel.costed_at.is_(None),
                    WorkflowRunModel.ended_at.is_not(None),
                    WorkflowRunModel.ended_at < cutoff,
                )
                .order_by(WorkflowRunModel.ended_at)
                .limit(limit)
            )
        ).all()

    return [int(row) for row in rows]


async def settle_uncosted_runs(
    *, now: datetime | None = None, limit: int = MAX_RUNS_PER_SWEEP
) -> tuple[int, int]:
    """Cost every completed run that has no receipt.

    Returns (costed, failed).

    Each run gets its own session and its own transaction. A run that cannot be
    costed — a rate that no longer resolves, a malformed usage_info — must not
    prevent the rest of the backlog from settling, and must not be retried in a
    tight loop either: leaving ``costed_at`` unset means the next sweep picks it
    up again, which is the behaviour we want for a transient fault and merely
    noisy for a permanent one. A run that fails every sweep is a real defect and
    the log line below is how it surfaces.
    """
    run_ids = await find_uncosted_run_ids(now=now, limit=limit)
    if not run_ids:
        return (0, 0)

    logger.warning(
        "Found {} completed run(s) with no receipt; costing them now. Each one "
        "is a call whose providers we paid and whose customer we did not bill.",
        len(run_ids),
    )

    costed = 0
    failed = 0
    for run_id in run_ids:
        try:
            async with db_client.async_session() as session:
                cost = await cost_workflow_run(session, run_id)
                if cost is None:
                    # Already costed by the normal path between the query and
                    # now, or nothing billable on it. Either way, not a failure.
                    continue
                await session.commit()
            costed += 1
        except Exception as error:  # noqa: BLE001 - one bad run must not stop the sweep
            failed += 1
            logger.error(
                "Backstop could not cost workflow run {}: {}. It stays uncosted "
                "and will be retried on the next sweep.",
                run_id,
                error,
            )

    logger.info(
        "Settlement backstop: {} run(s) costed, {} failed, out of {} found",
        costed,
        failed,
        len(run_ids),
    )
    return (costed, failed)

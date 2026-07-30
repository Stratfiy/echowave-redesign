"""Numbers that say whether the privacy controls are actually working.

Every control built here reports success by not raising. That is the wrong kind
of quiet: a retention sweep that silently stopped running looks identical to one
with nothing to do, and the first anyone hears of it is a regulator asking how
long recordings are kept. So these are the measurements, and the headline one is
designed to be **zero**:

    overdue recordings — audio that has outlived its retention window and is
    still in storage

Zero is the healthy value, and any other number is an incident. A dashboard full
of numbers that go up is easy to build; the useful one here is a number that
should never move.

No competitor in this market reports any of this. It is also the first thing an
enterprise security review asks for, which makes it the cheapest differentiator
on the list — the data was already being written by the access log and the
erasure register.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    DataAccessLogModel,
    ErasureRequestModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy.retention import PURGED_MARKER, resolve_policy

#: How long a data principal's erasure request may take before it is late.
#: GDPR Art 12(3) sets one month; the DPDP Rules set their own periods. One
#: month is the tighter of the two commonly cited, so it is the one measured.
ERASURE_DEADLINE_DAYS = 30


async def overdue_recordings(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    sample_limit: int = 20,
) -> dict:
    """Recordings past their retention window that still exist.

    **Should always be zero.** Any other number means the nightly sweep is not
    running, or is failing on the objects it cannot delete — in which case the
    row keeps its pointer deliberately, so those runs are exactly the ones that
    surface here.

    Counted per account against that account's own window, because a customer
    who set 7 days is overdue four times sooner than one on the 90-day default.
    """
    now = now or datetime.now(UTC)

    rows = (
        await session.execute(
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.created_at,
                WorkflowModel.organization_id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowRunModel.recording_url.is_not(None),
                WorkflowRunModel.recording_url != PURGED_MARKER,
            )
            .order_by(WorkflowRunModel.created_at)
        )
    ).all()

    policies: dict[int, int] = {}
    overdue: list[dict] = []
    oldest_age = 0
    total = 0

    for run_id, created_at, organization_id in rows:
        if organization_id is None or created_at is None:
            continue
        if organization_id not in policies:
            policy = await resolve_policy(session, organization_id=organization_id)
            policies[organization_id] = policy.recording_days
        window = policies[organization_id]
        age_days = (now - created_at).days
        if age_days < window:
            continue

        total += 1
        oldest_age = max(oldest_age, age_days)
        # A sample, not the whole set: a healthy deployment returns none, and an
        # unhealthy one could return millions, which is a report nobody can read
        # and a response nobody should have to serialise.
        if len(overdue) < sample_limit:
            overdue.append(
                {
                    "workflow_run_id": run_id,
                    "organization_id": organization_id,
                    "age_days": age_days,
                    "retention_days": window,
                }
            )

    return {
        "overdue": total,
        "healthy": total == 0,
        "oldest_overdue_days": oldest_age or None,
        "sample": overdue,
        "checked": len(rows),
    }


async def access_summary(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    organization_id: int | None = None,
) -> dict:
    """Who reached personal data in a window, and how.

    The split that matters is authenticated versus not. Access through a public
    share link carries no user id — it is the case least visible in ordinary
    logs and the most worth watching, so it is counted on its own rather than
    folded into a total.
    """
    conditions = [
        DataAccessLogModel.created_at >= start,
        DataAccessLogModel.created_at <= end,
    ]
    if organization_id is not None:
        conditions.append(DataAccessLogModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                DataAccessLogModel.resource_type,
                DataAccessLogModel.actor_kind,
                func.count().label("n"),
                func.count(func.distinct(DataAccessLogModel.user_id)).label("users"),
            )
            .where(*conditions)
            .group_by(DataAccessLogModel.resource_type, DataAccessLogModel.actor_kind)
        )
    ).all()

    by_resource: dict[str, int] = {}
    unauthenticated = 0
    total = 0
    for resource_type, actor_kind, n, _users in rows:
        by_resource[resource_type] = by_resource.get(resource_type, 0) + int(n)
        total += int(n)
        if actor_kind == "public_token":
            unauthenticated += int(n)

    distinct_users = await session.scalar(
        select(func.count(func.distinct(DataAccessLogModel.user_id))).where(
            *conditions, DataAccessLogModel.user_id.is_not(None)
        )
    )

    return {
        "total": total,
        "by_resource_type": by_resource,
        "exports": by_resource.get("export", 0),
        "unauthenticated": unauthenticated,
        "distinct_users": int(distinct_users or 0),
    }


async def erasure_turnaround(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    organization_id: int | None = None,
) -> dict:
    """How long erasure requests take, against the statutory deadline.

    Both regimes impose a deadline and neither accepts "we did it eventually".
    Without this number an operator can say requests are honoured but cannot say
    they were honoured in time, which is the part that is actually audited.
    """
    now = now or datetime.now(UTC)
    deadline = timedelta(days=ERASURE_DEADLINE_DAYS)

    conditions = []
    if organization_id is not None:
        conditions.append(ErasureRequestModel.organization_id == organization_id)

    rows = (await session.scalars(select(ErasureRequestModel).where(*conditions))).all()

    completed_hours: list[float] = []
    open_requests = 0
    breached = 0

    for row in rows:
        requested = row.requested_at
        completed = row.completed_at
        if requested is None:
            continue
        if completed is not None:
            completed_hours.append((completed - requested).total_seconds() / 3600)
            if completed - requested > deadline:
                breached += 1
        else:
            open_requests += 1
            if now - requested > deadline:
                breached += 1

    completed_hours.sort()
    median = completed_hours[len(completed_hours) // 2] if completed_hours else None

    return {
        "total": len(rows),
        "completed": len(completed_hours),
        "open": open_requests,
        "past_deadline": breached,
        "deadline_days": ERASURE_DEADLINE_DAYS,
        "median_hours": round(median, 1) if median is not None else None,
        "slowest_hours": round(completed_hours[-1], 1) if completed_hours else None,
    }


async def snapshot(
    session: AsyncSession,
    *,
    window_days: int = 30,
    organization_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Everything above, for one panel."""
    now = now or datetime.now(UTC)
    return {
        "window_days": window_days,
        "retention": await overdue_recordings(session, now=now),
        "access": await access_summary(
            session,
            start=now - timedelta(days=window_days),
            end=now,
            organization_id=organization_id,
        ),
        "erasure": await erasure_turnaround(
            session, now=now, organization_id=organization_id
        ),
    }

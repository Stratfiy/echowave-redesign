"""Daily per-account rollups backing the dashboard.

Dashboard pages read from ``daily_organization_rollup`` rather than scanning
``workflow_runs``, which is what keeps them inside the performance budget at a
million calls. Raw tables stay for drill-down.

**Days are IST calendar days.** Timestamps are stored in UTC everywhere, but
bucketing by UTC day would split an Indian working day across two rows and make
every daily figure look wrong to us. The conversion happens in SQL via
``AT TIME ZONE``, so the grouping and the filtering agree by construction.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    DailyOrganizationRollupModel,
    WorkflowModel,
    WorkflowRunModel,
)

IST = ZoneInfo("Asia/Kolkata")

# Postgres name for the display timezone; must match IST above.
_IST_SQL = "Asia/Kolkata"


def ist_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """UTC half-open range ``[start, end)`` covering one IST calendar day."""
    start_ist = datetime(day.year, day.month, day.day, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return start_ist.astimezone(ZoneInfo("UTC")), end_ist.astimezone(ZoneInfo("UTC"))


def _ist_date_expr():
    """SQL expression for the IST calendar date of a run.

    ``created_at`` is ``timestamptz``; ``AT TIME ZONE 'Asia/Kolkata'`` converts
    it to local wall-clock time, which is then truncated to a date.
    """
    return func.date(func.timezone(_IST_SQL, WorkflowRunModel.created_at))


async def refresh_daily_rollup(
    session: AsyncSession,
    *,
    day: date,
    organization_id: int | None = None,
) -> int:
    """Recompute rollup rows for one IST day. Returns rows written.

    Recomputes from scratch rather than incrementing, so a re-run after a
    late-arriving or recosted call converges on the right answer instead of
    double-counting.
    """
    start_utc, end_utc = ist_day_bounds_utc(day)

    conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
        WorkflowModel.organization_id.isnot(None),
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    billable = func.coalesce(WorkflowRunModel.billable_seconds, 0)

    rows = (
        await session.execute(
            select(
                WorkflowModel.organization_id.label("organization_id"),
                func.count().label("calls"),
                func.count(WorkflowRunModel.answered_at).label("answered_calls"),
                func.sum(
                    case((WorkflowRunModel.is_completed.is_(True), 1), else_=0)
                ).label("completed_calls"),
                func.coalesce(func.sum(billable), 0).label("billable_seconds"),
                # Billable minutes are ceilinged per call and then summed —
                # never summed as seconds and ceilinged once. A 30-second call
                # bills a whole minute.
                func.coalesce(func.sum(func.ceil(billable / 60.0)), 0).label(
                    "billable_minutes"
                ),
                func.coalesce(
                    func.sum(
                        func.coalesce(WorkflowRunModel.total_provider_cost_paise, 0)
                    ),
                    0,
                ).label("provider_cost_paise"),
                func.coalesce(
                    func.sum(func.coalesce(WorkflowRunModel.total_charged_paise, 0)), 0
                ).label("charged_paise"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(WorkflowModel.organization_id)
        )
    ).all()

    delete_stmt = delete(DailyOrganizationRollupModel).where(
        DailyOrganizationRollupModel.day == day
    )
    if organization_id is not None:
        delete_stmt = delete_stmt.where(
            DailyOrganizationRollupModel.organization_id == organization_id
        )
    await session.execute(delete_stmt)

    for row in rows:
        charged = int(row.charged_paise or 0)
        provider_cost = int(row.provider_cost_paise or 0)
        session.add(
            DailyOrganizationRollupModel(
                organization_id=row.organization_id,
                day=day,
                calls=int(row.calls or 0),
                answered_calls=int(row.answered_calls or 0),
                completed_calls=int(row.completed_calls or 0),
                billable_seconds=int(row.billable_seconds or 0),
                billable_minutes=int(row.billable_minutes or 0),
                provider_cost_paise=provider_cost,
                charged_paise=charged,
                margin_paise=charged - provider_cost,
            )
        )

    await session.flush()
    logger.info("Refreshed daily rollup for {}: {} account(s)", day, len(rows))
    return len(rows)


async def refresh_recent_rollups(
    session: AsyncSession, *, days: int = 3, today: date | None = None
) -> int:
    """Refresh the last ``days`` IST days.

    A trailing window rather than only today, because a call can be costed
    after its own day has rolled over.
    """
    anchor = today or datetime.now(IST).date()
    written = 0
    for offset in range(days):
        written += await refresh_daily_rollup(
            session, day=anchor - timedelta(days=offset)
        )
    return written

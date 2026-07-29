"""Read queries backing the admin billing dashboard.

Headline figures come from ``daily_organization_rollup`` rather than scanning
``workflow_runs``, which is what keeps pages inside the performance budget at a
million calls. Raw tables are only touched for drill-down, always bounded by a
time range and, where relevant, one account.

Percentiles are computed in SQL with ``percentile_cont`` over raw
``call_turn_metrics`` rows — never averaged from pre-aggregated buckets, which
would give wrong answers.

Every query here is cross-account by design: this module is only reachable from
staff-gated routes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CallCostItemModel,
    CallTurnMetricModel,
    CampaignModel,
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE
from api.services.billing.rollup import IST, ist_day_bounds_utc

R = DailyOrganizationRollupModel


def _sum(column):
    return func.coalesce(func.sum(column), 0)


async def overview_totals(session: AsyncSession, *, start: date, end: date) -> dict:
    """Headline figures for an inclusive IST day range."""
    row = (
        await session.execute(
            select(
                _sum(R.charged_paise).label("revenue_paise"),
                _sum(R.provider_cost_paise).label("provider_cost_paise"),
                _sum(R.margin_paise).label("margin_paise"),
                _sum(R.billable_minutes).label("billable_minutes"),
                _sum(R.calls).label("calls"),
                _sum(R.answered_calls).label("answered_calls"),
                _sum(R.completed_calls).label("completed_calls"),
                func.count(func.distinct(R.organization_id)).label("active_accounts"),
            ).where(R.day >= start, R.day <= end)
        )
    ).one()

    revenue = int(row.revenue_paise or 0)
    margin = int(row.margin_paise or 0)
    return {
        "revenue_paise": revenue,
        "provider_cost_paise": int(row.provider_cost_paise or 0),
        "margin_paise": margin,
        # None rather than 0 when there is no revenue: a margin percentage of a
        # zero base is undefined, and showing "0%" would read as "no margin".
        "margin_pct": (margin / revenue) if revenue else None,
        "billable_minutes": int(row.billable_minutes or 0),
        "calls": int(row.calls or 0),
        "answered_calls": int(row.answered_calls or 0),
        "completed_calls": int(row.completed_calls or 0),
        "active_accounts": int(row.active_accounts or 0),
    }


async def daily_series(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> list[dict]:
    """Per-day totals across the range, zero-filled so charts have no gaps."""
    conditions = [R.day >= start, R.day <= end]
    if organization_id is not None:
        conditions.append(R.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                R.day,
                _sum(R.calls).label("calls"),
                _sum(R.billable_minutes).label("billable_minutes"),
                _sum(R.charged_paise).label("charged_paise"),
                _sum(R.provider_cost_paise).label("provider_cost_paise"),
                _sum(R.margin_paise).label("margin_paise"),
            )
            .where(*conditions)
            .group_by(R.day)
            .order_by(R.day)
        )
    ).all()

    by_day = {r.day: r for r in rows}
    series: list[dict] = []
    cursor = start
    while cursor <= end:
        row = by_day.get(cursor)
        series.append(
            {
                "day": cursor.isoformat(),
                "calls": int(row.calls) if row else 0,
                "billable_minutes": int(row.billable_minutes) if row else 0,
                "charged_paise": int(row.charged_paise) if row else 0,
                "provider_cost_paise": int(row.provider_cost_paise) if row else 0,
                "margin_paise": int(row.margin_paise) if row else 0,
            }
        )
        cursor += timedelta(days=1)
    return series


async def cost_composition_series(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> list[dict]:
    """Per-day cost split by component, for the stacked-area chart.

    This one has to hit ``call_cost_items``: the rollup stores provider cost as
    a single figure, and the whole point of the chart is the breakdown. It is
    bounded by the same time range and grouped in SQL.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    day_expr = func.date(func.timezone("Asia/Kolkata", WorkflowRunModel.created_at))
    conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                day_expr.label("day"),
                CallCostItemModel.component,
                _sum(CallCostItemModel.cost_paise).label("cost_paise"),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(day_expr, CallCostItemModel.component)
            .order_by(day_expr)
        )
    ).all()

    by_day: dict[str, dict] = {}
    cursor = start
    while cursor <= end:
        by_day[cursor.isoformat()] = {
            "day": cursor.isoformat(),
            "stt": 0,
            "llm": 0,
            "tts": 0,
            "telephony": 0,
            "platform": 0,
        }
        cursor += timedelta(days=1)

    for row in rows:
        key = row.day.isoformat()
        if key in by_day and row.component in by_day[key]:
            by_day[key][row.component] = int(row.cost_paise or 0)

    return list(by_day.values())


async def top_accounts(
    session: AsyncSession, *, start: date, end: date, limit: int = 10
) -> list[dict]:
    rows = (
        await session.execute(
            select(
                OrganizationModel.id,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                _sum(R.charged_paise).label("revenue_paise"),
                _sum(R.margin_paise).label("margin_paise"),
            )
            .join(OrganizationModel, OrganizationModel.id == R.organization_id)
            .where(R.day >= start, R.day <= end)
            .group_by(OrganizationModel.id)
            .order_by(desc("revenue_paise"))
            .limit(limit)
        )
    ).all()
    return [
        {
            "organization_id": r.id,
            "name": r.billing_name or r.provider_id,
            "revenue_paise": int(r.revenue_paise or 0),
            "margin_paise": int(r.margin_paise or 0),
        }
        for r in rows
    ]


async def latency_series(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
    language: str | None = None,
) -> list[dict]:
    """Daily p50/p95 perceived latency, computed in SQL over raw turn rows."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    day_expr = func.date(func.timezone("Asia/Kolkata", CallTurnMetricModel.created_at))
    conditions = [
        CallTurnMetricModel.created_at >= start_utc,
        CallTurnMetricModel.created_at < end_utc,
        CallTurnMetricModel.latency_ms.isnot(None),
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)
    if language:
        conditions.append(WorkflowRunModel.language == language)

    rows = (
        await session.execute(
            select(
                day_expr.label("day"),
                func.percentile_cont(0.5)
                .within_group(CallTurnMetricModel.latency_ms)
                .label("p50"),
                func.percentile_cont(0.95)
                .within_group(CallTurnMetricModel.latency_ms)
                .label("p95"),
                func.count().label("turns"),
            )
            .select_from(CallTurnMetricModel)
            .join(
                WorkflowRunModel,
                CallTurnMetricModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()

    return [
        {
            "day": r.day.isoformat(),
            "p50_ms": int(r.p50) if r.p50 is not None else None,
            "p95_ms": int(r.p95) if r.p95 is not None else None,
            "turns": int(r.turns or 0),
        }
        for r in rows
    ]


async def latency_by_language(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> list[dict]:
    """p50/p95 split by language — where a slow language shows itself."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    conditions = [
        CallTurnMetricModel.created_at >= start_utc,
        CallTurnMetricModel.created_at < end_utc,
        CallTurnMetricModel.latency_ms.isnot(None),
        WorkflowRunModel.language.isnot(None),
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                WorkflowRunModel.language,
                func.percentile_cont(0.5)
                .within_group(CallTurnMetricModel.latency_ms)
                .label("p50"),
                func.percentile_cont(0.95)
                .within_group(CallTurnMetricModel.latency_ms)
                .label("p95"),
                func.count().label("turns"),
            )
            .select_from(CallTurnMetricModel)
            .join(
                WorkflowRunModel,
                CallTurnMetricModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(WorkflowRunModel.language)
            .order_by(desc("p50"))
        )
    ).all()

    return [
        {
            "language": r.language,
            "p50_ms": int(r.p50) if r.p50 is not None else None,
            "p95_ms": int(r.p95) if r.p95 is not None else None,
            "turns": int(r.turns or 0),
        }
        for r in rows
    ]


async def pipeline_stage_medians(
    session: AsyncSession, *, start: date, end: date
) -> dict:
    """Median duration of each pipeline stage — where the time goes."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)
    m = CallTurnMetricModel

    def median(expr):
        return func.percentile_cont(0.5).within_group(expr)

    row = (
        await session.execute(
            select(
                median(m.t_endpoint_fired_ms - m.t_user_stopped_ms).label(
                    "endpointing"
                ),
                median(m.t_stt_final_ms - m.t_endpoint_fired_ms).label("stt"),
                median(m.t_llm_first_token_ms - m.t_stt_final_ms).label("llm"),
                median(m.t_tts_first_byte_ms - m.t_llm_first_token_ms).label("tts"),
                median(m.t_audio_out_ms - m.t_tts_first_byte_ms).label("playback"),
            ).where(
                m.created_at >= start_utc,
                m.created_at < end_utc,
                m.t_audio_out_ms.isnot(None),
            )
        )
    ).one()

    return {
        "endpointing_ms": int(row.endpointing or 0),
        "stt_ms": int(row.stt or 0),
        "llm_ms": int(row.llm or 0),
        "tts_ms": int(row.tts or 0),
        "playback_ms": int(row.playback or 0),
    }


async def slowest_turns(
    session: AsyncSession, *, start: date, end: date, limit: int = 20
) -> list[dict]:
    """Slowest turns in the range, with a link back to the call."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    rows = (
        await session.execute(
            select(
                CallTurnMetricModel.workflow_run_id,
                CallTurnMetricModel.turn_index,
                CallTurnMetricModel.latency_ms,
                WorkflowRunModel.language,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                CallTurnMetricModel.created_at,
            )
            .select_from(CallTurnMetricModel)
            .join(
                WorkflowRunModel,
                CallTurnMetricModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .join(
                OrganizationModel, WorkflowModel.organization_id == OrganizationModel.id
            )
            .where(
                CallTurnMetricModel.created_at >= start_utc,
                CallTurnMetricModel.created_at < end_utc,
                CallTurnMetricModel.latency_ms.isnot(None),
            )
            .order_by(desc(CallTurnMetricModel.latency_ms))
            .limit(limit)
        )
    ).all()

    return [
        {
            "workflow_run_id": r.workflow_run_id,
            "turn_index": r.turn_index,
            "latency_ms": r.latency_ms,
            "language": r.language,
            "account": r.billing_name or r.provider_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def accounts_summary(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    account_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """One row per account for the accounts table, with the flag inputs."""
    usage = (
        select(
            R.organization_id.label("organization_id"),
            _sum(R.billable_minutes).label("billable_minutes"),
            _sum(R.charged_paise).label("charged_paise"),
            _sum(R.provider_cost_paise).label("provider_cost_paise"),
            _sum(R.margin_paise).label("margin_paise"),
            func.max(R.day).label("last_active_day"),
        )
        .where(R.day >= start, R.day <= end)
        .group_by(R.organization_id)
        .subquery()
    )

    balance = (
        select(
            CreditLedgerModel.organization_id.label("organization_id"),
            _sum(CreditLedgerModel.delta_paise).label("balance_paise"),
        )
        .group_by(CreditLedgerModel.organization_id)
        .subquery()
    )

    conditions = []
    if account_type:
        conditions.append(OrganizationModel.account_type == account_type)
    if status:
        conditions.append(OrganizationModel.billing_status == status)

    rows = (
        await session.execute(
            select(
                OrganizationModel.id,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                OrganizationModel.account_type,
                OrganizationModel.billing_status,
                OrganizationModel.platform_rate_mpaise,
                func.coalesce(usage.c.billable_minutes, 0).label("billable_minutes"),
                func.coalesce(usage.c.charged_paise, 0).label("charged_paise"),
                func.coalesce(usage.c.provider_cost_paise, 0).label(
                    "provider_cost_paise"
                ),
                func.coalesce(usage.c.margin_paise, 0).label("margin_paise"),
                usage.c.last_active_day,
                func.coalesce(balance.c.balance_paise, 0).label("balance_paise"),
            )
            .outerjoin(usage, usage.c.organization_id == OrganizationModel.id)
            .outerjoin(balance, balance.c.organization_id == OrganizationModel.id)
            .where(*conditions)
            .order_by(desc("charged_paise"))
        )
    ).all()

    return [
        {
            "organization_id": r.id,
            "name": r.billing_name or r.provider_id,
            "account_type": r.account_type,
            "status": r.billing_status,
            "billable_minutes": int(r.billable_minutes or 0),
            "revenue_paise": int(r.charged_paise or 0),
            "provider_cost_paise": int(r.provider_cost_paise or 0),
            "margin_paise": int(r.margin_paise or 0),
            "margin_pct": (
                (int(r.margin_paise) / int(r.charged_paise))
                if r.charged_paise
                else None
            ),
            "balance_paise": int(r.balance_paise or 0),
            "platform_rate_mpaise": r.platform_rate_mpaise
            or DEFAULT_PLATFORM_RATE_MPAISE,
            "platform_rate_is_override": r.platform_rate_mpaise is not None,
            "last_active_day": (
                r.last_active_day.isoformat() if r.last_active_day else None
            ),
        }
        for r in rows
    ]


async def account_detail(session: AsyncSession, *, organization_id: int) -> dict | None:
    org = await session.get(OrganizationModel, organization_id)
    if org is None:
        return None

    balance = await session.scalar(
        select(_sum(CreditLedgerModel.delta_paise)).where(
            CreditLedgerModel.organization_id == organization_id
        )
    )
    return {
        "organization_id": org.id,
        "name": org.billing_name or org.provider_id,
        "provider_id": org.provider_id,
        "account_type": org.account_type,
        "status": org.billing_status,
        "currency": org.billing_currency,
        "platform_rate_mpaise": org.platform_rate_mpaise
        or DEFAULT_PLATFORM_RATE_MPAISE,
        "platform_rate_is_override": org.platform_rate_mpaise is not None,
        "balance_paise": int(balance or 0),
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


async def account_rate_history(
    session: AsyncSession, *, organization_id: int
) -> list[dict]:
    rows = (
        await session.scalars(
            select(OrganizationRateHistoryModel)
            .where(OrganizationRateHistoryModel.organization_id == organization_id)
            .order_by(desc(OrganizationRateHistoryModel.effective_from))
        )
    ).all()
    return [
        {
            "id": r.id,
            "platform_rate_mpaise": r.platform_rate_mpaise,
            "effective_from": r.effective_from.isoformat()
            if r.effective_from
            else None,
            "effective_to": r.effective_to.isoformat() if r.effective_to else None,
            "set_by": r.set_by,
            "note": r.note,
        }
        for r in rows
    ]


async def credit_ledger(
    session: AsyncSession, *, organization_id: int, limit: int = 100
) -> list[dict]:
    rows = (
        await session.scalars(
            select(CreditLedgerModel)
            .where(CreditLedgerModel.organization_id == organization_id)
            .order_by(desc(CreditLedgerModel.created_at), desc(CreditLedgerModel.id))
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "delta_paise": r.delta_paise,
            "kind": r.kind,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
            "balance_after_paise": r.balance_after_paise,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def calls_page(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    organization_id: int | None = None,
    language: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    conditions = [
        WorkflowRunModel.created_at >= start,
        WorkflowRunModel.created_at < end,
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)
    if language:
        conditions.append(WorkflowRunModel.language == language)
    if direction:
        conditions.append(WorkflowRunModel.call_type == direction)
    if search:
        pattern = f"%{search}%"
        conditions.append(
            or_(
                WorkflowRunModel.name.ilike(pattern),
                cast(WorkflowRunModel.initial_context, func.text().type).ilike(pattern),
            )
        )

    base = (
        select(WorkflowRunModel, OrganizationModel)
        .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .join(OrganizationModel, WorkflowModel.organization_id == OrganizationModel.id)
        .where(*conditions)
    )

    total = await session.scalar(
        select(func.count())
        .select_from(WorkflowRunModel)
        .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
        .where(*conditions)
    )

    rows = (
        await session.execute(
            base.order_by(desc(WorkflowRunModel.created_at)).limit(limit).offset(offset)
        )
    ).all()

    return (
        [
            {
                "id": run.id,
                "name": run.name,
                "account": org.billing_name or org.provider_id,
                "organization_id": org.id,
                "language": run.language,
                "direction": run.call_type,
                "status": run.state,
                "disposition": (run.gathered_context or {}).get(
                    "mapped_call_disposition"
                ),
                "billable_seconds": run.billable_seconds or 0,
                "charged_paise": run.total_charged_paise or 0,
                "provider_cost_paise": run.total_provider_cost_paise or 0,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "answered": run.answered_at is not None,
            }
            for run, org in rows
        ],
        int(total or 0),
    )


async def call_detail(session: AsyncSession, *, workflow_run_id: int) -> dict | None:
    row = (
        await session.execute(
            select(WorkflowRunModel, OrganizationModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .join(
                OrganizationModel, WorkflowModel.organization_id == OrganizationModel.id
            )
            .where(WorkflowRunModel.id == workflow_run_id)
        )
    ).first()
    if row is None:
        return None
    run, org = row

    items = (
        await session.scalars(
            select(CallCostItemModel)
            .where(CallCostItemModel.workflow_run_id == workflow_run_id)
            .order_by(CallCostItemModel.component)
        )
    ).all()

    turns = (
        await session.scalars(
            select(CallTurnMetricModel)
            .where(CallTurnMetricModel.workflow_run_id == workflow_run_id)
            .order_by(CallTurnMetricModel.turn_index)
        )
    ).all()

    return {
        "id": run.id,
        "name": run.name,
        "account": org.billing_name or org.provider_id,
        "organization_id": org.id,
        "language": run.language,
        "direction": run.call_type,
        "status": run.state,
        "disposition": (run.gathered_context or {}).get("mapped_call_disposition"),
        "billable_seconds": run.billable_seconds or 0,
        "platform_rate_mpaise_applied": run.platform_rate_mpaise_applied,
        "provider_cost_paise": run.total_provider_cost_paise or 0,
        "charged_paise": run.total_charged_paise or 0,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "answered_at": run.answered_at.isoformat() if run.answered_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "recording_url": run.recording_url,
        "caller_number": (run.initial_context or {}).get("caller_number"),
        "called_number": (run.initial_context or {}).get("called_number"),
        "cost_items": [
            {
                "component": i.component,
                "provider": i.provider,
                "units": i.units,
                "unit_rate_mpaise": i.unit_rate_mpaise,
                "cost_paise": i.cost_paise,
            }
            for i in items
        ],
        "turns": [
            {
                "turn_index": t.turn_index,
                "endpointing_ms": (t.t_endpoint_fired_ms or 0)
                - (t.t_user_stopped_ms or 0),
                "stt_ms": (t.t_stt_final_ms or 0) - (t.t_endpoint_fired_ms or 0),
                "llm_ms": (t.t_llm_first_token_ms or 0) - (t.t_stt_final_ms or 0),
                "tts_ms": (t.t_tts_first_byte_ms or 0) - (t.t_llm_first_token_ms or 0),
                "latency_ms": t.latency_ms,
                "tool_called": t.tool_called,
                "tool_ms": t.tool_ms,
            }
            for t in turns
        ],
    }


async def campaigns_summary(session: AsyncSession) -> list[dict]:
    """Outbound campaign funnel and cost per completed response."""
    rows = (
        await session.execute(
            select(
                CampaignModel.id,
                CampaignModel.name,
                CampaignModel.state,
                CampaignModel.total_rows,
                CampaignModel.started_at,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                func.count(WorkflowRunModel.id).label("dialled"),
                func.count(WorkflowRunModel.answered_at).label("connected"),
                _sum(case((WorkflowRunModel.is_completed.is_(True), 1), else_=0)).label(
                    "completed"
                ),
                _sum(WorkflowRunModel.total_charged_paise).label("spend_paise"),
            )
            .select_from(CampaignModel)
            .join(
                OrganizationModel, CampaignModel.organization_id == OrganizationModel.id
            )
            .outerjoin(
                WorkflowRunModel, WorkflowRunModel.campaign_id == CampaignModel.id
            )
            .group_by(CampaignModel.id, OrganizationModel.id)
            .order_by(desc("spend_paise"))
        )
    ).all()

    result = []
    for r in rows:
        dialled = int(r.dialled or 0)
        connected = int(r.connected or 0)
        completed = int(r.completed or 0)
        spend = int(r.spend_paise or 0)
        result.append(
            {
                "campaign_id": r.id,
                "name": r.name,
                "account": r.billing_name or r.provider_id,
                "state": r.state,
                "contacts_total": r.total_rows,
                "dialled": dialled,
                "connected": connected,
                "completed": completed,
                "answer_rate": (connected / dialled) if dialled else None,
                "completion_rate": (completed / connected) if connected else None,
                "spend_paise": spend,
                # The headline number for outbound work.
                "cost_per_completed_paise": (spend // completed) if completed else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
        )
    return result


async def campaign_concurrency(
    session: AsyncSession, *, campaign_id: int
) -> list[dict]:
    """Calls started per minute during a campaign, as a concurrency proxy."""
    minute = func.date_trunc("minute", WorkflowRunModel.created_at)
    rows = (
        await session.execute(
            select(minute.label("minute"), func.count().label("calls"))
            .where(WorkflowRunModel.campaign_id == campaign_id)
            .group_by(minute)
            .order_by(minute)
        )
    ).all()
    return [
        {"minute": r.minute.astimezone(IST).isoformat(), "calls": int(r.calls)}
        for r in rows
    ]


async def distinct_languages(session: AsyncSession) -> list[str]:
    rows = (
        await session.scalars(
            select(func.distinct(WorkflowRunModel.language)).where(
                WorkflowRunModel.language.isnot(None)
            )
        )
    ).all()
    return sorted(r for r in rows if r)


async def concurrency_now(session: AsyncSession) -> int:
    """Calls currently in flight."""
    return int(
        await session.scalar(
            select(func.count()).where(
                WorkflowRunModel.state == "running",
                WorkflowRunModel.is_completed.is_(False),
            )
        )
        or 0
    )


async def calls_today(session: AsyncSession) -> int:
    today = datetime.now(IST).date()
    start_utc, end_utc = ist_day_bounds_utc(today)
    return int(
        await session.scalar(
            select(func.count()).where(
                and_(
                    WorkflowRunModel.created_at >= start_utc,
                    WorkflowRunModel.created_at < end_utc,
                )
            )
        )
        or 0
    )

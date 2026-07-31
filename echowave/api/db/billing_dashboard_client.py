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

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import (
    and_,
    case,
    cast,
    desc,
    func,
    literal,
    or_,
    select,
    union_all,
)
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
from api.services.billing.rates import resolve_platform_rate
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


async def _effective_rates(
    session: AsyncSession, organization_ids: list[int]
) -> dict[int, dict]:
    """What each of these accounts actually pays per minute, right now.

    Goes through the same resolver the cost engine uses, so the dashboard and
    an invoice cannot disagree. Returns the rupee figure that would be billed,
    the dollar price when there is one, the pulse, and where the price came
    from — an operator reading a list of accounts needs to know which of them
    are on a negotiated deal, and "is this column non-null" stopped answering
    that once prices could be quoted in dollars.

    One resolver call per account. These lists are bounded by the account count
    rather than by traffic, and each call is two indexed lookups.
    """
    now = datetime.now(UTC)
    resolved: dict[int, dict] = {}
    for organization_id in organization_ids:
        rate = await resolve_platform_rate(
            session, organization_id=organization_id, at=now
        )
        resolved[organization_id] = {
            "platform_rate_mpaise": rate.rate_mpaise,
            "platform_rate_micros_usd": rate.rate_micros_usd,
            "pulse_seconds": rate.pulse_seconds,
            "platform_rate_source": rate.source,
            "platform_rate_is_override": rate.source == "account_override",
        }
    return resolved


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

    # Resolved rather than read off organizations.platform_rate_mpaise. That
    # column is a convenience mirror that only ever held a rupee figure, so a
    # dollar-denominated price left it null and the list silently showed the
    # fallback constant instead — a wrong number reported as if it were the
    # account's own.
    rates = await _effective_rates(session, [r.id for r in rows])

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
            **rates[r.id],
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
        **(await _effective_rates(session, [organization_id]))[organization_id],
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
            # A row quoted in dollars has no fixed rupee value, so the history
            # has to show the dollar price rather than a blank.
            "platform_rate_micros_usd": r.platform_rate_micros_usd,
            "pulse_seconds": r.pulse_seconds,
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


async def campaign_concurrency(session: AsyncSession, *, campaign_id: int) -> dict:
    """Peak concurrent calls over the life of a campaign.

    Real concurrency, not a calls-started rate: every call contributes +1 at its
    start and -1 at its end, and a running sum over that event stream gives the
    number of calls in flight at each transition. We report the peak per bucket
    because that is the number capacity planning needs.

    The bucket adapts to how long the campaign has been running — hourly reads
    well over a couple of days but degenerates into a comb of hundreds of points
    over a month, where the daily peak is both readable and the number an
    operator actually asks for.
    """
    start_ts = func.coalesce(WorkflowRunModel.answered_at, WorkflowRunModel.created_at)
    # A still-running call has no ended_at; fall back to its billed duration so
    # it still occupies the line rather than vanishing from the series.
    end_ts = func.coalesce(
        WorkflowRunModel.ended_at,
        start_ts
        + func.make_interval(
            0, 0, 0, 0, 0, 0, func.coalesce(WorkflowRunModel.billable_seconds, 0)
        ),
    )
    in_campaign = WorkflowRunModel.campaign_id == campaign_id

    events = union_all(
        select(start_ts.label("ts"), literal(1).label("delta")).where(in_campaign),
        select(end_ts.label("ts"), literal(-1).label("delta")).where(in_campaign),
    ).subquery()

    # Ordering -1 before +1 within the same instant stops a call that ends
    # exactly as another starts from reading as two concurrent calls.
    running = select(
        events.c.ts.label("ts"),
        func.sum(events.c.delta)
        .over(order_by=(events.c.ts, events.c.delta))
        .label("concurrent"),
    ).subquery()

    span = (
        await session.execute(
            select(func.min(start_ts), func.max(end_ts)).where(in_campaign)
        )
    ).first()
    first, last = (span or (None, None))[0], (span or (None, None))[1]
    if first is None or last is None:
        return {"bucket": "hour", "series": []}
    bucket = "hour" if (last - first) <= timedelta(days=3) else "day"

    # Bucket in IST so a "day" is the local day operators work in, matching the
    # rollups; date_trunc otherwise cuts at UTC midnight (05:30 IST).
    truncated = func.date_trunc(bucket, func.timezone("Asia/Kolkata", running.c.ts))
    rows = (
        await session.execute(
            select(
                truncated.label("bucket_start"),
                func.max(running.c.concurrent).label("peak"),
            )
            .where(running.c.ts.isnot(None))
            .group_by(truncated)
            .order_by(truncated)
        )
    ).all()
    return {
        "bucket": bucket,
        "series": [
            {
                "bucket_start": r.bucket_start.isoformat(),
                "peak": int(r.peak or 0),
            }
            for r in rows
        ],
    }


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


# --- Latency percentiles -----------------------------------------------------
#
# Three different numbers get called "latency" and conflating them hides the
# thing you would actually fix:
#
#   TTFT       the model's thinking time — final transcript in, first token out
#   TTFB       the voice's — first token in, first audio byte out
#   perceived  what the caller experiences, which is neither of the above and is
#              the only one a customer complains about
#
# Every comparable platform headlines TTFT and TTFB separately. Here they were
# only ever visible folded into a stage-median bar, which averages away the tail
# and cannot answer "is it the model or the voice".


def _ttft_expr():
    return CallTurnMetricModel.t_llm_first_token_ms - CallTurnMetricModel.t_stt_final_ms


def _ttfb_expr():
    return (
        CallTurnMetricModel.t_tts_first_byte_ms
        - CallTurnMetricModel.t_llm_first_token_ms
    )


#: Selectable measures, each with the expression and the columns that must be
#: present for a turn to count. A turn missing one endpoint is excluded rather
#: than treated as zero — a zero would drag a percentile down and read as an
#: improvement.
LATENCY_MEASURES: dict[str, tuple] = {
    "perceived": (
        lambda: CallTurnMetricModel.latency_ms,
        (CallTurnMetricModel.latency_ms,),
    ),
    "ttft": (
        _ttft_expr,
        (CallTurnMetricModel.t_llm_first_token_ms, CallTurnMetricModel.t_stt_final_ms),
    ),
    "ttfb": (
        _ttfb_expr,
        (
            CallTurnMetricModel.t_tts_first_byte_ms,
            CallTurnMetricModel.t_llm_first_token_ms,
        ),
    ),
}

#: p95 is the usual headline, but p99 is where the calls that get escalated
#: live: at 20 turns a call, a p95 breach happens roughly once per call.
PERCENTILES = (0.5, 0.9, 0.95, 0.99)


def _percentile_columns(expr):
    return [
        func.percentile_cont(p).within_group(expr).label(f"p{int(p * 100)}")
        for p in PERCENTILES
    ]


def _percentile_dict(row) -> dict:
    return {
        f"p{int(p * 100)}_ms": (
            int(getattr(row, f"p{int(p * 100)}"))
            if getattr(row, f"p{int(p * 100)}") is not None
            else None
        )
        for p in PERCENTILES
    }


def _measure_or_raise(measure: str):
    try:
        return LATENCY_MEASURES[measure]
    except KeyError as exc:
        raise ValueError(
            f"Unknown latency measure {measure!r}; "
            f"expected one of {sorted(LATENCY_MEASURES)}"
        ) from exc


async def latency_percentile_series(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    measure: str = "perceived",
    organization_id: int | None = None,
    language: str | None = None,
) -> list[dict]:
    """Daily p50/p90/p95/p99 for one measure, computed in SQL over raw turns."""
    expr_factory, required = _measure_or_raise(measure)
    expr = expr_factory()

    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)
    day_expr = func.date(func.timezone("Asia/Kolkata", CallTurnMetricModel.created_at))

    conditions = [
        CallTurnMetricModel.created_at >= start_utc,
        CallTurnMetricModel.created_at < end_utc,
        *[column.isnot(None) for column in required],
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)
    if language:
        conditions.append(WorkflowRunModel.language == language)

    rows = (
        await session.execute(
            select(
                day_expr.label("day"),
                *_percentile_columns(expr),
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
        {"day": r.day.isoformat(), **_percentile_dict(r), "turns": int(r.turns or 0)}
        for r in rows
    ]


async def latency_headline(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> dict:
    """One set of percentiles per measure over the whole window.

    Separate from the series because the percentile of a period is not the
    average of its daily percentiles — a fact that is easy to get wrong by
    summarising the chart instead of the data.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    out: dict[str, dict] = {}
    for measure, (expr_factory, required) in LATENCY_MEASURES.items():
        conditions = [
            CallTurnMetricModel.created_at >= start_utc,
            CallTurnMetricModel.created_at < end_utc,
            *[column.isnot(None) for column in required],
        ]
        if organization_id is not None:
            conditions.append(WorkflowModel.organization_id == organization_id)

        row = (
            await session.execute(
                select(
                    *_percentile_columns(expr_factory()), func.count().label("turns")
                )
                .select_from(CallTurnMetricModel)
                .join(
                    WorkflowRunModel,
                    CallTurnMetricModel.workflow_run_id == WorkflowRunModel.id,
                )
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(*conditions)
            )
        ).one()
        out[measure] = {**_percentile_dict(row), "turns": int(row.turns or 0)}
    return out


async def call_latency_summary(
    session: AsyncSession, *, workflow_run_id: int
) -> dict | None:
    """Percentiles for one call, and the first turn separated from the rest.

    The opening turn is not comparable to the others — no conversation context,
    a cold connection, and often a pre-recorded greeting — so averaging it in
    makes a healthy call look slow and hides a genuinely slow opening.
    """
    rows = (
        await session.scalars(
            select(CallTurnMetricModel)
            .where(CallTurnMetricModel.workflow_run_id == workflow_run_id)
            .order_by(CallTurnMetricModel.turn_index)
        )
    ).all()
    if not rows:
        return None

    def percentiles(values: list[int]) -> dict:
        if not values:
            return {f"p{int(p * 100)}_ms": None for p in PERCENTILES}
        ordered = sorted(values)
        out = {}
        for p in PERCENTILES:
            # Nearest-rank, matching what a reader expects from a handful of
            # turns. Interpolating across four samples invents precision.
            index = max(0, min(len(ordered) - 1, round(p * len(ordered)) - 1))
            out[f"p{int(p * 100)}_ms"] = ordered[index]
        return out

    def series(turns, expr) -> list[int]:
        return [value for value in (expr(t) for t in turns) if value is not None]

    perceived = series(rows, lambda t: t.latency_ms)
    ttft = series(
        rows,
        lambda t: (
            t.t_llm_first_token_ms - t.t_stt_final_ms
            if t.t_llm_first_token_ms is not None and t.t_stt_final_ms is not None
            else None
        ),
    )
    ttfb = series(
        rows,
        lambda t: (
            t.t_tts_first_byte_ms - t.t_llm_first_token_ms
            if t.t_tts_first_byte_ms is not None and t.t_llm_first_token_ms is not None
            else None
        ),
    )
    steady = series(rows[1:], lambda t: t.latency_ms)
    worst = max(perceived) if perceived else None

    return {
        "turns": len(rows),
        "perceived": percentiles(perceived),
        "ttft": percentiles(ttft),
        "ttfb": percentiles(ttfb),
        "first_turn_ms": rows[0].latency_ms,
        "steady_state_p50_ms": percentiles(steady)["p50_ms"],
        "worst_turn_ms": worst,
        "worst_turn_index": (
            rows[perceived.index(worst)].turn_index
            if worst is not None and worst in perceived
            else None
        ),
    }


# --- Tokens and conversational efficiency ------------------------------------
#
# Token counts are already stored: ``call_cost_items.units`` for the LLM
# component is the raw token count (see money.cost_paise — callers never
# pre-divide). They have only ever been rendered as money, which hides the one
# number that predicts what a change to the prompt will cost.
#
# Tokens *per minute of conversation* is the figure worth watching. Raw token
# totals track how busy the platform was; tokens per minute tracks how
# expensive the agent design is, and it is comparable across accounts, models
# and months.

#: Truncation granularities the trend supports, in the timezone billing uses.
_TREND_TRUNC = {"day": "day", "week": "week", "month": "month"}


def _ist_period(column, granularity: str):
    try:
        unit = _TREND_TRUNC[granularity]
    except KeyError as exc:
        raise ValueError(
            f"Unknown granularity {granularity!r}; expected one of {sorted(_TREND_TRUNC)}"
        ) from exc
    return func.date_trunc(unit, func.timezone("Asia/Kolkata", column))


async def token_usage_series(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    granularity: str = "day",
    organization_id: int | None = None,
) -> list[dict]:
    """Tokens, LLM spend and tokens per minute, by day, week or month."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    # Bucketed by when the *call* happened, not when costing ran. A cost item's
    # created_at is the moment the post-call job wrote it — so a call recosted
    # after a rate change would move its tokens to the day of the recost, and
    # the tokens-per-minute ratio would divide one period's tokens by another
    # period's conversation. Both series therefore key off the run.
    token_period = _ist_period(WorkflowRunModel.created_at, granularity)
    conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
        CallCostItemModel.component == "llm",
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    token_rows = (
        await session.execute(
            select(
                token_period.label("period"),
                func.sum(CallCostItemModel.units).label("tokens"),
                func.sum(CallCostItemModel.cost_paise).label("cost_paise"),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)).label(
                    "calls"
                ),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(token_period)
            .order_by(token_period)
        )
    ).all()

    # Conversation seconds come from the runs, not the cost items: summing
    # seconds across components would multiply every call by however many
    # providers priced it.
    run_period = _ist_period(WorkflowRunModel.created_at, granularity)
    run_conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
    ]
    if organization_id is not None:
        run_conditions.append(WorkflowModel.organization_id == organization_id)

    second_rows = (
        await session.execute(
            select(
                run_period.label("period"),
                func.sum(WorkflowRunModel.billable_seconds).label("seconds"),
            )
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*run_conditions)
            .group_by(run_period)
            .order_by(run_period)
        )
    ).all()
    seconds_by_period = {r.period: int(r.seconds or 0) for r in second_rows}

    out = []
    for row in token_rows:
        tokens = int(row.tokens or 0)
        seconds = seconds_by_period.get(row.period, 0)
        out.append(
            {
                "period": row.period.date().isoformat(),
                "tokens": tokens,
                "cost_paise": int(row.cost_paise or 0),
                "calls": int(row.calls or 0),
                "minutes": round(seconds / 60, 1),
                # None rather than zero when there is no conversation to divide
                # by: a rate over no time is undefined, not free.
                "tokens_per_minute": (
                    round(tokens / (seconds / 60)) if seconds else None
                ),
            }
        )
    return out


async def token_usage_by_model(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> list[dict]:
    """Which models consumed the tokens, and what each cost per 1k.

    The effective per-1k cost is derived from what was actually spent rather
    than read from the rate card, so a model priced at one rate and billed at
    another shows up here as the discrepancy it is.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    conditions = [
        # As in token_usage_series: the call's date, not the costing job's.
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
        CallCostItemModel.component == "llm",
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                CallCostItemModel.provider,
                CallCostItemModel.model,
                func.sum(CallCostItemModel.units).label("tokens"),
                func.sum(CallCostItemModel.cost_paise).label("cost_paise"),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)).label(
                    "calls"
                ),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(CallCostItemModel.provider, CallCostItemModel.model)
            .order_by(desc(func.sum(CallCostItemModel.units)))
        )
    ).all()

    return [
        {
            "provider": r.provider,
            "model": r.model or "(unspecified)",
            "tokens": int(r.tokens or 0),
            "cost_paise": int(r.cost_paise or 0),
            "calls": int(r.calls or 0),
            "tokens_per_call": (
                round(int(r.tokens or 0) / int(r.calls)) if r.calls else None
            ),
            "paise_per_1k_tokens": (
                round(int(r.cost_paise or 0) / (int(r.tokens) / 1000), 2)
                if r.tokens
                else None
            ),
        }
        for r in rows
    ]


async def tool_call_stats(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> list[dict]:
    """How often each tool was called and how long it took.

    ``tool_called`` and ``tool_ms`` have been recorded on every turn since the
    latency observer landed and never read. A tool that takes two seconds makes
    a call feel broken while every provider metric stays green, and there was no
    way to see it.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    conditions = [
        CallTurnMetricModel.created_at >= start_utc,
        CallTurnMetricModel.created_at < end_utc,
        CallTurnMetricModel.tool_called.isnot(None),
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                CallTurnMetricModel.tool_called.label("tool"),
                func.count().label("calls"),
                func.percentile_cont(0.5)
                .within_group(CallTurnMetricModel.tool_ms)
                .label("p50"),
                func.percentile_cont(0.95)
                .within_group(CallTurnMetricModel.tool_ms)
                .label("p95"),
                func.max(CallTurnMetricModel.tool_ms).label("worst"),
                # A turn that named a tool but recorded no duration never
                # returned. Counting it as a failure is the honest reading, and
                # it is the only failure signal available without new columns.
                func.count()
                .filter(CallTurnMetricModel.tool_ms.is_(None))
                .label("no_duration"),
            )
            .select_from(CallTurnMetricModel)
            .join(
                WorkflowRunModel,
                CallTurnMetricModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(CallTurnMetricModel.tool_called)
            .order_by(desc(func.count()))
        )
    ).all()

    return [
        {
            "tool": r.tool,
            "calls": int(r.calls or 0),
            "p50_ms": int(r.p50) if r.p50 is not None else None,
            "p95_ms": int(r.p95) if r.p95 is not None else None,
            "worst_ms": int(r.worst) if r.worst is not None else None,
            "incomplete": int(r.no_duration or 0),
        }
        for r in rows
    ]


async def context_growth_by_turn(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
    max_turn: int = 30,
) -> dict:
    """How the prompt grows as a conversation goes on.

    A voice agent resends the whole conversation every turn: turn 1 sends the
    system prompt, turn 20 sends the system prompt plus nineteen exchanges. So
    prompt tokens rise roughly linearly with turn index, and their *sum over a
    call* rises with the square of its length — a call twice as long costs
    closer to four times as much in language-model spend, not twice.

    Call-wide totals cannot show this. Ten short exchanges and three long ones
    sum to the same number, and only the shape says whether context growth is
    where the money went. The fixes are structural — summarise older turns, trim
    the system prompt, turn on prompt caching — and none of them appears as a
    line item on any invoice.

    Medians rather than means throughout: one twenty-minute call would otherwise
    define the curve for everybody.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)
    m = CallTurnMetricModel

    conditions = [
        m.created_at >= start_utc,
        m.created_at < end_utc,
        m.prompt_tokens.isnot(None),
        m.turn_index < max_turn,
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    rows = (
        await session.execute(
            select(
                m.turn_index,
                func.percentile_cont(0.5).within_group(m.prompt_tokens).label("prompt"),
                func.percentile_cont(0.5)
                .within_group(m.completion_tokens)
                .label("completion"),
                func.percentile_cont(0.5)
                .within_group(func.coalesce(m.cached_tokens, 0))
                .label("cached"),
                func.count().label("turns"),
            )
            .select_from(m)
            .join(WorkflowRunModel, m.workflow_run_id == WorkflowRunModel.id)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(m.turn_index)
            .order_by(m.turn_index)
        )
    ).all()

    series = [
        {
            "turn": int(r.turn_index) + 1,
            "prompt_tokens": int(r.prompt) if r.prompt is not None else None,
            "completion_tokens": (
                int(r.completion) if r.completion is not None else None
            ),
            "cached_tokens": int(r.cached) if r.cached is not None else 0,
            "turns": int(r.turns or 0),
        }
        for r in rows
    ]

    # The headline: how much bigger the prompt is late in a call than at the
    # start. A flat curve means context is being managed; a steep one is the
    # single largest saving available and is invisible everywhere else.
    first = series[0]["prompt_tokens"] if series else None
    last = series[-1]["prompt_tokens"] if series else None
    growth = round(last / first, 1) if first and last and first > 0 else None

    cached_total = sum(row["cached_tokens"] for row in series)
    prompt_total = sum(row["prompt_tokens"] or 0 for row in series)

    return {
        "series": series,
        "first_turn_prompt_tokens": first,
        "last_turn_prompt_tokens": last,
        "growth_multiple": growth,
        "deepest_turn": series[-1]["turn"] if series else None,
        # Prompt caching cuts the cost of resent context by most of its value.
        # None rather than 0 when nothing was measured, because "no cache" and
        # "not reported" are different findings.
        "cache_hit_rate": (
            round(cached_total / prompt_total, 3) if prompt_total else None
        ),
    }

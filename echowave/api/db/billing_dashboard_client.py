"""Read queries backing the admin billing dashboard.

Headline figures come from ``daily_organization_rollup`` rather than scanning
``workflow_runs``, which is what keeps pages inside the performance budget at a
million calls. Raw tables are only touched for drill-down, always bounded by a
time range and, where relevant, one account.

Percentiles are computed in SQL with ``percentile_cont`` over raw
``call_turn_metrics`` rows — never averaged from pre-aggregated buckets, which
would give wrong answers.

Queries default to cross-account, which is safe because the staff routes are
the only ones that call them without an ``organization_id``. Several also take
an optional ``organization_id`` and are reused by the customer-facing usage
routes — those **force** the id from the authenticated user and ignore any id
in the request, so a customer can only ever read their own account.
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
    OrganizationMembershipModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    PaymentModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind, RateUnit
from api.services.billing.rates import resolve_platform_rate
from api.services.billing.rollup import IST, ist_day_bounds_utc

R = DailyOrganizationRollupModel


def _sum(column):
    return func.coalesce(func.sum(column), 0)


def _gap(earlier: int | None, later: int | None) -> int | None:
    """A stage's duration, or None when either end of it was not measured.

    None rather than zero throughout: an unmeasured stage and an instantaneous
    one look identical on a chart but mean opposite things, and only one of
    them is good news.
    """
    if earlier is None or later is None:
        return None
    return max(later - earlier, 0)


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
                _sum(R.answered_calls).label("answered_calls"),
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
        calls = int(row.calls) if row else 0
        answered = int(row.answered_calls) if row else 0
        series.append(
            {
                "day": cursor.isoformat(),
                "calls": calls,
                "answered_calls": answered,
                # None rather than 0 with no calls that day: a 0% success rate
                # and "nothing happened" look identical as a number and are
                # not the same story on a chart.
                "success_rate": (answered / calls) if calls else None,
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
    """Median duration of each pipeline stage — where the time goes.

    The last bucket is **unattributed**, not playback. It is the remainder of
    the turn after the last stage that reports a mark, and it holds everything
    the pipeline does not instrument: transport and network transit, output
    queueing, and any gap between stages that the marks do not cover. Some of
    it is genuinely audio starting to play. Naming the whole remainder after
    one of its parts is how somebody ends up optimising audio output when the
    time is really in the network.

    Turns predating measured endpointing have a NULL release mark. They are
    excluded from the endpointing and STT medians rather than counted as zero —
    a zero would halve the median of the stage that is usually the largest —
    and ``endpointing_measured_turns`` reports how much of the window has it,
    so a median over a handful of rows is not read as the whole picture.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)
    m = CallTurnMetricModel

    def median(expr):
        return func.percentile_cont(0.5).within_group(expr)

    # STT is measured from the turn release, so it shares endpointing's fate:
    # without a release mark there is no start to measure from. Falling back to
    # t_user_stopped_ms would silently fold endpointing into STT.
    released = m.t_endpoint_fired_ms.isnot(None)

    row = (
        await session.execute(
            select(
                median(m.t_endpoint_fired_ms - m.t_user_stopped_ms).label(
                    "endpointing"
                ),
                median(m.t_stt_final_ms - m.t_endpoint_fired_ms).label("stt"),
                median(m.t_llm_first_token_ms - m.t_stt_final_ms).label("llm"),
                median(m.t_tts_first_byte_ms - m.t_llm_first_token_ms).label("tts"),
                median(m.t_audio_out_ms - m.t_tts_first_byte_ms).label("unattributed"),
                func.count(m.id).label("turns"),
                func.count(m.t_endpoint_fired_ms).label("measured"),
            ).where(
                m.created_at >= start_utc,
                m.created_at < end_utc,
                m.t_audio_out_ms.isnot(None),
            )
        )
    ).one()

    endpointing_row = (
        await session.execute(
            select(
                median(m.t_endpoint_fired_ms - m.t_user_stopped_ms).label(
                    "endpointing"
                ),
                median(m.t_stt_final_ms - m.t_endpoint_fired_ms).label("stt"),
            ).where(
                m.created_at >= start_utc,
                m.created_at < end_utc,
                m.t_audio_out_ms.isnot(None),
                released,
            )
        )
    ).one()

    turns = int(row.turns or 0)
    measured = int(row.measured or 0)

    return {
        # None, not 0, when nothing in the window measured it: "we did not look"
        # and "it took no time" are different answers and only one is good news.
        "endpointing_ms": (int(endpointing_row.endpointing) if measured else None),
        "stt_ms": int(endpointing_row.stt) if measured else None,
        "llm_ms": int(row.llm or 0),
        "tts_ms": int(row.tts or 0),
        "unattributed_ms": int(row.unattributed or 0),
        "turns": turns,
        "endpointing_measured_turns": measured,
        "endpointing_coverage": (measured / turns) if turns else None,
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


def _account_contact():
    """One contact per organization: whoever answers for the account.

    The accounts table identified an account as ``billing_name or provider_id``.
    ``billing_name`` is only set once somebody fills in the billing profile, so
    in practice most rows showed ``provider_id`` — ``org_user-3f9c…``, which
    names nobody. Staff could see that an account existed, was spending money
    and had gone idle, and could not tell whose it was or write to them.

    Ranked rather than filtered to ``role = 'owner'``: an organization created
    before the founder fix, or one mid-transfer, would otherwise show a blank
    where a person should be. Owner first, then Admin, then the earliest
    member — always somebody, and always the person with the most standing to
    answer for the account.
    """
    return (
        select(
            OrganizationMembershipModel.organization_id.label("organization_id"),
            UserModel.email.label("owner_email"),
            OrganizationMembershipModel.role.label("owner_role"),
        )
        .join(UserModel, UserModel.id == OrganizationMembershipModel.user_id)
        .distinct(OrganizationMembershipModel.organization_id)
        .order_by(
            OrganizationMembershipModel.organization_id,
            case(
                (OrganizationMembershipModel.role == "owner", 0),
                (OrganizationMembershipModel.role == "admin", 1),
                else_=2,
            ),
            OrganizationMembershipModel.created_at,
            OrganizationMembershipModel.id,
        )
        .subquery()
    )


def _member_counts():
    """How many people are in each organization.

    Reads as the difference between a solo signup and a team, which is the
    distinction that decides whether the role tiers mean anything on that
    account at all.
    """
    return (
        select(
            OrganizationMembershipModel.organization_id.label("organization_id"),
            func.count().label("member_count"),
        )
        .group_by(OrganizationMembershipModel.organization_id)
        .subquery()
    )


async def accounts_summary(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    account_type: str | None = None,
    status: str | None = None,
    q: str | None = None,
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

    contact = _account_contact()
    members = _member_counts()

    conditions = []
    if account_type:
        conditions.append(OrganizationModel.account_type == account_type)
    if status:
        conditions.append(OrganizationModel.billing_status == status)
    if q and q.strip():
        # Matched against *any* member's address, not just the contact the row
        # displays. Someone writing in from support@ is often not the founder,
        # and a search that only knew the owner would report their account as
        # not existing.
        term = f"%{q.strip().lower()}%"
        member_match = (
            select(OrganizationMembershipModel.organization_id)
            .join(UserModel, UserModel.id == OrganizationMembershipModel.user_id)
            .where(func.lower(UserModel.email).like(term))
        )
        conditions.append(
            or_(
                func.lower(OrganizationModel.billing_name).like(term),
                func.lower(OrganizationModel.provider_id).like(term),
                OrganizationModel.id.in_(member_match),
            )
        )

    rows = (
        await session.execute(
            select(
                OrganizationModel.id,
                OrganizationModel.billing_name,
                OrganizationModel.provider_id,
                OrganizationModel.account_type,
                OrganizationModel.billing_status,
                OrganizationModel.platform_rate_mpaise,
                contact.c.owner_email,
                contact.c.owner_role,
                func.coalesce(members.c.member_count, 0).label("member_count"),
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
            .outerjoin(contact, contact.c.organization_id == OrganizationModel.id)
            .outerjoin(members, members.c.organization_id == OrganizationModel.id)
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
            # Falls back to the email before the opaque provider id, so an
            # account nobody has billed yet still reads as a person.
            "name": r.billing_name or r.owner_email or r.provider_id,
            "owner_email": r.owner_email,
            "owner_role": r.owner_role,
            "member_count": int(r.member_count or 0),
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

    # Who is on the account, ranked the way the list ranks its contact. Support
    # arrives here from a flag on the accounts table — low balance, idle, a
    # failed payment — and the next question is always who to write to.
    people = (
        await session.execute(
            select(
                UserModel.email,
                OrganizationMembershipModel.role,
                OrganizationMembershipModel.created_at,
            )
            .join(
                OrganizationMembershipModel,
                OrganizationMembershipModel.user_id == UserModel.id,
            )
            .where(OrganizationMembershipModel.organization_id == organization_id)
            .order_by(
                case(
                    (OrganizationMembershipModel.role == "owner", 0),
                    (OrganizationMembershipModel.role == "admin", 1),
                    else_=2,
                ),
                OrganizationMembershipModel.created_at,
                OrganizationMembershipModel.id,
            )
        )
    ).all()
    members = [
        {
            "email": p.email,
            "role": p.role,
            "joined_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in people
    ]

    return {
        "organization_id": org.id,
        "name": org.billing_name
        or (members[0]["email"] if members else None)
        or org.provider_id,
        "provider_id": org.provider_id,
        "owner_email": members[0]["email"] if members else None,
        "members": members,
        "member_count": len(members),
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
        # Stage durations are the gaps between consecutive marks. A missing
        # mark makes both stages either side of it unknowable, so both go None
        # rather than being measured from the previous mark instead — treating
        # an absent release as zero would silently fold endpointing, usually
        # the largest stage of the turn, into STT.
        "turns": [
            {
                "turn_index": t.turn_index,
                "endpointing_ms": _gap(t.t_user_stopped_ms, t.t_endpoint_fired_ms),
                "stt_ms": _gap(t.t_endpoint_fired_ms, t.t_stt_final_ms),
                "llm_ms": _gap(t.t_stt_final_ms, t.t_llm_first_token_ms),
                "tts_ms": _gap(t.t_llm_first_token_ms, t.t_tts_first_byte_ms),
                "unattributed_ms": _gap(t.t_tts_first_byte_ms, t.t_audio_out_ms),
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


def _call_span():
    """When a call started and when it stopped occupying a line.

    A still-running call has no ``ended_at``; falling back to its billed
    duration keeps it on the line rather than letting it vanish from the
    series, which would under-report exactly the peak we are looking for.
    """
    start_ts = func.coalesce(WorkflowRunModel.answered_at, WorkflowRunModel.created_at)
    end_ts = func.coalesce(
        WorkflowRunModel.ended_at,
        start_ts
        + func.make_interval(
            0, 0, 0, 0, 0, 0, func.coalesce(WorkflowRunModel.billable_seconds, 0)
        ),
    )
    return start_ts, end_ts


def _running_concurrency(*filters):
    """Calls in flight at each transition, as a subquery.

    Real concurrency, not a calls-started rate: every call contributes +1 at
    its start and -1 at its end, and a running sum over that event stream gives
    the number in flight at each instant. Ordering -1 before +1 within the same
    instant stops a call that ends exactly as another starts from reading as
    two concurrent calls.
    """
    start_ts, end_ts = _call_span()
    events = union_all(
        select(start_ts.label("ts"), literal(1).label("delta")).where(*filters),
        select(end_ts.label("ts"), literal(-1).label("delta")).where(*filters),
    ).subquery()
    return select(
        events.c.ts.label("ts"),
        func.sum(events.c.delta)
        .over(order_by=(events.c.ts, events.c.delta))
        .label("concurrent"),
    ).subquery()


async def peak_concurrency_by_day(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Peak concurrent calls per IST day, across every account.

    What a concurrency tier has to be priced against. The limiter's ceiling
    says what we permit; this says what anyone has ever actually needed, and
    the two are usually nothing like each other. A tier priced off the first is
    theatre.
    """
    lo, hi = ist_day_bounds_utc(start)[0], ist_day_bounds_utc(end)[1]
    running = _running_concurrency(
        WorkflowRunModel.created_at >= lo,
        WorkflowRunModel.created_at < hi,
        func.coalesce(WorkflowRunModel.billable_seconds, 0) > 0,
    )
    day = func.date_trunc("day", func.timezone("Asia/Kolkata", running.c.ts))
    rows = (
        await session.execute(
            select(day.label("day"), func.max(running.c.concurrent).label("peak"))
            .where(running.c.ts.isnot(None))
            .group_by(day)
            .order_by(day)
        )
    ).all()
    return [
        {"day": r.day.date().isoformat(), "peak": int(r.peak or 0)}
        for r in rows
        if r.day is not None
    ]


async def campaign_concurrency(session: AsyncSession, *, campaign_id: int) -> dict:
    """Peak concurrent calls over the life of a campaign.

    The bucket adapts to how long the campaign has been running — hourly reads
    well over a couple of days but degenerates into a comb of hundreds of points
    over a month, where the daily peak is both readable and the number an
    operator actually asks for.
    """
    start_ts, end_ts = _call_span()
    in_campaign = WorkflowRunModel.campaign_id == campaign_id
    running = _running_concurrency(in_campaign)

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


# Duration bands for the "how long are my calls" histogram. Chosen around the
# shapes that mean different things on a voice agent: under 30s is usually a
# hang-up or a wrong number, 30s–2m is a completed short task, and anything past
# five minutes is where language-model spend starts compounding.
_DURATION_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0–30s", 0, 30),
    ("30–60s", 30, 60),
    ("1–2m", 60, 120),
    ("2–5m", 120, 300),
    ("5–10m", 300, 600),
    ("10m+", 600, None),
)


def _duration_bucket_expr():
    """A CASE mapping billable_seconds onto the labels above.

    Bucketing in SQL rather than in Python because the alternative is loading
    every run in the range into memory to count them, which stops working at
    exactly the volume that makes the chart interesting.
    """
    seconds = func.coalesce(WorkflowRunModel.billable_seconds, 0)
    branches = [
        (seconds < upper, label)
        for label, _lower, upper in _DURATION_BUCKETS
        if upper is not None
    ]
    return case(*branches, else_=_DURATION_BUCKETS[-1][0])


async def call_analytics(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    organization_id: int | None = None,
) -> dict:
    """The shape of an account's calls: outcome, direction, length, hour, agent.

    Every figure here is a ``GROUP BY`` over ``workflow_runs`` bounded to the
    range, so the whole panel is five small queries rather than a page scan.

    Deliberately *not* served from ``daily_organization_rollup``: the rollup
    stores per-day totals and knows nothing about disposition, direction or
    duration, which is the entire question this answers. The daily counts a
    caller wants alongside it come from :func:`daily_series`, which is on the
    rollup and stays cheap.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
    ]
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    def _scoped(*columns):
        return (
            select(*columns)
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
        )

    answered = func.count(WorkflowRunModel.answered_at)
    seconds = _sum(WorkflowRunModel.billable_seconds)
    charged = _sum(WorkflowRunModel.total_charged_paise)

    totals_row = (
        await session.execute(
            _scoped(
                func.count(WorkflowRunModel.id).label("calls"),
                answered.label("answered"),
                seconds.label("billable_seconds"),
                charged.label("charged_paise"),
            )
        )
    ).one()

    calls = int(totals_row.calls or 0)
    answered_calls = int(totals_row.answered or 0)
    billable_seconds = int(totals_row.billable_seconds or 0)

    # `gathered_context` is a JSON column, so the disposition is read with ->>
    # rather than a typed accessor. Runs that never reached a disposition come
    # back as NULL and are reported as such — collapsing them into "unknown"
    # here would hide the difference between a call that ended without one and
    # a call the pipeline never finished writing.
    disposition = WorkflowRunModel.gathered_context.op("->>")("mapped_call_disposition")
    by_disposition = [
        {
            "disposition": row.disposition,
            "calls": int(row.calls or 0),
            "billable_seconds": int(row.billable_seconds or 0),
        }
        for row in (
            await session.execute(
                _scoped(
                    disposition.label("disposition"),
                    func.count(WorkflowRunModel.id).label("calls"),
                    seconds.label("billable_seconds"),
                )
                .group_by(disposition)
                .order_by(desc(func.count(WorkflowRunModel.id)))
            )
        ).all()
    ]

    by_direction = [
        {
            "direction": row.direction,
            "calls": int(row.calls or 0),
            "answered": int(row.answered or 0),
            "billable_seconds": int(row.billable_seconds or 0),
        }
        for row in (
            await session.execute(
                _scoped(
                    WorkflowRunModel.call_type.label("direction"),
                    func.count(WorkflowRunModel.id).label("calls"),
                    answered.label("answered"),
                    seconds.label("billable_seconds"),
                ).group_by(WorkflowRunModel.call_type)
            )
        ).all()
    ]

    bucket = _duration_bucket_expr()
    counted = {
        row.bucket: int(row.calls or 0)
        for row in (
            await session.execute(
                _scoped(
                    bucket.label("bucket"),
                    func.count(WorkflowRunModel.id).label("calls"),
                ).group_by(bucket)
            )
        ).all()
    }
    # Emitted in band order with the empty bands kept, so the histogram reads
    # as a distribution rather than rearranging itself as data arrives.
    by_duration = [
        {"bucket": label, "calls": counted.get(label, 0)}
        for label, _lower, _upper in _DURATION_BUCKETS
    ]

    # Local hour, not UTC: "when do people call us" is a question about the
    # caller's day, and IST is 5:30 off the hour, so a UTC bucket would smear
    # every peak across two of them.
    hour_expr = func.extract(
        "hour", func.timezone("Asia/Kolkata", WorkflowRunModel.created_at)
    )
    hourly = {
        int(row.hour): int(row.calls or 0)
        for row in (
            await session.execute(
                _scoped(
                    hour_expr.label("hour"),
                    func.count(WorkflowRunModel.id).label("calls"),
                ).group_by(hour_expr)
            )
        ).all()
    }
    by_hour = [{"hour": hour, "calls": hourly.get(hour, 0)} for hour in range(24)]

    by_agent = [
        {
            "workflow_id": int(row.workflow_id),
            "name": row.name,
            "calls": int(row.calls or 0),
            "billable_seconds": int(row.billable_seconds or 0),
            "charged_paise": int(row.charged_paise or 0),
        }
        for row in (
            await session.execute(
                _scoped(
                    WorkflowModel.id.label("workflow_id"),
                    WorkflowModel.name.label("name"),
                    func.count(WorkflowRunModel.id).label("calls"),
                    seconds.label("billable_seconds"),
                    charged.label("charged_paise"),
                )
                .group_by(WorkflowModel.id, WorkflowModel.name)
                .order_by(desc(func.count(WorkflowRunModel.id)))
                .limit(10)
            )
        ).all()
    ]

    return {
        "totals": {
            "calls": calls,
            "answered": answered_calls,
            "billable_seconds": billable_seconds,
            "charged_paise": int(totals_row.charged_paise or 0),
            # None rather than 0 on an empty range: "no calls" and "calls that
            # averaged nothing" are different answers and only one of them is
            # a problem.
            "average_seconds": (round(billable_seconds / calls, 1) if calls else None),
            "answer_rate": round(answered_calls / calls, 3) if calls else None,
        },
        "by_disposition": by_disposition,
        "by_direction": by_direction,
        "by_duration": by_duration,
        "by_hour": by_hour,
        "by_agent": by_agent,
    }


# ---------------------------------------------------------------------------
# Model economics
#
# Every existing surface answers "what did this call cost" or "what did this
# account spend". Neither answers the one a pricing decision needs: *which
# model is eating the margin*. The data was already there — every cost line
# stores both what the customer was charged and what the vendor charged us —
# it had simply never been grouped by the model that incurred it.
# ---------------------------------------------------------------------------

#: What ``call_cost_items.units`` counts, per component. Only a fallback: the
#: authoritative unit is on the rate the line was priced against, and that is
#: read from ``provider_rates`` below. This map answers for a component whose
#: rate has since been deleted, so a row is never labelled with the wrong unit
#: merely because its rate card entry is gone.
_FALLBACK_UNIT_BY_COMPONENT = {
    "stt": RateUnit.MINUTE.value,
    "llm": RateUnit.THOUSAND_TOKENS.value,
    "tts": RateUnit.THOUSAND_CHARS.value,
    "telephony": RateUnit.MINUTE.value,
    "platform": RateUnit.MINUTE.value,
}

#: How the raw ``units`` count is spelled for a reader, per rate unit. Seconds
#: rather than minutes because seconds is what is stored — converting here
#: would make the column disagree with the receipt it came from.
_UNIT_LABEL = {
    RateUnit.MINUTE.value: "seconds",
    RateUnit.THOUSAND_CHARS.value: "characters",
    RateUnit.THOUSAND_TOKENS.value: "tokens",
    RateUnit.MONTH.value: "months",
}


async def _open_rate_units(session: AsyncSession) -> dict[tuple[str, str, str], str]:
    """The unit each currently-open provider rate is quoted in.

    One query. The rate card is bounded by how many models exist, not by
    traffic, so this is a few dozen rows however many calls were made.
    """
    rows = (
        await session.execute(
            select(
                ProviderRateModel.component,
                ProviderRateModel.provider,
                ProviderRateModel.model,
                ProviderRateModel.unit,
            ).where(ProviderRateModel.effective_to.is_(None))
        )
    ).all()
    return {(r.component, r.provider, r.model or ""): r.unit for r in rows}


async def model_usage(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    component: str | None = None,
    organization_id: int | None = None,
) -> dict:
    """Usage and margin per model, for every component — not only the LLM.

    ``token_usage_by_model`` already answered this for language models and only
    in tokens charged. This is the whole receipt: speech-to-text, the language
    model, speech synthesis, telephony and the platform fee, each split into
    what the customer paid and what the vendor charged us.

    **Units are never summed across rows.** A row's ``units`` is seconds,
    characters or tokens depending on what its rate is quoted against, so a
    total over the column would add tokens to seconds. Each row therefore
    carries its own unit, and the totals block sums only money.

    Bucketed by when the *call* happened rather than when costing ran — a call
    re-costed after a rate change would otherwise move its usage to the day of
    the re-cost. Same rule as ``token_usage_series``.
    """
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    conditions = [
        WorkflowRunModel.created_at >= start_utc,
        WorkflowRunModel.created_at < end_utc,
    ]
    if component is not None:
        conditions.append(CallCostItemModel.component == component)
    if organization_id is not None:
        conditions.append(WorkflowModel.organization_id == organization_id)

    charged = _sum(CallCostItemModel.cost_paise)
    our_cost = _sum(CallCostItemModel.provider_cost_paise)

    rows = (
        await session.execute(
            select(
                CallCostItemModel.component,
                CallCostItemModel.provider,
                CallCostItemModel.model,
                _sum(CallCostItemModel.units).label("units"),
                charged.label("charged_paise"),
                our_cost.label("our_cost_paise"),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)).label(
                    "calls"
                ),
                # The rate actually applied. Averaged across the group because a
                # rate change mid-window means there is no single one; a spread
                # between this and the rate card is the thing worth seeing.
                func.avg(CallCostItemModel.unit_rate_mpaise).label("rate_mpaise"),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(*conditions)
            .group_by(
                CallCostItemModel.component,
                CallCostItemModel.provider,
                CallCostItemModel.model,
            )
            .order_by(desc(charged))
        )
    ).all()

    units_by_rate = await _open_rate_units(session)

    models: list[dict] = []
    for row in rows:
        provider = row.provider or ""
        model = row.model or ""
        unit = (
            units_by_rate.get((row.component, provider, model))
            # A model with no rate row of its own was priced at the
            # provider-wide fallback, so it is quoted in that rate's unit.
            or units_by_rate.get((row.component, provider, ""))
            or _FALLBACK_UNIT_BY_COMPONENT.get(row.component, "")
        )
        charged_paise = int(row.charged_paise or 0)
        our_cost_paise = int(row.our_cost_paise or 0)
        models.append(
            {
                "component": row.component,
                # The platform fee has no vendor and no model. Named rather
                # than left blank so a reader is not left wondering whether it
                # is missing data.
                "provider": provider or ("—" if row.component == "platform" else ""),
                "model": model or "(unspecified)",
                "calls": int(row.calls or 0),
                "units": int(row.units or 0),
                "unit": unit,
                "unit_label": _UNIT_LABEL.get(unit, "units"),
                "rate_mpaise": int(row.rate_mpaise or 0),
                "charged_paise": charged_paise,
                "our_cost_paise": our_cost_paise,
                "margin_paise": charged_paise - our_cost_paise,
                # None rather than 0 on a zero-revenue row: a margin share of
                # nothing is undefined, and "0%" would read as "we made none".
                "margin_pct": (
                    (charged_paise - our_cost_paise) / charged_paise
                    if charged_paise
                    else None
                ),
                "paise_per_call": (
                    round(charged_paise / int(row.calls), 2) if row.calls else None
                ),
            }
        )

    by_component: dict[str, dict] = {}
    for entry in models:
        bucket = by_component.setdefault(
            entry["component"],
            {
                "component": entry["component"],
                "charged_paise": 0,
                "our_cost_paise": 0,
                "margin_paise": 0,
                "models": 0,
            },
        )
        bucket["charged_paise"] += entry["charged_paise"]
        bucket["our_cost_paise"] += entry["our_cost_paise"]
        bucket["margin_paise"] += entry["margin_paise"]
        bucket["models"] += 1

    charged_total = sum(entry["charged_paise"] for entry in models)
    cost_total = sum(entry["our_cost_paise"] for entry in models)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "models": models,
        "by_component": sorted(
            by_component.values(), key=lambda b: b["charged_paise"], reverse=True
        ),
        "totals": {
            "charged_paise": charged_total,
            "our_cost_paise": cost_total,
            "margin_paise": charged_total - cost_total,
            "margin_pct": (
                (charged_total - cost_total) / charged_total if charged_total else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Payments and top-ups
#
# Two different questions live here, and answering them off the same timestamp
# is how a payments screen ends up contradicting itself:
#
# * **How many orders converted?** That is a question about a *cohort* — the
#   orders started in this window — and it is keyed on ``created_at``. An
#   order created on Tuesday and paid on Wednesday belongs to Tuesday's cohort
#   either way, which is what keeps the rate stable as the window widens.
# * **How much money came in?** That is keyed on ``paid_at``, because the cash
#   arrived when it arrived. Bucketing collections by ``created_at`` would
#   credit a day for money it did not receive.
#
# The ledger is reported beside both. A top-up can reach an account without a
# payment row — staff credit, trial grants, a manual adjustment — so the gap
# between "collected" and "credited" is a real reconciliation signal rather
# than a bug in one of the two numbers.
# ---------------------------------------------------------------------------


async def payments_summary(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    granularity: str = "day",
    organization_id: int | None = None,
) -> dict:
    """Orders started, orders paid, money collected, credit granted."""
    start_utc, _ = ist_day_bounds_utc(start)
    _, end_utc = ist_day_bounds_utc(end)

    def _scope(conditions: list):
        if organization_id is not None:
            conditions.append(PaymentModel.organization_id == organization_id)
        return conditions

    # --- The cohort: orders started in this window, by what became of them.
    cohort_rows = (
        await session.execute(
            select(
                PaymentModel.status,
                func.count(PaymentModel.id).label("orders"),
                _sum(PaymentModel.amount_paise).label("net_paise"),
            )
            .where(
                *_scope(
                    [
                        PaymentModel.created_at >= start_utc,
                        PaymentModel.created_at < end_utc,
                    ]
                )
            )
            .group_by(PaymentModel.status)
        )
    ).all()
    by_status = {r.status: int(r.orders) for r in cohort_rows}
    started = sum(by_status.values())
    converted = by_status.get("paid", 0)

    # --- The money: keyed on when it settled, not when it was asked for.
    collected_conditions = _scope(
        [
            PaymentModel.status == "paid",
            PaymentModel.paid_at >= start_utc,
            PaymentModel.paid_at < end_utc,
        ]
    )

    # gross_paise is nullable — payments taken before GST existed have no
    # split recorded. Falling back to amount_paise rather than to zero: those
    # payments were zero-rated, so gross and net were the same figure.
    gross = func.coalesce(PaymentModel.gross_paise, PaymentModel.amount_paise)

    totals_row = (
        await session.execute(
            select(
                func.count(PaymentModel.id).label("payments"),
                _sum(PaymentModel.amount_paise).label("net_paise"),
                _sum(gross).label("gross_paise"),
                _sum(
                    func.coalesce(PaymentModel.cgst_paise, 0)
                    + func.coalesce(PaymentModel.sgst_paise, 0)
                    + func.coalesce(PaymentModel.igst_paise, 0)
                ).label("tax_paise"),
                func.count(func.distinct(PaymentModel.organization_id)).label(
                    "paying_accounts"
                ),
            ).where(*collected_conditions)
        )
    ).one()

    payments = int(totals_row.payments or 0)
    net_paise = int(totals_row.net_paise or 0)

    period = _ist_period(PaymentModel.paid_at, granularity)
    series = [
        {
            "period": row.period.date().isoformat(),
            "payments": int(row.payments or 0),
            "net_paise": int(row.net_paise or 0),
            "gross_paise": int(row.gross_paise or 0),
            "accounts": int(row.accounts or 0),
        }
        for row in (
            await session.execute(
                select(
                    period.label("period"),
                    func.count(PaymentModel.id).label("payments"),
                    _sum(PaymentModel.amount_paise).label("net_paise"),
                    _sum(gross).label("gross_paise"),
                    func.count(func.distinct(PaymentModel.organization_id)).label(
                        "accounts"
                    ),
                )
                .where(*collected_conditions)
                .group_by(period)
                .order_by(period)
            )
        ).all()
    ]

    # --- Who paid. Ordered by value, because the question behind this table is
    # always "which accounts are carrying the revenue".
    top_accounts = [
        {
            "organization_id": row.organization_id,
            # Same fallback the rest of this module uses: an account that has
            # not filled in a billing name is still identifiable by the id its
            # auth provider knows it as.
            "name": row.billing_name or row.provider_id,
            "payments": int(row.payments or 0),
            "net_paise": int(row.net_paise or 0),
            "last_paid_at": row.last_paid_at.isoformat() if row.last_paid_at else None,
        }
        for row in (
            await session.execute(
                select(
                    PaymentModel.organization_id,
                    OrganizationModel.billing_name,
                    OrganizationModel.provider_id,
                    func.count(PaymentModel.id).label("payments"),
                    _sum(PaymentModel.amount_paise).label("net_paise"),
                    func.max(PaymentModel.paid_at).label("last_paid_at"),
                )
                .select_from(PaymentModel)
                .join(
                    OrganizationModel,
                    OrganizationModel.id == PaymentModel.organization_id,
                )
                .where(*collected_conditions)
                .group_by(PaymentModel.organization_id, OrganizationModel.id)
                .order_by(desc(_sum(PaymentModel.amount_paise)))
                .limit(10)
            )
        ).all()
    ]

    # --- The ledger, for reconciliation. Every TOPUP row, whatever produced it.
    ledger_conditions = [
        CreditLedgerModel.created_at >= start_utc,
        CreditLedgerModel.created_at < end_utc,
        CreditLedgerModel.kind == CreditLedgerKind.TOPUP.value,
    ]
    if organization_id is not None:
        ledger_conditions.append(CreditLedgerModel.organization_id == organization_id)

    ledger_row = (
        await session.execute(
            select(
                func.count(CreditLedgerModel.id).label("entries"),
                _sum(CreditLedgerModel.delta_paise).label("credited_paise"),
            ).where(*ledger_conditions)
        )
    ).one()
    credited_paise = int(ledger_row.credited_paise or 0)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granularity": granularity,
        "collected": {
            "payments": payments,
            # Net of GST — this is what reaches the ledger as credit.
            "net_paise": net_paise,
            # What the customer was actually charged, tax included.
            "gross_paise": int(totals_row.gross_paise or 0),
            "tax_paise": int(totals_row.tax_paise or 0),
            "paying_accounts": int(totals_row.paying_accounts or 0),
            "average_topup_paise": (round(net_paise / payments) if payments else None),
        },
        "cohort": {
            "started": started,
            "paid": converted,
            "failed": by_status.get("failed", 0),
            # Neither paid nor failed: the customer opened the checkout and
            # walked away. Worth its own number — it is the only one of the
            # three that a change to the payment page can move.
            "abandoned": by_status.get("created", 0),
            # None rather than 0 on an empty window: no orders is not a
            # conversion rate of zero.
            "conversion": (converted / started) if started else None,
        },
        "series": series,
        "top_accounts": top_accounts,
        "ledger": {
            "entries": int(ledger_row.entries or 0),
            "credited_paise": credited_paise,
            # Credit granted that no settled payment paid for: staff top-ups,
            # promotional credit, a manual correction. Not an error — but if it
            # is large and unexplained, it is the first place to look.
            "unpaid_credit_paise": credited_paise - net_paise,
        },
    }

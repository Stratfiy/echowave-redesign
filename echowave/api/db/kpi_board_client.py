"""Queries behind the super-admin KPI board.

Every function here answers one KPI from the pricing spec (section 9) and
returns plain numbers or small dicts; ``services/billing/kpi_board.py`` puts
them on the board with their definitions and previous-period comparisons.

Two conventions hold throughout:

* **Flow** queries take an inclusive IST day range ``(start, end)`` and count
  what happened inside it. **Point** queries take an instant ``at`` and
  describe the state of the world then, so the same query gives "now" and
  "at the start of the window" without a second code path.
* **Customers only.** Internal accounts (``organizations.internal_billing``)
  run demos and QA on their own platform; counting them as revenue, signups
  or churn would flatter or frighten every number on the screen. Every query
  that touches an organisation joins it and drops those rows. That is one
  filter applied consistently rather than a per-query afterthought, which is
  why it lives in ``_customers()`` and nowhere else.

Aggregate-only by design: nothing here returns a per-call or per-org row to
the API, so the cost of a query is bounded by the range, not by traffic.
Cross-account, and only reachable from a superuser-gated route.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentEventModel,
    APIKeyModel,
    AppInteractionModel,
    BillingAuditLogModel,
    CallCostItemModel,
    CallTurnMetricModel,
    CreditLedgerModel,
    DataAccessLogModel,
    ErasureRequestModel,
    EvalResultModel,
    KnowledgeBaseDocumentModel,
    OrganizationModel,
    PartnerCommissionModel,
    PartnerStatementModel,
    PaymentMandateModel,
    PaymentModel,
    TaxDocumentModel,
    WebhookDeliveryModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    CallType,
    CreditLedgerKind,
    MandateStatus,
    PartnerStatementStatus,
    WorkflowRunMode,
    WorkflowRunState,
)
from api.services.billing.rollup import ist_day_bounds_utc

SECONDS_PER_MINUTE = 60
MONTHS_PER_YEAR = 12

#: An API key nobody has used for this long is one to rotate or archive.
STALE_KEY_AFTER_DAYS = 90

#: The purposes and statuses that make a mandate "a subscription that pays".
#: Mirrors ``mandates.PURPOSE_STARTER_PLAN`` and ``MandateStatus.authorised``
#: without importing the service layer into a DB client.
PLAN_PURPOSE = "starter_plan"
ANNUAL = "annual"


def _range_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """An inclusive IST day range as the UTC half-open window it really is."""
    lo, _ = ist_day_bounds_utc(start)
    _, hi = ist_day_bounds_utc(end)
    return lo, hi


def end_of_day_utc(day: date) -> datetime:
    """The instant an IST day ends: the ``at`` for a point-in-time KPI."""
    _, hi = ist_day_bounds_utc(day)
    return hi


def _customers():
    """Organisations that are customers rather than our own accounts."""
    return OrganizationModel.internal_billing.is_(False)


def _customer_ids():
    return select(OrganizationModel.id).where(_customers())


def _paise_to_credits(paise: int) -> int:
    """Credits from paise at the fixed 1 credit = ₹0.50 (KAN-52)."""
    return paise // 50


# ---------------------------------------------------------------------------
# 9.1 Revenue
# ---------------------------------------------------------------------------


def _mrr_expr():
    """A mandate's monthly recurring value: annual collections spread over 12."""
    return case(
        (
            PaymentMandateModel.billing_period == ANNUAL,
            PaymentMandateModel.price_paise / MONTHS_PER_YEAR,
        ),
        else_=PaymentMandateModel.price_paise,
    )


def _active_plan_mandates(at: datetime):
    """Plan mandates that were authorised and not cancelled at ``at``."""
    started = func.coalesce(
        PaymentMandateModel.authorised_at, PaymentMandateModel.created_at
    )
    return [
        PaymentMandateModel.purpose == PLAN_PURPOSE,
        started <= at,
        or_(
            PaymentMandateModel.cancelled_at.is_(None),
            PaymentMandateModel.cancelled_at > at,
        ),
        # Cancelled or halted after ``at`` still counted then; a mandate that
        # never reached an authorised state never counted at all.
        or_(
            PaymentMandateModel.status.in_(MandateStatus.authorised()),
            PaymentMandateModel.cancelled_at.isnot(None),
        ),
        PaymentMandateModel.organization_id.in_(_customer_ids()),
    ]


async def mrr_by_org(session: AsyncSession, *, at: datetime) -> dict[int, int]:
    """``{organization_id: mrr_paise}`` for every account subscribed at ``at``."""
    rows = (
        await session.execute(
            select(
                PaymentMandateModel.organization_id,
                func.sum(_mrr_expr()).label("mrr"),
            )
            .where(*_active_plan_mandates(at))
            .group_by(PaymentMandateModel.organization_id)
        )
    ).all()
    return {int(row.organization_id): int(row.mrr or 0) for row in rows}


async def paying_orgs_by_plan(session: AsyncSession, *, at: datetime) -> dict[str, int]:
    """How many subscribed accounts sit on each plan at ``at``."""
    rows = (
        await session.execute(
            select(
                PaymentMandateModel.plan_code,
                func.count(func.distinct(PaymentMandateModel.organization_id)),
            )
            .where(*_active_plan_mandates(at))
            .group_by(PaymentMandateModel.plan_code)
        )
    ).all()
    return {str(code or "unknown"): int(n) for code, n in rows}


async def plan_mandate_history(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Every plan mandate authorised or cancelled in the range, with what the
    same account held before it. The raw material for upgrades, downgrades
    and logo churn; small because it is bounded to the mandates that moved."""
    lo, hi = _range_utc(start, end)
    started = func.coalesce(
        PaymentMandateModel.authorised_at, PaymentMandateModel.created_at
    )
    rows = (
        await session.execute(
            select(
                PaymentMandateModel.organization_id,
                PaymentMandateModel.plan_code,
                PaymentMandateModel.price_paise,
                PaymentMandateModel.billing_period,
                started.label("started_at"),
                PaymentMandateModel.cancelled_at,
                PaymentMandateModel.status,
            )
            .where(
                PaymentMandateModel.purpose == PLAN_PURPOSE,
                PaymentMandateModel.organization_id.in_(_customer_ids()),
                or_(
                    and_(started >= lo, started < hi),
                    and_(
                        PaymentMandateModel.cancelled_at >= lo,
                        PaymentMandateModel.cancelled_at < hi,
                    ),
                ),
            )
            .order_by(PaymentMandateModel.organization_id, started)
        )
    ).all()
    return [dict(row._mapping) for row in rows]


async def previous_mandate_price(
    session: AsyncSession, *, organization_id: int, before: datetime
) -> int | None:
    """The monthly value of the plan an account held before ``before``."""
    started = func.coalesce(
        PaymentMandateModel.authorised_at, PaymentMandateModel.created_at
    )
    return await session.scalar(
        select(_mrr_expr())
        .where(
            PaymentMandateModel.purpose == PLAN_PURPOSE,
            PaymentMandateModel.organization_id == organization_id,
            started < before,
            PaymentMandateModel.cancelled_at.isnot(None),
        )
        .order_by(started.desc())
        .limit(1)
    )


def _paid_payments(lo: datetime, hi: datetime):
    return [
        PaymentModel.status == "paid",
        PaymentModel.paid_at >= lo,
        PaymentModel.paid_at < hi,
        PaymentModel.organization_id.in_(_customer_ids()),
    ]


async def topup_sales(session: AsyncSession, *, start: date, end: date) -> dict:
    """Top-ups paid in the range: count, buyers, rupees and credits granted.

    A payment is a top-up when the ledger row it credited is one — plan
    collections also produce payments, and those are subscription revenue.
    """
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count(PaymentModel.id),
                func.count(func.distinct(PaymentModel.organization_id)),
                func.coalesce(func.sum(PaymentModel.amount_paise), 0),
                func.coalesce(
                    func.sum(
                        PaymentModel.amount_paise
                        + func.coalesce(PaymentModel.bonus_paise, 0)
                    ),
                    0,
                ),
            )
            .select_from(PaymentModel)
            .join(
                CreditLedgerModel, CreditLedgerModel.id == PaymentModel.credit_ledger_id
            )
            .where(
                *_paid_payments(lo, hi),
                CreditLedgerModel.kind == CreditLedgerKind.TOPUP.value,
            )
        )
    ).one()
    return {
        "count": int(row[0]),
        "buyers": int(row[1]),
        "amount_paise": int(row[2]),
        "credits": _paise_to_credits(int(row[3])),
    }


async def paying_org_count(session: AsyncSession, *, start: date, end: date) -> int:
    """Accounts that paid us anything in the range or held a plan at its end."""
    lo, hi = _range_utc(start, end)
    paid = select(PaymentModel.organization_id).where(*_paid_payments(lo, hi))
    subscribed = select(PaymentMandateModel.organization_id).where(
        *_active_plan_mandates(hi)
    )
    return int(
        await session.scalar(
            select(func.count(func.distinct(OrganizationModel.id))).where(
                or_(
                    OrganizationModel.id.in_(paid),
                    OrganizationModel.id.in_(subscribed),
                )
            )
        )
        or 0
    )


async def deferred_revenue_paise(session: AsyncSession, *, at: datetime) -> int:
    """Unused prepaid balance across customers at ``at``: what we still owe.

    The latest ledger row per account carries its balance, so this is a
    max-id-per-org lookup rather than a re-sum of every row ever written.
    """
    latest = (
        select(
            CreditLedgerModel.organization_id,
            func.max(CreditLedgerModel.id).label("last_id"),
        )
        .where(
            CreditLedgerModel.created_at < at,
            CreditLedgerModel.organization_id.in_(_customer_ids()),
        )
        .group_by(CreditLedgerModel.organization_id)
        .subquery()
    )
    total = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            CreditLedgerModel.balance_after_paise > 0,
                            CreditLedgerModel.balance_after_paise,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        )
        .select_from(CreditLedgerModel)
        .join(latest, latest.c.last_id == CreditLedgerModel.id)
    )
    return int(total or 0)


def _documents_issued(lo: datetime, hi: datetime):
    return [
        TaxDocumentModel.issued_at >= lo,
        TaxDocumentModel.issued_at < hi,
        TaxDocumentModel.organization_id.in_(_customer_ids()),
    ]


async def export_revenue(session: AsyncSession, *, start: date, end: date) -> dict:
    """Zero-rated export invoices issued in the range."""
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count(TaxDocumentModel.id),
                func.coalesce(func.sum(TaxDocumentModel.total_paise), 0),
            ).where(
                *_documents_issued(lo, hi),
                TaxDocumentModel.supply_type == "export",
                TaxDocumentModel.kind != "credit_note",
            )
        )
    ).one()
    return {"count": int(row[0]), "total_paise": int(row[1])}


async def gst_collected(session: AsyncSession, *, start: date, end: date) -> dict:
    """Tax on documents issued in the range, credit notes netted off."""
    lo, hi = _range_utc(start, end)
    sign = case((TaxDocumentModel.kind == "credit_note", -1), else_=1)
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(sign * TaxDocumentModel.cgst_paise), 0),
                func.coalesce(func.sum(sign * TaxDocumentModel.sgst_paise), 0),
                func.coalesce(func.sum(sign * TaxDocumentModel.igst_paise), 0),
            ).where(*_documents_issued(lo, hi))
        )
    ).one()
    return {
        "cgst_paise": int(row[0]),
        "sgst_paise": int(row[1]),
        "igst_paise": int(row[2]),
    }


async def credit_notes(session: AsyncSession, *, start: date, end: date) -> dict:
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count(TaxDocumentModel.id),
                func.coalesce(func.sum(TaxDocumentModel.total_paise), 0),
            ).where(*_documents_issued(lo, hi), TaxDocumentModel.kind == "credit_note")
        )
    ).one()
    return {"count": int(row[0]), "total_paise": int(row[1])}


async def partner_liability(session: AsyncSession, *, at: datetime) -> dict:
    """Commission statements accrued and not yet paid at ``at``."""
    row = (
        await session.execute(
            select(
                func.count(PartnerStatementModel.id),
                func.coalesce(func.sum(PartnerStatementModel.amount_paise), 0),
            ).where(
                PartnerStatementModel.status.in_(
                    (
                        PartnerStatementStatus.DRAFT.value,
                        PartnerStatementStatus.ISSUED.value,
                    )
                ),
                PartnerStatementModel.generated_at < at,
                or_(
                    PartnerStatementModel.paid_at.is_(None),
                    PartnerStatementModel.paid_at >= at,
                ),
            )
        )
    ).one()
    return {"count": int(row[0]), "amount_paise": int(row[1])}


# ---------------------------------------------------------------------------
# 9.2 Customers and funnel
# ---------------------------------------------------------------------------


def _signups(lo: datetime, hi: datetime):
    return [
        OrganizationModel.created_at >= lo,
        OrganizationModel.created_at < hi,
        _customers(),
    ]


async def signups_by_source(session: AsyncSession, *, start: date, end: date) -> dict:
    """New customer accounts in the range, by how they arrived.

    Three sources are knowable from the database: a referral code, a champion
    (partner) attribution, and everything else. Ads and campus attribution
    need UTM capture that does not exist yet; they are named on the board as
    missing rather than folded into "organic", where nobody would notice.
    """
    lo, hi = _range_utc(start, end)
    championed = select(PartnerCommissionModel.organization_id)
    source = case(
        (OrganizationModel.id.in_(championed), "champion"),
        (OrganizationModel.referred_by_organization_id.isnot(None), "referral"),
        else_="organic",
    )
    rows = (
        await session.execute(
            select(source.label("source"), func.count(OrganizationModel.id))
            .where(*_signups(lo, hi))
            .group_by(source)
        )
    ).all()
    by_source = {"organic": 0, "referral": 0, "champion": 0}
    for name, n in rows:
        by_source[str(name)] = int(n)
    return {"total": sum(by_source.values()), "by_source": by_source}


def _first_run_per_org():
    return (
        select(
            WorkflowModel.organization_id.label("organization_id"),
            func.min(WorkflowRunModel.created_at).label("first_run_at"),
        )
        .select_from(WorkflowRunModel)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .group_by(WorkflowModel.organization_id)
        .subquery()
    )


async def activation(session: AsyncSession, *, start: date, end: date) -> dict:
    """Of the accounts that signed up in the range, how many ran a bot within
    seven days. The spec's "first bot live" is the first run: a bot that has
    never been run is not live in any sense a customer would recognise."""
    lo, hi = _range_utc(start, end)
    first = _first_run_per_org()
    row = (
        await session.execute(
            select(
                func.count(OrganizationModel.id),
                func.count(
                    case(
                        (
                            first.c.first_run_at
                            <= OrganizationModel.created_at + timedelta(days=7),
                            1,
                        )
                    )
                ),
            )
            .select_from(OrganizationModel)
            .outerjoin(first, first.c.organization_id == OrganizationModel.id)
            .where(*_signups(lo, hi))
        )
    ).one()
    return {"signups": int(row[0]), "activated": int(row[1])}


def _first_paid_per_org():
    return (
        select(
            PaymentModel.organization_id.label("organization_id"),
            func.min(PaymentModel.paid_at).label("first_paid_at"),
        )
        .where(PaymentModel.status == "paid")
        .group_by(PaymentModel.organization_id)
        .subquery()
    )


async def free_to_paid(session: AsyncSession, *, start: date, end: date) -> dict:
    """Signups in the range that paid anything within thirty days."""
    lo, hi = _range_utc(start, end)
    first = _first_paid_per_org()
    row = (
        await session.execute(
            select(
                func.count(OrganizationModel.id),
                func.count(
                    case(
                        (
                            first.c.first_paid_at
                            <= OrganizationModel.created_at + timedelta(days=30),
                            1,
                        )
                    )
                ),
            )
            .select_from(OrganizationModel)
            .outerjoin(first, first.c.organization_id == OrganizationModel.id)
            .where(*_signups(lo, hi))
        )
    ).one()
    return {"signups": int(row[0]), "paid": int(row[1])}


async def median_days_to_first_payment(
    session: AsyncSession, *, start: date, end: date
) -> float | None:
    """Median days from signup to first payment, for accounts whose first
    payment landed in the range."""
    lo, hi = _range_utc(start, end)
    first = _first_paid_per_org()
    days = (
        func.extract("epoch", first.c.first_paid_at - OrganizationModel.created_at)
        / 86400.0
    )
    value = await session.scalar(
        select(func.percentile_cont(0.5).within_group(days))
        .select_from(first)
        .join(OrganizationModel, OrganizationModel.id == first.c.organization_id)
        .where(
            first.c.first_paid_at >= lo,
            first.c.first_paid_at < hi,
            _customers(),
        )
    )
    return float(value) if value is not None else None


async def champion_accounts(session: AsyncSession, *, start: date, end: date) -> int:
    """Accounts a champion or partner brought in during the range."""
    lo, hi = _range_utc(start, end)
    return int(
        await session.scalar(
            select(
                func.count(func.distinct(PartnerCommissionModel.organization_id))
            ).where(
                PartnerCommissionModel.effective_from >= lo,
                PartnerCommissionModel.effective_from < hi,
                PartnerCommissionModel.organization_id.in_(_customer_ids()),
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# 9.3 Usage and unit economics
# ---------------------------------------------------------------------------


def _costed_runs(lo: datetime, hi: datetime):
    return [
        WorkflowRunModel.costed_at.isnot(None),
        WorkflowRunModel.created_at >= lo,
        WorkflowRunModel.created_at < hi,
        func.coalesce(WorkflowRunModel.billable_seconds, 0) > 0,
        WorkflowModel.organization_id.in_(_customer_ids()),
    ]


def _runs_with_org():
    return select(WorkflowRunModel).join(
        WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id
    )


async def overage_credits(session: AsyncSession, *, start: date, end: date) -> dict:
    """Credits billed on calls priced past the plan (``overage_applied``)."""
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0),
                func.count(WorkflowRunModel.id),
                func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
            )
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_costed_runs(lo, hi), WorkflowRunModel.overage_applied.is_(True))
        )
    ).one()
    return {
        "credits": _paise_to_credits(int(row[0])),
        "calls": int(row[1]),
        "minutes": int(row[2]) // SECONDS_PER_MINUTE,
    }


async def voice_minutes(session: AsyncSession, *, start: date, end: date) -> dict:
    """Connected minutes on costed calls, by language and by carrier."""
    lo, hi = _range_utc(start, end)
    base = (
        select(
            func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
            func.count(WorkflowRunModel.id),
        )
        .select_from(WorkflowRunModel)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(*_costed_runs(lo, hi))
    )
    seconds, calls = (await session.execute(base)).one()

    async def _by(column):
        rows = (
            await session.execute(
                select(
                    func.coalesce(column, "unknown"),
                    func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
                )
                .select_from(WorkflowRunModel)
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .where(*_costed_runs(lo, hi))
                .group_by(column)
                .order_by(func.sum(WorkflowRunModel.billable_seconds).desc())
            )
        ).all()
        return {str(k): int(s) // SECONDS_PER_MINUTE for k, s in rows}

    return {
        "minutes": int(seconds) // SECONDS_PER_MINUTE,
        "calls": int(calls),
        "by_language": await _by(WorkflowRunModel.language),
        "by_provider": await _by(WorkflowRunModel.mode),
    }


def _ledger_in(lo: datetime, hi: datetime):
    return [
        CreditLedgerModel.created_at >= lo,
        CreditLedgerModel.created_at < hi,
        CreditLedgerModel.organization_id.in_(_customer_ids()),
    ]


async def text_events(
    session: AsyncSession, *, start: date, end: date, kinds: tuple[str, ...]
) -> dict[str, int]:
    """Metered text events in the range, counted by kind (the ledger's
    ``ref_type`` for an event charge is the event's name; see billing.events)."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(CreditLedgerModel.ref_type, func.count(CreditLedgerModel.id))
            .where(
                *_ledger_in(lo, hi),
                CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
                CreditLedgerModel.ref_type.in_(kinds),
            )
            .group_by(CreditLedgerModel.ref_type)
        )
    ).all()
    counts = {kind: 0 for kind in kinds}
    for ref_type, n in rows:
        counts[str(ref_type)] = int(n)
    return counts


#: Ledger kinds that spend credit, as opposed to granting or reserving it.
CONSUMING_KINDS = (
    CreditLedgerKind.USAGE.value,
    CreditLedgerKind.MESSAGE.value,
    CreditLedgerKind.EMBEDDING_INGEST.value,
    CreditLedgerKind.RENTAL.value,
)

#: Ledger kinds that put paid-for credit on an account.
SOLD_KINDS = (CreditLedgerKind.TOPUP.value, CreditLedgerKind.PLAN.value)


async def credits_consumed(session: AsyncSession, *, start: date, end: date) -> dict:
    """Credits spent in the range, by what spent them."""
    lo, hi = _range_utc(start, end)
    bucket = case(
        (CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value, "number_rental"),
        (
            CreditLedgerModel.kind == CreditLedgerKind.EMBEDDING_INGEST.value,
            "knowledge_ingest",
        ),
        (CreditLedgerModel.kind == CreditLedgerKind.MESSAGE.value, "message"),
        (CreditLedgerModel.ref_type == "workflow_run", "voice"),
        else_=func.coalesce(CreditLedgerModel.ref_type, "other"),
    )
    rows = (
        await session.execute(
            select(bucket.label("bucket"), func.sum(-CreditLedgerModel.delta_paise))
            .where(
                *_ledger_in(lo, hi),
                CreditLedgerModel.kind.in_(CONSUMING_KINDS),
                CreditLedgerModel.delta_paise < 0,
            )
            .group_by(bucket)
        )
    ).all()
    by_kind = {str(k): _paise_to_credits(int(p or 0)) for k, p in rows}
    return {"credits": sum(by_kind.values()), "by_kind": by_kind}


async def credits_sold(session: AsyncSession, *, start: date, end: date) -> dict:
    """Credits granted for money in the range: plan cycles and top-ups."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(CreditLedgerModel.kind, func.sum(CreditLedgerModel.delta_paise))
            .where(
                *_ledger_in(lo, hi),
                CreditLedgerModel.kind.in_(SOLD_KINDS),
                CreditLedgerModel.delta_paise > 0,
            )
            .group_by(CreditLedgerModel.kind)
        )
    ).all()
    by_kind = {str(k): _paise_to_credits(int(p or 0)) for k, p in rows}
    return {"credits": sum(by_kind.values()), "by_kind": by_kind}


async def consumption_by_plan(
    session: AsyncSession, *, start: date, end: date
) -> dict[str, dict]:
    """Credits spent in the range grouped by the plan the account holds at the
    end of it, with the credits those plans granted in the same range."""
    lo, hi = _range_utc(start, end)
    plan_of = (
        select(
            PaymentMandateModel.organization_id.label("organization_id"),
            func.max(PaymentMandateModel.plan_code).label("plan_code"),
        )
        .where(*_active_plan_mandates(hi))
        .group_by(PaymentMandateModel.organization_id)
        .subquery()
    )
    plan = func.coalesce(plan_of.c.plan_code, "free")
    spent = func.sum(
        case(
            (
                and_(
                    CreditLedgerModel.kind.in_(CONSUMING_KINDS),
                    CreditLedgerModel.delta_paise < 0,
                ),
                -CreditLedgerModel.delta_paise,
            ),
            else_=0,
        )
    )
    granted = func.sum(
        case(
            (
                and_(
                    CreditLedgerModel.kind.in_(SOLD_KINDS),
                    CreditLedgerModel.delta_paise > 0,
                ),
                CreditLedgerModel.delta_paise,
            ),
            else_=0,
        )
    )
    rows = (
        await session.execute(
            select(plan.label("plan"), spent, granted)
            .select_from(CreditLedgerModel)
            .outerjoin(
                plan_of, plan_of.c.organization_id == CreditLedgerModel.organization_id
            )
            .where(*_ledger_in(lo, hi))
            .group_by(plan)
        )
    ).all()
    return {
        str(code): {
            "consumed": _paise_to_credits(int(s or 0)),
            "sold": _paise_to_credits(int(g or 0)),
        }
        for code, s, g in rows
    }


async def voice_economics(session: AsyncSession, *, start: date, end: date) -> dict:
    """What costed calls earned and cost in the range, and the Indic subset
    the ₹2.78-a-minute assumption is about."""
    lo, hi = _range_utc(start, end)
    indic = and_(
        WorkflowRunModel.language.isnot(None),
        func.lower(WorkflowRunModel.language).notlike("en%"),
    )
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
                func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0),
                func.coalesce(func.sum(WorkflowRunModel.total_provider_cost_paise), 0),
                func.coalesce(
                    func.sum(case((indic, WorkflowRunModel.billable_seconds), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (indic, WorkflowRunModel.total_provider_cost_paise),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_costed_runs(lo, hi))
        )
    ).one()
    by_language = (
        await session.execute(
            select(
                func.coalesce(WorkflowRunModel.language, "unknown"),
                func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
                func.coalesce(func.sum(WorkflowRunModel.total_provider_cost_paise), 0),
            )
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_costed_runs(lo, hi))
            .group_by(WorkflowRunModel.language)
        )
    ).all()
    return {
        "billable_seconds": int(row[0]),
        "revenue_paise": int(row[1]),
        "provider_cost_paise": int(row[2]),
        "indic_seconds": int(row[3]),
        "indic_provider_cost_paise": int(row[4]),
        "by_language": [
            {
                "language": str(lang),
                "billable_seconds": int(secs),
                "provider_cost_paise": int(cost),
            }
            for lang, secs, cost in by_language
        ],
    }


async def provider_spend(
    session: AsyncSession, *, start: date, end: date, provider_like: str
) -> dict:
    """Provider cost on calls in the range for one vendor (``ilike`` match)."""
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            CallCostItemModel.provider_cost_paise,
                            CallCostItemModel.cost_paise,
                        )
                    ),
                    0,
                ),
                func.count(func.distinct(CallCostItemModel.workflow_run_id)),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(
                *_costed_runs(lo, hi),
                CallCostItemModel.provider.ilike(provider_like),
            )
        )
    ).one()
    return {"cost_paise": int(row[0]), "calls": int(row[1])}


async def markup_realised(
    session: AsyncSession, *, start: date, end: date
) -> list[dict]:
    """Per component: what we billed against what the provider charged."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(
                CallCostItemModel.component,
                func.coalesce(func.sum(CallCostItemModel.cost_paise), 0),
                func.coalesce(func.sum(CallCostItemModel.provider_cost_paise), 0),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_costed_runs(lo, hi))
            .group_by(CallCostItemModel.component)
            .order_by(CallCostItemModel.component)
        )
    ).all()
    return [
        {
            "component": str(component),
            "billed_paise": int(billed),
            "provider_cost_paise": int(cost),
        }
        for component, billed, cost in rows
    ]


async def builder_past_allowance(
    session: AsyncSession, *, start: date, end: date, ref_type: str
) -> dict:
    """Builder messages charged past the allowance, and how many accounts paid."""
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count(CreditLedgerModel.id),
                func.count(func.distinct(CreditLedgerModel.organization_id)),
            ).where(
                *_ledger_in(lo, hi),
                CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
                CreditLedgerModel.ref_type == ref_type,
            )
        )
    ).one()
    return {"messages": int(row[0]), "orgs": int(row[1])}


async def knowledge_pages_per_org(session: AsyncSession) -> list[dict]:
    """Active knowledge pages held by each customer account, with its plan."""
    pages = (
        select(
            KnowledgeBaseDocumentModel.organization_id.label("organization_id"),
            func.coalesce(func.sum(KnowledgeBaseDocumentModel.page_count), 0).label(
                "pages"
            ),
        )
        .where(KnowledgeBaseDocumentModel.is_active.is_(True))
        .group_by(KnowledgeBaseDocumentModel.organization_id)
        .subquery()
    )
    plan_of = (
        select(
            PaymentMandateModel.organization_id.label("organization_id"),
            func.max(PaymentMandateModel.plan_code).label("plan_code"),
        )
        .where(
            PaymentMandateModel.purpose == PLAN_PURPOSE,
            PaymentMandateModel.status.in_(MandateStatus.authorised()),
        )
        .group_by(PaymentMandateModel.organization_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                pages.c.organization_id,
                pages.c.pages,
                func.coalesce(plan_of.c.plan_code, "free"),
            )
            .select_from(pages)
            .outerjoin(plan_of, plan_of.c.organization_id == pages.c.organization_id)
            .where(pages.c.organization_id.in_(_customer_ids()))
        )
    ).all()
    return [
        {"organization_id": int(org), "pages": int(p), "plan_code": str(code)}
        for org, p, code in rows
    ]


# ---------------------------------------------------------------------------
# 9.4 Quality and reliability
# ---------------------------------------------------------------------------


def _runs_in(lo: datetime, hi: datetime):
    return [
        WorkflowRunModel.created_at >= lo,
        WorkflowRunModel.created_at < hi,
        WorkflowModel.organization_id.in_(_customer_ids()),
    ]


async def call_outcomes(session: AsyncSession, *, start: date, end: date) -> dict:
    """Answered, completed and transferred counts over the runs in the range."""
    lo, hi = _range_utc(start, end)
    voice = WorkflowRunModel.mode != WorkflowRunMode.TEXTCHAT.value
    inbound = WorkflowRunModel.call_type == CallType.INBOUND.value
    transferred = (
        select(CallTurnMetricModel.workflow_run_id)
        .where(CallTurnMetricModel.tool_called == "transfer_call")
        .distinct()
    )
    row = (
        await session.execute(
            select(
                func.count(WorkflowRunModel.id),
                func.count(case((voice, 1))),
                func.count(case((and_(voice, inbound), 1))),
                func.count(
                    case(
                        (
                            and_(
                                voice, inbound, WorkflowRunModel.answered_at.isnot(None)
                            ),
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            and_(
                                voice,
                                WorkflowRunModel.state
                                == WorkflowRunState.COMPLETED.value,
                            ),
                            1,
                        )
                    )
                ),
                func.count(case((WorkflowRunModel.id.in_(transferred), 1))),
            )
            .select_from(WorkflowRunModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_runs_in(lo, hi))
        )
    ).one()
    return {
        "runs": int(row[0]),
        "calls": int(row[1]),
        "inbound_offered": int(row[2]),
        "inbound_answered": int(row[3]),
        "completed": int(row[4]),
        "transferred": int(row[5]),
    }


async def event_counts(
    session: AsyncSession, *, start: date, end: date, kinds: tuple[str, ...]
) -> dict[str, int]:
    """Agent timeline events of the given kinds raised in the range."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(AgentEventModel.kind, func.count(AgentEventModel.id))
            .where(
                AgentEventModel.at >= lo,
                AgentEventModel.at < hi,
                AgentEventModel.kind.in_(kinds),
                AgentEventModel.organization_id.in_(_customer_ids()),
            )
            .group_by(AgentEventModel.kind)
        )
    ).all()
    counts = {kind: 0 for kind in kinds}
    for kind, n in rows:
        counts[str(kind)] = int(n)
    return counts


def _p(expr, q: float):
    return func.percentile_cont(q).within_group(expr)


async def latency_percentiles(session: AsyncSession, *, start: date, end: date) -> dict:
    """p50 and p95 of each pipeline stage and of the whole first response,
    over the turns of calls in the range."""
    lo, hi = _range_utc(start, end)
    m = CallTurnMetricModel
    stt = m.t_stt_final_ms - m.t_user_stopped_ms
    llm = m.t_llm_first_token_ms - m.t_stt_final_ms
    tts = m.t_tts_first_byte_ms - m.t_llm_first_token_ms
    e2e = m.latency_ms
    row = (
        await session.execute(
            select(
                _p(stt, 0.5),
                _p(stt, 0.95),
                _p(llm, 0.5),
                _p(llm, 0.95),
                _p(tts, 0.5),
                _p(tts, 0.95),
                _p(e2e, 0.5),
                _p(e2e, 0.95),
                func.count(m.id),
            )
            .select_from(m)
            .join(WorkflowRunModel, WorkflowRunModel.id == m.workflow_run_id)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(*_runs_in(lo, hi))
        )
    ).one()

    def _ms(value):
        return int(value) if value is not None else None

    return {
        "turns": int(row[8]),
        "stt": {"p50_ms": _ms(row[0]), "p95_ms": _ms(row[1])},
        "llm": {"p50_ms": _ms(row[2]), "p95_ms": _ms(row[3])},
        "tts": {"p50_ms": _ms(row[4]), "p95_ms": _ms(row[5])},
        "first_response": {"p50_ms": _ms(row[6]), "p95_ms": _ms(row[7])},
    }


async def connector_errors(session: AsyncSession, *, start: date, end: date) -> dict:
    """Connector (tool) calls in the range and how many ended in an error,
    by app. The nearest thing recorded to a provider error rate today."""
    lo, hi = _range_utc(start, end)
    failed = case((AppInteractionModel.error.isnot(None), 1))
    rows = (
        await session.execute(
            select(
                func.coalesce(AppInteractionModel.app, "unknown"),
                func.count(AppInteractionModel.id),
                func.count(failed),
            )
            .where(
                AppInteractionModel.created_at >= lo,
                AppInteractionModel.created_at < hi,
                AppInteractionModel.organization_id.in_(_customer_ids()),
            )
            .group_by(AppInteractionModel.app)
            .order_by(func.count(failed).desc())
        )
    ).all()
    total = sum(int(n) for _, n, _ in rows)
    errors = sum(int(e) for _, _, e in rows)
    return {
        "calls": total,
        "errors": errors,
        "by_app": [
            {"app": str(app), "calls": int(n), "errors": int(e)} for app, n, e in rows
        ],
    }


async def webhook_outcomes(session: AsyncSession, *, start: date, end: date) -> dict:
    """Webhook deliveries created in the range that have finished, by outcome."""
    lo, hi = _range_utc(start, end)
    row = (
        await session.execute(
            select(
                func.count(case((WebhookDeliveryModel.status == "succeeded", 1))),
                func.count(case((WebhookDeliveryModel.status == "dead_letter", 1))),
            ).where(
                WebhookDeliveryModel.created_at >= lo,
                WebhookDeliveryModel.created_at < hi,
                WebhookDeliveryModel.organization_id.in_(_customer_ids()),
            )
        )
    ).one()
    return {"succeeded": int(row[0]), "dead_letter": int(row[1])}


async def eval_outcomes(session: AsyncSession, *, start: date, end: date) -> dict:
    """QA (eval) results finished in the range, by status."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(EvalResultModel.status, func.count(EvalResultModel.id))
            .where(
                EvalResultModel.created_at >= lo,
                EvalResultModel.created_at < hi,
                EvalResultModel.organization_id.in_(_customer_ids()),
            )
            .group_by(EvalResultModel.status)
        )
    ).all()
    return {str(status): int(n) for status, n in rows}


# ---------------------------------------------------------------------------
# 9.5 Trust and admin
# ---------------------------------------------------------------------------


async def superadmin_actions(session: AsyncSession, *, start: date, end: date) -> dict:
    """Audited staff actions on billing in the range, by action."""
    lo, hi = _range_utc(start, end)
    rows = (
        await session.execute(
            select(BillingAuditLogModel.action, func.count(BillingAuditLogModel.id))
            .where(
                BillingAuditLogModel.created_at >= lo,
                BillingAuditLogModel.created_at < hi,
            )
            .group_by(BillingAuditLogModel.action)
            .order_by(func.count(BillingAuditLogModel.id).desc())
        )
    ).all()
    by_action = {str(action): int(n) for action, n in rows}
    return {"total": sum(by_action.values()), "by_action": by_action}


async def data_requests(session: AsyncSession, *, start: date, end: date) -> dict:
    """Erasure requests raised in the range, how many are done, and the
    median hours they took; plus exports logged by the access log."""
    lo, hi = _range_utc(start, end)
    hours = (
        func.extract(
            "epoch", ErasureRequestModel.completed_at - ErasureRequestModel.requested_at
        )
        / 3600.0
    )
    row = (
        await session.execute(
            select(
                func.count(ErasureRequestModel.id),
                func.count(ErasureRequestModel.completed_at),
                _p(hours, 0.5),
            ).where(
                ErasureRequestModel.requested_at >= lo,
                ErasureRequestModel.requested_at < hi,
            )
        )
    ).one()
    exports = await session.scalar(
        select(func.count(DataAccessLogModel.id)).where(
            DataAccessLogModel.created_at >= lo,
            DataAccessLogModel.created_at < hi,
            DataAccessLogModel.action == "export",
        )
    )
    return {
        "erasures": int(row[0]),
        "erasures_completed": int(row[1]),
        "median_hours_to_complete": float(row[2]) if row[2] is not None else None,
        "exports": int(exports or 0),
    }


async def api_key_hygiene(session: AsyncSession, *, at: datetime) -> dict:
    """Live API keys at ``at``: how many, how many are stale, and the oldest."""
    stale_before = at - timedelta(days=STALE_KEY_AFTER_DAYS)
    last_touch = func.coalesce(APIKeyModel.last_used_at, APIKeyModel.created_at)
    live = [
        APIKeyModel.is_active.is_(True),
        APIKeyModel.archived_at.is_(None),
        APIKeyModel.created_at < at,
    ]
    row = (
        await session.execute(
            select(
                func.count(APIKeyModel.id),
                func.count(case((last_touch < stale_before, 1))),
                func.min(APIKeyModel.created_at),
            ).where(*live)
        )
    ).one()
    oldest = row[2]
    return {
        "active": int(row[0]),
        "stale": int(row[1]),
        "oldest_age_days": (at - oldest).days if oldest is not None else None,
    }

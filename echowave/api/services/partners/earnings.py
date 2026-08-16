"""What a partner earned, computed from what their accounts actually spent.

The half of the partner programme that turns a percentage into a number.
``applications.py`` records the arrangement; this reads it back against real
usage and produces a statement somebody can be paid against.

Three decisions carry most of the weight here.

**Accrual is per day, not per period.** ``daily_organization_rollup`` is already
bucketed by IST calendar day, and ``commission_at`` answers for an instant, so
each day is priced at the rate that was in force on that day. A partner
renegotiated from 10% to 15% on the 12th earns 10% on the first eleven days and
15% on the rest, out of one code path. Pricing the period total at the rate
found at period end — the obvious shortcut — silently backdates every
renegotiation to the first of the month.

**Only spend after attribution counts.** An account carries ``referred_at``,
and days before it are skipped. In practice attribution happens at
provisioning, so the account has no history to exclude; the check is here
because the one time it matters is the one time somebody backfills attribution
by hand, and the failure mode is paying commission on a year of spend that
nobody introduced.

**Money is integer paise, rounded once.** Same rule as the rest of billing:
``round_half_up_div`` over the day's basis amount, summed into the line. There
is exactly one rounding step per account per day, and the statement total is
*defined* as the sum of its lines, so a statement reconciles against its own
breakdown the way an invoice does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    DailyOrganizationRollupModel,
    OrganizationModel,
    PartnerStatementLineModel,
    PartnerStatementModel,
)
from api.enums import CommissionBasis, PartnerStatementStatus
from api.services.billing.money import round_half_up_div
from api.services.partners.applications import (
    PartnerError,
    commission_at,
    live_commission,
)

#: Basis points are per 10,000.
_BPS_DENOMINATOR = 10_000


def _basis_amount(row: DailyOrganizationRollupModel, basis: str) -> int:
    """What one day of one account's usage offers the commission.

    Margin is read from the stored column rather than subtracted here: the
    rollup defines ``margin_paise`` as charged minus provider cost and stores
    it so sorting is indexable, and recomputing it in a second place is how the
    two drift.
    """
    if basis == CommissionBasis.PLATFORM_FEE.value:
        # Clamped at zero. A day can run negative — a recost, or a provider
        # invoice arriving above what we charged — and a negative day inside a
        # positive month would quietly reduce what we owe for the days the
        # partner did nothing wrong. Losses on our rate card are ours.
        return max(int(row.margin_paise or 0), 0)
    if basis == CommissionBasis.TOTAL_SPEND.value:
        return max(int(row.charged_paise or 0), 0)
    raise PartnerError(f"Unknown commission basis: {basis}")


async def referred_accounts(
    session: AsyncSession, partner_organization_id: int
) -> list[OrganizationModel]:
    """Every account attributed to this partner, oldest first."""
    return list(
        (
            await session.scalars(
                select(OrganizationModel)
                .where(
                    OrganizationModel.referred_by_organization_id
                    == partner_organization_id
                )
                .order_by(OrganizationModel.referred_at, OrganizationModel.id)
            )
        ).all()
    )


async def compute_lines(
    session: AsyncSession,
    *,
    partner_organization_id: int,
    period_start: date,
    period_end: date,
) -> tuple[list[dict], str]:
    """Per-referred-account earnings for a period, and the basis at period end.

    Returns the arithmetic without writing anything, so a partner can be shown
    what a period is worth so far without a statement existing for it — and so
    the tests can assert the numbers rather than the rows.
    """
    accounts = await referred_accounts(session, partner_organization_id)
    if not accounts:
        commission = await live_commission(session, partner_organization_id)
        return (
            [],
            commission.basis if commission else CommissionBasis.PLATFORM_FEE.value,
        )

    by_id = {account.id: account for account in accounts}

    rollups = list(
        (
            await session.scalars(
                select(DailyOrganizationRollupModel)
                .where(
                    DailyOrganizationRollupModel.organization_id.in_(list(by_id)),
                    DailyOrganizationRollupModel.day >= period_start,
                    DailyOrganizationRollupModel.day <= period_end,
                )
                .order_by(DailyOrganizationRollupModel.day)
            )
        ).all()
    )

    # Cached per day: a month of rollups across forty accounts is one query per
    # distinct day rather than twelve hundred.
    rate_cache: dict[date, tuple[int, str] | None] = {}

    async def rate_on(day: date) -> tuple[int, str] | None:
        if day not in rate_cache:
            # End of the IST day, expressed in UTC-aware terms. A commission
            # granted at 3pm applies to that whole day rather than to none of
            # it: the rollup has no finer resolution, and a rate is negotiated
            # for a period rather than for an hour.
            at = datetime.combine(day, time(23, 59, 59), tzinfo=UTC)
            commission = await commission_at(session, partner_organization_id, at)
            rate_cache[day] = (
                (commission.commission_bps, commission.basis) if commission else None
            )
        return rate_cache[day]

    totals: dict[int, dict] = {}
    basis_at_end = None

    for row in rollups:
        rate = await rate_on(row.day)
        if rate is None:
            # No arrangement in force that day. Not an error: an application
            # approved on the 20th earns nothing for the 19th.
            continue
        commission_bps, basis = rate
        basis_at_end = basis

        account = by_id[row.organization_id]
        if account.referred_at is not None and row.day < account.referred_at.date():
            continue

        basis_paise = _basis_amount(row, basis)
        if basis_paise <= 0:
            continue

        bucket = totals.setdefault(
            account.id,
            {
                "referred_organization_id": account.id,
                "referred_name": account.billing_name or account.provider_id,
                "basis_amount_paise": 0,
                "amount_paise": 0,
            },
        )
        bucket["basis_amount_paise"] += basis_paise
        bucket["amount_paise"] += round_half_up_div(
            basis_paise * commission_bps, _BPS_DENOMINATOR
        )

    if basis_at_end is None:
        commission = await live_commission(session, partner_organization_id)
        basis_at_end = (
            commission.basis if commission else CommissionBasis.PLATFORM_FEE.value
        )

    lines = [line for line in totals.values() if line["amount_paise"] > 0]
    lines.sort(key=lambda line: line["amount_paise"], reverse=True)
    return lines, basis_at_end


async def generate(
    session: AsyncSession,
    *,
    partner_organization_id: int,
    period_start: date,
    period_end: date,
) -> PartnerStatementModel:
    """Write the period's statement, replacing an existing draft.

    Idempotent by design rather than by luck: running it twice for the same
    period produces one statement, because the second run replaces the first
    rather than adding to it. The unique index is what holds under a race; this
    is what makes a re-run the ordinary way to pick up a late rollup.
    """
    if period_end < period_start:
        raise PartnerError("A statement period cannot end before it starts.")

    existing = await session.scalar(
        select(PartnerStatementModel).where(
            PartnerStatementModel.partner_organization_id == partner_organization_id,
            PartnerStatementModel.period_start == period_start,
        )
    )
    if existing is not None and existing.status != PartnerStatementStatus.DRAFT.value:
        raise PartnerError(
            f"The statement for {period_start} was already "
            f"{existing.status}. Issue a correction rather than regenerating it."
        )

    lines, basis = await compute_lines(
        session,
        partner_organization_id=partner_organization_id,
        period_start=period_start,
        period_end=period_end,
    )

    if existing is None:
        # `basis` is set here rather than below with the rest: the flush that
        # gives the statement an id — which the lines need — inserts the row,
        # and the column is NOT NULL.
        statement = PartnerStatementModel(
            partner_organization_id=partner_organization_id,
            period_start=period_start,
            period_end=period_end,
            basis=basis,
        )
        session.add(statement)
        await session.flush()
    else:
        statement = existing
        statement.period_end = period_end
        await session.execute(
            delete(PartnerStatementLineModel).where(
                PartnerStatementLineModel.statement_id == statement.id
            )
        )

    for line in lines:
        session.add(PartnerStatementLineModel(statement_id=statement.id, **line))

    # Defined as the sum of the lines, never computed separately. Same rule as
    # `total_charged_paise` on a call receipt, for the same reason: a total
    # that is derived anywhere else is a total that can disagree with the
    # breakdown printed under it.
    statement.basis = basis
    statement.amount_paise = sum(line["amount_paise"] for line in lines)
    statement.basis_amount_paise = sum(line["basis_amount_paise"] for line in lines)
    statement.generated_at = datetime.now(UTC)
    statement.status = PartnerStatementStatus.DRAFT.value

    await session.flush()
    return statement


async def statements_for(
    session: AsyncSession, partner_organization_id: int
) -> list[PartnerStatementModel]:
    """A partner's statements, newest period first."""
    return list(
        (
            await session.scalars(
                select(PartnerStatementModel)
                .where(
                    PartnerStatementModel.partner_organization_id
                    == partner_organization_id
                )
                .order_by(PartnerStatementModel.period_start.desc())
            )
        ).all()
    )


async def lines_for(
    session: AsyncSession, statement_id: int
) -> list[PartnerStatementLineModel]:
    return list(
        (
            await session.scalars(
                select(PartnerStatementLineModel)
                .where(PartnerStatementLineModel.statement_id == statement_id)
                .order_by(PartnerStatementLineModel.amount_paise.desc())
            )
        ).all()
    )


async def load(
    session: AsyncSession, statement_id: int
) -> PartnerStatementModel | None:
    return await session.get(PartnerStatementModel, statement_id)


async def by_status(session: AsyncSession, status: str) -> list[PartnerStatementModel]:
    """Every statement in one state, oldest period first."""
    if status not in {s.value for s in PartnerStatementStatus}:
        raise PartnerError(f"Unknown statement status: {status}")
    return list(
        (
            await session.scalars(
                select(PartnerStatementModel)
                .where(PartnerStatementModel.status == status)
                .order_by(PartnerStatementModel.period_start)
            )
        ).all()
    )


async def issue(
    session: AsyncSession, statement: PartnerStatementModel
) -> PartnerStatementModel:
    """Mark it sent. After this the number is somebody else's too."""
    if statement.status != PartnerStatementStatus.DRAFT.value:
        raise PartnerError(f"This statement is already {statement.status}.")
    statement.status = PartnerStatementStatus.ISSUED.value
    statement.issued_at = datetime.now(UTC)
    await session.flush()
    return statement


async def mark_paid(
    session: AsyncSession,
    statement: PartnerStatementModel,
    *,
    payment_reference: str | None = None,
    note: str | None = None,
) -> PartnerStatementModel:
    """Record that the money left.

    Allowed from draft as well as issued: paying a partner without emailing
    them a statement first is a perfectly ordinary thing to do, and refusing it
    would only teach somebody to issue a statement they never sent.
    """
    if statement.status == PartnerStatementStatus.PAID.value:
        raise PartnerError("This statement is already marked paid.")
    if statement.amount_paise <= 0:
        raise PartnerError("There is nothing to pay on this statement.")
    statement.status = PartnerStatementStatus.PAID.value
    statement.paid_at = datetime.now(UTC)
    statement.payment_reference = payment_reference
    if note:
        statement.note = note
    await session.flush()
    return statement


async def outstanding(session: AsyncSession) -> list[PartnerStatementModel]:
    """Everything owed and not yet paid, oldest period first.

    Oldest first for the same reason the application queue is: the partner who
    has been waiting longest is the one to pay next.
    """
    return list(
        (
            await session.scalars(
                select(PartnerStatementModel)
                .where(
                    PartnerStatementModel.status != PartnerStatementStatus.PAID.value,
                    PartnerStatementModel.amount_paise > 0,
                )
                .order_by(PartnerStatementModel.period_start)
            )
        ).all()
    )

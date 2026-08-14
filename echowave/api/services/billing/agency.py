"""Agencies: the accounts they manage, and what they earn on them.

An agency signs up its own clients and gets a flat commission on what those
clients are charged. It sees a roll-up of them — calls, spend, what it has
earned — and nothing else. It does **not** sign in as a client, cannot open
their agents, and cannot read their recordings or transcripts. That is a
deliberate limit rather than an unfinished one: those are conversations with
the client's own customers, and an agency relationship is not consent to
listen to them.

Commission is **snapshotted monthly, never derived on read.** Deriving it would
mean renegotiating an agency's rate silently rewrote every past month — the
same failure the rate card and the managed markup each avoid by being
effective-dated. Here the accrual row is the record; changing the rate moves
future months only.

The commission base is what the client was *charged*, not our margin on them.
An agency cannot see our costs, so a commission they cannot check is one they
will dispute, and "20% of what my client spent" is a number they can verify
from their own client's invoice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgencyCommissionAccrualModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
)

#: Bounds on a commission rate. The ceiling is a fat-finger guard rather than a
#: policy: 5000 instead of 500 is one keystroke, and at 50% an agency earns
#: more on a call than the platform fee it is taken from.
MIN_COMMISSION_BPS = 0
MAX_COMMISSION_BPS = 5000


class AgencyError(ValueError):
    """An agency relationship could not be set or accrued."""


def validate_commission_bps(bps: object) -> int:
    try:
        value = int(bps)
    except (TypeError, ValueError) as exc:
        raise AgencyError(
            "The commission must be a whole number of basis points."
        ) from exc
    if not MIN_COMMISSION_BPS <= value <= MAX_COMMISSION_BPS:
        raise AgencyError(
            f"The commission must be between {MIN_COMMISSION_BPS / 100:g}% and "
            f"{MAX_COMMISSION_BPS / 100:g}%."
        )
    return value


def month_start(day: date) -> date:
    return day.replace(day=1)


async def set_manager(
    session: AsyncSession,
    *,
    client_organization_id: int,
    agency_organization_id: int | None,
    commission_bps: int | None,
) -> OrganizationModel:
    """Put a client under an agency, or take them out from under one.

    ``agency_organization_id=None`` detaches. Past accruals are deliberately
    left alone: the agency did earn them, and deleting the record of work
    already done is not what "no longer manages" means.
    """
    client = await session.get(OrganizationModel, client_organization_id)
    if client is None:
        raise AgencyError("No such account.")

    if agency_organization_id is None:
        client.managed_by_organization_id = None
        client.agency_commission_bps = None
        await session.flush()
        return client

    if agency_organization_id == client_organization_id:
        raise AgencyError("An account cannot manage itself.")

    agency = await session.get(OrganizationModel, agency_organization_id)
    if agency is None:
        raise AgencyError("No such agency.")

    # One level, on purpose. An agency that is itself managed makes "who earns
    # on this call" a tree walk, and every answer below the first level is a
    # commission on a commission that nobody agreed to.
    if agency.managed_by_organization_id:
        raise AgencyError(
            "That account is itself managed by an agency. Agencies are one level deep."
        )

    client.managed_by_organization_id = agency_organization_id
    client.agency_commission_bps = validate_commission_bps(commission_bps or 0)
    await session.flush()
    logger.info(
        "Organization {} is now managed by {} at {} bps",
        client_organization_id,
        agency_organization_id,
        client.agency_commission_bps,
    )
    return client


@dataclass(frozen=True)
class AccruedMonth:
    agency_organization_id: int
    client_organization_id: int
    month: date
    amount_paise: int


async def accrue_month(
    session: AsyncSession, *, month: date, now: datetime | None = None
) -> list[AccruedMonth]:
    """Record what every agency earned in one month.

    Re-runnable: an existing row for the same agency, client and month is
    updated rather than added to, so a retried job converges instead of
    doubling what is owed. The unique constraint is what makes that safe under
    two workers rather than one.

    An accrual is written even when it comes to zero, because "this client
    spent nothing in March" and "March was never accrued" are different facts
    and only one of them needs chasing.
    """
    moment = now or datetime.now(UTC)
    first = month_start(month)
    # First of the next month, without needing a calendar library.
    following = (
        date(first.year + 1, 1, 1)
        if first.month == 12
        else date(first.year, first.month + 1, 1)
    )

    managed = (
        (
            await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.managed_by_organization_id.isnot(None)
                )
            )
        )
        .scalars()
        .all()
    )

    accrued: list[AccruedMonth] = []
    for client in managed:
        charged = await session.scalar(
            select(
                func.coalesce(func.sum(DailyOrganizationRollupModel.charged_paise), 0)
            ).where(
                DailyOrganizationRollupModel.organization_id == client.id,
                DailyOrganizationRollupModel.day >= first,
                DailyOrganizationRollupModel.day < following,
            )
        )
        charged = int(charged or 0)
        bps = int(client.agency_commission_bps or 0)
        amount = charged * bps // 10_000

        existing = await session.scalar(
            select(AgencyCommissionAccrualModel).where(
                AgencyCommissionAccrualModel.agency_organization_id
                == client.managed_by_organization_id,
                AgencyCommissionAccrualModel.client_organization_id == client.id,
                AgencyCommissionAccrualModel.month == first,
            )
        )
        if existing is not None:
            # A month already paid is closed. Re-accruing it would move a
            # figure somebody has already settled against.
            if existing.paid_at is None:
                existing.charged_paise = charged
                existing.bps_applied = bps
                existing.amount_paise = amount
        else:
            session.add(
                AgencyCommissionAccrualModel(
                    agency_organization_id=client.managed_by_organization_id,
                    client_organization_id=client.id,
                    month=first,
                    charged_paise=charged,
                    bps_applied=bps,
                    amount_paise=amount,
                    created_at=moment,
                )
            )
        accrued.append(
            AccruedMonth(
                agency_organization_id=client.managed_by_organization_id,
                client_organization_id=client.id,
                month=first,
                amount_paise=amount,
            )
        )

    await session.flush()
    if accrued:
        logger.info("Accrued agency commission for {} client-month(s)", len(accrued))
    return accrued


async def statement(session: AsyncSession, *, agency_organization_id: int) -> dict:
    """What the agency screen shows: its clients, and what it has earned."""
    clients = (
        (
            await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.managed_by_organization_id
                    == agency_organization_id
                )
            )
        )
        .scalars()
        .all()
    )

    accruals = (
        (
            await session.execute(
                select(AgencyCommissionAccrualModel)
                .where(
                    AgencyCommissionAccrualModel.agency_organization_id
                    == agency_organization_id
                )
                .order_by(AgencyCommissionAccrualModel.month.desc())
            )
        )
        .scalars()
        .all()
    )

    earned = sum(a.amount_paise for a in accruals)
    paid = sum(a.amount_paise for a in accruals if a.paid_at)

    return {
        "clients": [
            {
                "organization_id": c.id,
                # The provider_id, not a name we do not hold. An agency knows
                # which of its own clients this is; inventing a display name
                # here would be inventing data.
                "reference": c.provider_id,
                "commission_bps": c.agency_commission_bps or 0,
            }
            for c in clients
        ],
        "earned_paise": earned,
        "paid_paise": paid,
        # The number the agency actually cares about, computed rather than
        # stored, so it cannot disagree with the rows beneath it.
        "owed_paise": earned - paid,
        "months": [
            {
                "month": a.month.isoformat(),
                "client_organization_id": a.client_organization_id,
                # Both halves of the arithmetic, so a statement shows its own
                # working rather than a figure to be trusted.
                "charged_paise": a.charged_paise,
                "commission_bps": a.bps_applied,
                "amount_paise": a.amount_paise,
                "paid": a.paid_at is not None,
            }
            for a in accruals
        ],
    }


async def mark_paid(
    session: AsyncSession, *, accrual_id: int, note: str | None = None
) -> AgencyCommissionAccrualModel:
    """Record that an accrual has been settled with the agency.

    Records the fact; it moves no money. Payouts to an agency go through
    whatever the finance process is, and a button here that claimed to pay
    would be a button that lies.
    """
    accrual = await session.get(AgencyCommissionAccrualModel, accrual_id)
    if accrual is None:
        raise AgencyError("No such accrual.")
    if accrual.paid_at is not None:
        raise AgencyError("That month has already been marked paid.")

    accrual.paid_at = datetime.now(UTC)
    accrual.paid_note = note
    await session.flush()
    return accrual

"""Applying to be a partner, and being granted one.

A partner account is an ordinary account with a commercial arrangement
attached. Everything the product does, it does the same way afterwards — the
only differences are the badge staff see and the commission that accrues. So
this is four questions and a decision, not a second signup, and nothing here
touches what the account can *do*.

The one thing worth being careful about is the commission, because it is
money. Granting one closes the open row and inserts a new one rather than
updating in place, so a statement for March still reproduces March's number
after an April renegotiation. That is the same shape
``organization_rate_history`` uses, for the same reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    OrganizationModel,
    PartnerApplicationModel,
    PartnerCommissionModel,
)
from api.enums import (
    AccountType,
    CommissionBasis,
    PartnerApplicationStatus,
    PartnerKind,
)
from api.services.partners import referrals


class PartnerError(Exception):
    """Something the caller asked for that the rules do not allow.

    Routes turn this into a 400 with the message as-is, so the messages are
    written for the person who will read them.
    """


#: What an approved application turns the account into. A developer building
#: on the API is not an agency and should not be counted as one on the revenue
#: split; enterprise is a sales motion rather than something applied for, so
#: it is not reachable from here.
ACCOUNT_TYPE_FOR_KIND = {
    PartnerKind.DEVELOPER.value: AccountType.AGENCY.value,
    PartnerKind.AGENCY.value: AccountType.AGENCY.value,
    PartnerKind.RESELLER.value: AccountType.AGENCY.value,
}


async def submit(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int | None,
    kind: str,
    expected_minutes_per_month: int | None = None,
    note: str | None = None,
    company_website: str | None = None,
) -> PartnerApplicationModel:
    """Put an account in the queue."""
    if kind not in {k.value for k in PartnerKind}:
        raise PartnerError(f"Unknown partner kind: {kind}")
    if expected_minutes_per_month is not None and expected_minutes_per_month < 0:
        raise PartnerError("Expected minutes cannot be negative.")

    # Checked here as well as by the partial unique index. The index is what
    # actually holds under a double-submit race; this is what produces a
    # sentence the applicant can act on instead of a constraint violation.
    if await pending_for(session, organization_id) is not None:
        raise PartnerError(
            "You already have an application waiting for review. "
            "We will email you when somebody has looked at it."
        )

    application = PartnerApplicationModel(
        organization_id=organization_id,
        kind=kind,
        expected_minutes_per_month=expected_minutes_per_month,
        note=note,
        company_website=company_website,
        status=PartnerApplicationStatus.PENDING.value,
        submitted_by=user_id,
        submitted_at=datetime.now(UTC),
    )
    session.add(application)
    await session.flush()
    return application


async def pending_for(
    session: AsyncSession, organization_id: int
) -> PartnerApplicationModel | None:
    return await session.scalar(
        select(PartnerApplicationModel).where(
            PartnerApplicationModel.organization_id == organization_id,
            PartnerApplicationModel.status == PartnerApplicationStatus.PENDING.value,
        )
    )


async def latest_for(
    session: AsyncSession, organization_id: int
) -> PartnerApplicationModel | None:
    """The most recent application, whatever became of it.

    What the customer's own screen shows: a pending one says "we are looking",
    a rejected one carries the reason, an approved one is the arrangement they
    are on.
    """
    return await session.scalar(
        select(PartnerApplicationModel)
        .where(PartnerApplicationModel.organization_id == organization_id)
        .order_by(PartnerApplicationModel.submitted_at.desc())
        .limit(1)
    )


async def queue(
    session: AsyncSession, *, status: str = PartnerApplicationStatus.PENDING.value
) -> list[PartnerApplicationModel]:
    """Applications for staff to review, oldest first.

    Oldest first on purpose: a queue sorted newest-first starves the person who
    has been waiting longest, which is the one thing a queue must not do.
    """
    return list(
        (
            await session.scalars(
                select(PartnerApplicationModel)
                .where(PartnerApplicationModel.status == status)
                .order_by(PartnerApplicationModel.submitted_at)
            )
        ).all()
    )


async def live_commission(
    session: AsyncSession, organization_id: int
) -> PartnerCommissionModel | None:
    """The arrangement in effect right now."""
    return await session.scalar(
        select(PartnerCommissionModel).where(
            PartnerCommissionModel.organization_id == organization_id,
            PartnerCommissionModel.effective_to.is_(None),
        )
    )


async def commission_at(
    session: AsyncSession, organization_id: int, when: datetime
) -> PartnerCommissionModel | None:
    """The arrangement that was in effect at ``when``.

    This is the one a statement must use. Reading the live row to compute an
    old month is how a partner gets paid April's rate for March's minutes.
    """
    return await session.scalar(
        select(PartnerCommissionModel)
        .where(
            PartnerCommissionModel.organization_id == organization_id,
            PartnerCommissionModel.effective_from <= when,
            (PartnerCommissionModel.effective_to.is_(None))
            | (PartnerCommissionModel.effective_to > when),
        )
        .order_by(PartnerCommissionModel.effective_from.desc())
        .limit(1)
    )


async def history_for(
    session: AsyncSession, organization_id: int
) -> list[PartnerCommissionModel]:
    """Every rate this account has been on, newest first.

    The audit trail behind a statement. A partner asking "why is this number
    what it is" is answered from here rather than from the live row, which by
    then may be a rate that had nothing to do with the month in question.
    """
    return list(
        (
            await session.scalars(
                select(PartnerCommissionModel)
                .where(PartnerCommissionModel.organization_id == organization_id)
                .order_by(PartnerCommissionModel.effective_from.desc())
            )
        ).all()
    )


async def organizations_with_commission(session: AsyncSession) -> list[int]:
    """Every account that has ever held a commission.

    Ever, not currently: a partner whose arrangement ended last week is still
    owed for the days before it did, and a generate run that skipped them would
    quietly drop the final month of every partner who left.
    """
    return list(
        (
            await session.scalars(
                select(PartnerCommissionModel.organization_id)
                .distinct()
                .order_by(PartnerCommissionModel.organization_id)
            )
        ).all()
    )


async def set_commission(
    session: AsyncSession,
    *,
    organization_id: int,
    commission_bps: int,
    basis: str,
    set_by: int | None,
    application_id: int | None = None,
    note: str | None = None,
    effective_from: datetime | None = None,
) -> PartnerCommissionModel:
    """Close the open arrangement and start a new one.

    Also the path for a renegotiation with no new application, which is why it
    is separate from ``approve``.
    """
    if basis not in {b.value for b in CommissionBasis}:
        raise PartnerError(f"Unknown commission basis: {basis}")
    if not 0 <= commission_bps <= 10_000:
        raise PartnerError("Commission must be between 0% and 100%.")

    now = effective_from or datetime.now(UTC)

    # Close the current row at exactly the moment the new one opens, so
    # `commission_at` can never find two rows covering one instant and never
    # find a gap between them.
    await session.execute(
        update(PartnerCommissionModel)
        .where(
            PartnerCommissionModel.organization_id == organization_id,
            PartnerCommissionModel.effective_to.is_(None),
        )
        .values(effective_to=now)
    )

    row = PartnerCommissionModel(
        organization_id=organization_id,
        commission_bps=commission_bps,
        basis=basis,
        application_id=application_id,
        effective_from=now,
        set_by=set_by,
        note=note,
    )
    session.add(row)
    await session.flush()
    return row


async def approve(
    session: AsyncSession,
    *,
    application: PartnerApplicationModel,
    decided_by: int | None,
    commission_bps: int,
    basis: str,
    note: str | None = None,
) -> PartnerCommissionModel:
    """Grant the application and record what it was granted at."""
    if application.status != PartnerApplicationStatus.PENDING.value:
        raise PartnerError(
            f"This application was already {application.status}. "
            "Set a new commission on the account instead."
        )

    commission = await set_commission(
        session,
        organization_id=application.organization_id,
        commission_bps=commission_bps,
        basis=basis,
        set_by=decided_by,
        application_id=application.id,
        note=note,
    )

    application.status = PartnerApplicationStatus.APPROVED.value
    application.decided_by = decided_by
    application.decided_at = datetime.now(UTC)
    application.decision_note = note

    # The badge staff sort and segment by. Set from the application rather than
    # asked for again, so the tier can never disagree with what was approved.
    await session.execute(
        update(OrganizationModel)
        .where(OrganizationModel.id == application.organization_id)
        .values(account_type=ACCOUNT_TYPE_FOR_KIND[application.kind])
    )

    # The code, minted here rather than on first view of the partner screen.
    # A rate with no way to attribute anything to it is a percentage of
    # nothing, so approval is the moment both halves have to exist — and
    # `ensure_code` returns any code the account already has, so re-approving
    # after a rejection cannot orphan links already in circulation.
    organization = await session.get(OrganizationModel, application.organization_id)
    if organization is not None:
        await referrals.ensure_code(session, organization)

    await session.flush()
    return commission


async def reject(
    session: AsyncSession,
    *,
    application: PartnerApplicationModel,
    decided_by: int | None,
    note: str | None = None,
) -> PartnerApplicationModel:
    """Turn it down.

    Leaves the account exactly as it was — no commission row, no tier change —
    so a rejection is never something the customer has to notice to be
    unharmed by. They keep the product they already had.
    """
    if application.status != PartnerApplicationStatus.PENDING.value:
        raise PartnerError(f"This application was already {application.status}.")

    application.status = PartnerApplicationStatus.REJECTED.value
    application.decided_by = decided_by
    application.decided_at = datetime.now(UTC)
    application.decision_note = note
    await session.flush()
    return application

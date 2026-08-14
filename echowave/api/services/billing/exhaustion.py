"""Telling an account the moment its credit actually runs out.

The low-balance warning fires while there is still money. This is the other
end: the call that took the balance to zero, and what happens next.

**A live call is never cut off.** The person on the phone is the customer's
customer, and dropping them mid-sentence to save a few rupees is a worse
outcome for everybody than finishing the call and refusing the next one. So the
balance is allowed to go slightly negative — bounded by one call's cost, since
the reservation held most of it up front — and the existing gate refuses the
next run. That is a decision rather than an accident, and it is written down
here because the alternative reads as a bug when somebody first sees a negative
balance.

What was missing was the telling. An account whose credit ran out learned about
it when its next call was refused, which is the worst possible moment: mid
campaign, with no explanation on the screen the caller was watching.

Deduped per day like every other notice, because a busy account can cross zero,
top up, and cross again — and three emails in an afternoon is how a useful
warning becomes one people filter.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import UI_APP_URL
from api.db.models import NotificationModel, UserModel

NOTICE_KIND = "credit_exhausted"


def _dedupe_key(organization_id: int, day: str) -> str:
    return f"{NOTICE_KIND}:{organization_id}:{day}"


def notice_subject() -> str:
    return "Your Decibyl credit has run out"


def notice_body(*, balance_paise: int) -> str:
    """Says what happened, what it means, and what to do — in that order.

    Not "your balance is low". It has already run out, and a warning that
    understates the situation is one somebody deals with tomorrow.
    """
    owing = (
        f"The last call took it to ₹{balance_paise / 100:.2f}.\n\n"
        if balance_paise < 0
        else ""
    )
    return (
        "Your Decibyl credit has run out.\n\n"
        f"{owing}"
        "The call that was in progress finished normally — we do not cut a live "
        "call off. New calls will be refused until the account is topped up.\n\n"
        f"Top up here: {UI_APP_URL}/billing\n"
    )


async def note_if_exhausted(
    session: AsyncSession,
    *,
    organization_id: int,
    balance_before: int,
    balance_after: int,
    now: datetime | None = None,
) -> bool:
    """Claim the right to tell this account its credit ran out.

    Returns True when this caller should send the email — the row is claimed
    here rather than after sending, so two workers costing calls at the same
    moment produce one notice rather than two.

    Only on the **crossing**. An account that was already at zero and goes
    further negative has been told; repeating it every call would be the
    behaviour that makes people filter the sender.
    """
    if balance_before <= 0 or balance_after > 0:
        return False

    moment = now or datetime.now(UTC)
    key = _dedupe_key(organization_id, moment.date().isoformat())

    existing = await session.scalar(
        select(NotificationModel).where(NotificationModel.dedupe_key == key)
    )
    if existing is not None:
        return False

    session.add(
        NotificationModel(
            organization_id=organization_id,
            kind=NOTICE_KIND,
            dedupe_key=key,
            created_at=moment,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        # Two workers raced and the other one won. Theirs sends; ours does not.
        await session.rollback()
        return False

    logger.info(
        "Credit exhausted for organization {} ({} paise)",
        organization_id,
        balance_after,
    )
    return True


async def recipient_for(session: AsyncSession, *, organization_id: int) -> str | None:
    """Who to tell. The organization's owners, or its earliest member.

    An account with nobody contactable returns None rather than raising: it is
    worth knowing that the notice could not be delivered, and it is not worth
    failing the costing job that discovered it.
    """
    from api.db.models import OrganizationMembershipModel

    row = await session.scalar(
        select(UserModel)
        .join(
            OrganizationMembershipModel,
            OrganizationMembershipModel.user_id == UserModel.id,
        )
        .where(
            OrganizationMembershipModel.organization_id == organization_id,
            UserModel.email.isnot(None),
        )
        # Owners first, then whoever joined earliest — the same order the
        # roster shows, so the person who gets the mail is the one a customer
        # would expect to.
        .order_by(
            (OrganizationMembershipModel.role == "owner").desc(),
            OrganizationMembershipModel.id.asc(),
        )
        .limit(1)
    )
    return row.email if row else None

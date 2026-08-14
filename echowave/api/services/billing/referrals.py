"""Refer a friend: attribution, the award, and the window before it is spendable.

A referrer earns 20% of the first payment the account they referred makes. The
whole design turns on *when* that lands, because the obvious answer is wrong in
an expensive way.

Crediting at settlement means a refunded or charged-back payment leaves
referral credit already spent, on an account with nothing behind it. Card
disputes arrive weeks later; a referral scheme that pays instantly and reverses
never is a way to buy credit at a discount with a stolen card.

So an award is **earned at settlement and spendable after a hold**. Until it
matures it lives in ``referral_awards`` rather than in the credit ledger, which
keeps the ledger meaning exactly one thing — money that is yours now — and
keeps every balance query the same shape it already was. Maturing writes the
ledger row; cancelling before maturity writes nothing at all, so a clawback
leaves no correction to explain to anybody.

Two abuses this closes, both by construction rather than by policing:

**Self-referral.** An account cannot refer itself, and a code is refused for an
organization that already has a referrer. Otherwise the scheme pays 20% back on
every first payment anybody makes.

**Repeat awards.** One award per referred organization, enforced by a unique
index rather than by a check in this module — the check races with itself under
two concurrent webhooks for the same order.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CreditLedgerModel, OrganizationModel, ReferralAwardModel
from api.enums import CreditLedgerKind

#: Share of the referred account's first payment, in basis points. 2000 = 20%.
REFERRAL_SHARE_BPS = 2000

#: How long an award waits before it can be spent. Long enough to cover the
#: window in which a card payment is normally disputed or refunded, short
#: enough that a referrer who checks back in a week sees it.
HOLD_DAYS = 7

#: Unambiguous alphabet: no O/0, no I/1/L. A referral code gets read aloud and
#: typed from a screenshot, and a code that turns into a different valid code
#: when misread pays the wrong person.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class ReferralError(ValueError):
    """A referral could not be attributed or awarded."""


def generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def normalise_code(code: str) -> str:
    """Codes arrive typed, pasted, and lower-cased by autocorrect."""
    return "".join(
        c for c in (code or "").upper() if c in string.ascii_uppercase + string.digits
    )


async def ensure_code(session: AsyncSession, *, organization_id: int) -> str:
    """The account's own code, minting one the first time it is asked for.

    Lazily rather than at signup, so an account that never opens the referral
    screen never gets a code — and the codes that do exist are ones somebody
    intended to share.
    """
    organization = await session.get(OrganizationModel, organization_id)
    if organization is None:
        raise ReferralError("No such organization.")
    if organization.referral_code:
        return organization.referral_code

    # Retry on collision rather than trusting 31^8. The unique index is the
    # authority; this loop is what turns a collision into a second attempt
    # instead of a 500 on somebody's first visit to the screen.
    for _ in range(5):
        candidate = generate_code()
        taken = await session.scalar(
            select(func.count())
            .select_from(OrganizationModel)
            .where(OrganizationModel.referral_code == candidate)
        )
        if not taken:
            organization.referral_code = candidate
            await session.flush()
            return candidate
    raise ReferralError("Could not allocate a referral code. Try again.")


async def attribute(
    session: AsyncSession, *, organization_id: int, code: str
) -> OrganizationModel:
    """Record who referred this account. Awards nothing.

    Separate from the award because they happen at different times and fail for
    different reasons: attribution is at signup and is the customer's mistake to
    fix, the award is at settlement and is ours.
    """
    cleaned = normalise_code(code)
    if not cleaned:
        raise ReferralError("Enter a referral code.")

    referred = await session.get(OrganizationModel, organization_id)
    if referred is None:
        raise ReferralError("No such organization.")
    if referred.referred_by_organization_id:
        raise ReferralError("This account already has a referrer.")

    referrer = await session.scalar(
        select(OrganizationModel).where(OrganizationModel.referral_code == cleaned)
    )
    if referrer is None:
        raise ReferralError("That referral code is not valid.")
    if referrer.id == organization_id:
        raise ReferralError("An account cannot refer itself.")

    referred.referred_by_organization_id = referrer.id
    referred.referred_at = datetime.now(UTC)
    await session.flush()
    logger.info("Organization {} referred by {}", organization_id, referrer.id)
    return referrer


@dataclass(frozen=True)
class AwardedReferral:
    referrer_organization_id: int
    amount_paise: int
    available_at: datetime


async def award_for_payment(
    session: AsyncSession,
    *,
    organization_id: int,
    payment_id: str,
    net_paise: int,
    now: datetime | None = None,
) -> AwardedReferral | None:
    """Earn the referrer their share of a first payment.

    ``None`` — not an exception — when there is nothing to award: no referrer,
    or this is not the first payment. Both are the ordinary case on almost
    every payment, and a settlement path that raised on them would turn the
    common case into an error to be swallowed.

    ``net_paise`` is what the account was credited, not what it was charged.
    Awarding on the gross would pay 20% of the GST as well, which is somebody
    else's money.
    """
    moment = now or datetime.now(UTC)

    organization = await session.get(OrganizationModel, organization_id)
    if organization is None or not organization.referred_by_organization_id:
        return None

    existing = await session.scalar(
        select(func.count())
        .select_from(ReferralAwardModel)
        .where(ReferralAwardModel.referred_organization_id == organization_id)
    )
    if existing:
        # Not the first payment, or a webhook replayed. Either way the award
        # has been made and making it again would pay twice for one referral.
        return None

    amount = net_paise * REFERRAL_SHARE_BPS // 10_000
    if amount <= 0:
        return None

    available_at = moment + timedelta(days=HOLD_DAYS)
    session.add(
        ReferralAwardModel(
            referrer_organization_id=organization.referred_by_organization_id,
            referred_organization_id=organization_id,
            payment_id=payment_id,
            amount_paise=amount,
            earned_at=moment,
            available_at=available_at,
        )
    )
    await session.flush()
    logger.info(
        "Referral award of {} paise earned by organization {} for {}",
        amount,
        organization.referred_by_organization_id,
        organization_id,
    )
    return AwardedReferral(
        referrer_organization_id=organization.referred_by_organization_id,
        amount_paise=amount,
        available_at=available_at,
    )


async def cancel_for_payment(session: AsyncSession, *, payment_id: str) -> int | None:
    """Take back an award whose payment was refunded or charged back.

    Only while it is still held. Once matured the money is in the ledger and
    may already be spent, and clawing it back would take a call off an account
    that did nothing wrong — a reversing adjustment is a decision for a human,
    not something a refund webhook should do on its own.
    """
    award = await session.scalar(
        select(ReferralAwardModel).where(
            ReferralAwardModel.payment_id == payment_id,
            ReferralAwardModel.credited_at.is_(None),
            ReferralAwardModel.cancelled_at.is_(None),
        )
    )
    if award is None:
        return None

    award.cancelled_at = datetime.now(UTC)
    await session.flush()
    logger.info(
        "Referral award for payment {} cancelled before maturing ({} paise)",
        payment_id,
        award.amount_paise,
    )
    return award.amount_paise


async def credit_matured(
    session: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """Move every award past its hold into the ledger. Returns their ids.

    Idempotent by ``credited_at``, so a retried job credits nothing twice.
    """
    from api.services.billing.costing import current_balance_paise

    moment = now or datetime.now(UTC)
    due = (
        (
            await session.execute(
                select(ReferralAwardModel).where(
                    ReferralAwardModel.credited_at.is_(None),
                    ReferralAwardModel.cancelled_at.is_(None),
                    ReferralAwardModel.available_at <= moment,
                )
            )
        )
        .scalars()
        .all()
    )

    credited: list[int] = []
    for award in due:
        balance = await current_balance_paise(
            session, organization_id=award.referrer_organization_id
        )
        session.add(
            CreditLedgerModel(
                organization_id=award.referrer_organization_id,
                delta_paise=award.amount_paise,
                kind=CreditLedgerKind.REFERRAL.value,
                ref_type="referral_award",
                ref_id=str(award.id),
                balance_after_paise=balance + award.amount_paise,
                note="Referral reward",
            )
        )
        award.credited_at = moment
        credited.append(award.id)

    if credited:
        await session.flush()
        logger.info("Credited {} matured referral award(s)", len(credited))
    return credited


async def summary(session: AsyncSession, *, organization_id: int) -> dict:
    """What the referral screen shows: the code, and what it has earned."""
    rows = (
        (
            await session.execute(
                select(ReferralAwardModel).where(
                    ReferralAwardModel.referrer_organization_id == organization_id
                )
            )
        )
        .scalars()
        .all()
    )

    referred_count = await session.scalar(
        select(func.count())
        .select_from(OrganizationModel)
        .where(OrganizationModel.referred_by_organization_id == organization_id)
    )

    return {
        "code": (await session.get(OrganizationModel, organization_id)).referral_code,
        "share_bps": REFERRAL_SHARE_BPS,
        "hold_days": HOLD_DAYS,
        "signed_up": int(referred_count or 0),
        # Three figures rather than one total, because they answer different
        # questions: what is mine, what is coming, and what did not survive.
        "credited_paise": sum(
            a.amount_paise for a in rows if a.credited_at and not a.cancelled_at
        ),
        "pending_paise": sum(
            a.amount_paise for a in rows if not a.credited_at and not a.cancelled_at
        ),
        "cancelled_paise": sum(a.amount_paise for a in rows if a.cancelled_at),
        "awards": [
            {
                "amount_paise": a.amount_paise,
                "earned_at": a.earned_at.isoformat() if a.earned_at else None,
                "available_at": a.available_at.isoformat() if a.available_at else None,
                "status": (
                    "cancelled"
                    if a.cancelled_at
                    else "credited"
                    if a.credited_at
                    else "held"
                ),
            }
            for a in sorted(
                rows, key=lambda a: a.earned_at or datetime.min, reverse=True
            )
        ],
    }

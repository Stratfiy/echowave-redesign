"""Friend referral: 200 credits each, on the friend's first payment (KAN-133).

Every account has a referral code and a link, minted with the same rule as a
partner's (``partners.referrals.ensure_code``) because it is the same column
and the same door: a signup through ``/auth/signup?ref=CODE`` is attributed
once, at provisioning, and never again. What differs is what the attribution
pays.

* **A partner's referral pays commission** on the referred account's spend,
  for as long as the arrangement lasts. That is the partner programme, and a
  partner-attributed account earns nothing here: one reward per relationship.
* **A friend's referral pays 200 credits each**, to the referrer and to the
  referred, the moment the referred account's first payment is captured.
  Nothing on signup -- a signup costs nothing to fake, a payment does.

Both grants are ``trial`` ledger rows keyed on the referred account, once
per pair by a partial unique index, so a replayed payment webhook cannot pay
twice and a race between two captures cannot either. Internal accounts earn
nothing on either side. The referrer's side is capped per calendar month
(twenty by default; staff can raise it on the account), and the cap stops the
referrer's credit only: the friend was promised theirs by the link.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import UI_APP_URL
from api.db.models import CreditLedgerModel, OrganizationModel, PartnerCommissionModel
from api.enums import CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.partners import referrals

#: Credits to each side on the referred account's first payment.
REWARD_CREDITS = int(os.getenv("REFERRAL_REWARD_CREDITS", "200"))
#: Paid referrals a referrer may earn on in a calendar month, unless staff
#: set a different cap on the account.
DEFAULT_MONTHLY_CAP = int(os.getenv("REFERRAL_MONTHLY_CAP", "20"))
#: The scheme can be paused without a release.
ENABLED = os.getenv("REFERRAL_REWARDS_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

#: The ledger rows this writes: kind ``trial``, this ref_type, ref_id the
#: referred organisation's id, on both organisations.
REF_TYPE = "referral"


def _link(code: str) -> str:
    return f"{UI_APP_URL}/auth/signup?ref={code}"


async def _is_internal(session: AsyncSession, organization_id: int) -> bool:
    return bool(
        await session.scalar(
            select(OrganizationModel.internal_billing).where(
                OrganizationModel.id == organization_id
            )
        )
    )


async def _is_partner(session: AsyncSession, organization_id: int) -> bool:
    """Whether this account is on a live partner arrangement, which pays
    commission on its referrals instead of credits."""
    return (
        await session.scalar(
            select(PartnerCommissionModel.id).where(
                PartnerCommissionModel.organization_id == organization_id,
                PartnerCommissionModel.effective_to.is_(None),
            )
        )
    ) is not None


async def _already_paid(session: AsyncSession, *, referred_id: int) -> bool:
    return (
        await session.scalar(
            select(CreditLedgerModel.id).where(
                CreditLedgerModel.organization_id == referred_id,
                CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                CreditLedgerModel.ref_type == REF_TYPE,
                CreditLedgerModel.ref_id == str(referred_id),
            )
        )
    ) is not None


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _paid_this_month(session: AsyncSession, *, referrer_id: int) -> int:
    """Referral rows the referrer earned since the first of the month."""
    return int(
        await session.scalar(
            select(func.count(CreditLedgerModel.id)).where(
                CreditLedgerModel.organization_id == referrer_id,
                CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                CreditLedgerModel.ref_type == REF_TYPE,
                CreditLedgerModel.created_at >= _month_start(datetime.now(UTC)),
            )
        )
        or 0
    )


async def monthly_cap(session: AsyncSession, *, organization_id: int) -> int:
    cap = await session.scalar(
        select(OrganizationModel.referral_monthly_cap).where(
            OrganizationModel.id == organization_id
        )
    )
    return int(cap) if cap is not None else DEFAULT_MONTHLY_CAP


async def set_monthly_cap(
    session: AsyncSession, *, organization_id: int, cap: int | None
) -> int:
    """Staff raise (or clear back to the default) an account's cap."""
    organization = await session.get(OrganizationModel, organization_id)
    if organization is None:
        raise ValueError(f"Organization {organization_id} not found")
    organization.referral_monthly_cap = cap
    await session.flush()
    return cap if cap is not None else DEFAULT_MONTHLY_CAP


async def _grant(
    session: AsyncSession, *, organization_id: int, referred_id: int, note: str
) -> int:
    """One trial row, in a savepoint; a lost race is a quiet zero."""
    from api.services.billing.costing import current_balance_paise

    amount = REWARD_CREDITS * PAISE_PER_CREDIT
    from api.services.billing.ledger_lock import lock_organization_ledger

    await lock_organization_ledger(session, organization_id=organization_id)
    balance = await current_balance_paise(session, organization_id=organization_id)
    try:
        async with session.begin_nested():
            session.add(
                CreditLedgerModel(
                    organization_id=organization_id,
                    delta_paise=amount,
                    kind=CreditLedgerKind.TRIAL.value,
                    ref_type=REF_TYPE,
                    ref_id=str(referred_id),
                    balance_after_paise=balance + amount,
                    note=note,
                )
            )
            await session.flush()
    except IntegrityError:
        logger.debug(
            "Referral reward for org {} on {} was paid concurrently",
            organization_id,
            referred_id,
        )
        return 0
    return REWARD_CREDITS


async def settle_first_payment(
    session: AsyncSession, *, organization_id: int, payment_ref: str
) -> dict[str, int]:
    """Pay both sides if this is the referred account's first payment.

    Called from the payment webhook after a top-up is credited or a plan
    cycle is granted, with the provider's payment id for the note. Returns
    the credits granted to each side; zeros are the common case and are not
    an error. Never raises for a business reason: the payment that got us
    here is already credited, and a referral must not be able to fail it.
    """
    nothing = {"referrer": 0, "referred": 0}
    if not ENABLED:
        return nothing
    organization = await session.get(OrganizationModel, organization_id)
    if organization is None or organization.referred_by_organization_id is None:
        return nothing
    referrer_id = int(organization.referred_by_organization_id)
    if referrer_id == organization.id:
        return nothing
    if await _already_paid(session, referred_id=organization.id):
        return nothing
    # The referred account is internal (a staff test account through a real
    # link): nobody earns, or a tester could mint credits for a friend.
    if await _is_internal(session, organization.id):
        return nothing
    # A partner's referral is the partner programme's business.
    if await _is_partner(session, referrer_id):
        logger.info(
            "Org {} first payment {}: referred by partner {}, commission applies",
            organization.id,
            payment_ref,
            referrer_id,
        )
        return nothing

    referrer_name = await session.scalar(
        select(OrganizationModel.billing_name).where(
            OrganizationModel.id == referrer_id
        )
    )
    referred = await _grant(
        session,
        organization_id=organization.id,
        referred_id=organization.id,
        note=f"Referral: welcome credits, invited by "
        f"{referrer_name or f'account {referrer_id}'} ({payment_ref})",
    )

    referrer = 0
    if await _is_internal(session, referrer_id):
        logger.info(
            "Org {} first payment {}: referrer {} is internal, earns nothing",
            organization.id,
            payment_ref,
            referrer_id,
        )
    else:
        cap = await monthly_cap(session, organization_id=referrer_id)
        paid = await _paid_this_month(session, referrer_id=referrer_id)
        if paid >= cap:
            logger.info(
                "Org {} first payment {}: referrer {} at its monthly cap ({})",
                organization.id,
                payment_ref,
                referrer_id,
                cap,
            )
        else:
            referrer = await _grant(
                session,
                organization_id=referrer_id,
                referred_id=organization.id,
                note=f"Referral: {organization.billing_name or f'account {organization.id}'} "
                f"made their first payment ({payment_ref})",
            )

    if referred or referrer:
        logger.info(
            "Referral paid on org {} first payment {}: referred {} credits, "
            "referrer {} got {} credits",
            organization.id,
            payment_ref,
            referred,
            referrer_id,
            referrer,
        )
    return {"referrer": referrer, "referred": referred}


async def settle_in_own_session(*, organization_id: int, payment_ref: str) -> None:
    """The webhook's hook: its own transaction, so a referral can never roll
    back the credit that triggered it, and never raises."""
    from api.db import db_client

    try:
        async with db_client.async_session() as session:
            await settle_first_payment(
                session, organization_id=organization_id, payment_ref=payment_ref
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - the payment outranks the referral
        logger.error(
            "Referral settlement for org {} on {} failed: {}",
            organization_id,
            payment_ref,
            exc,
        )


async def state(session: AsyncSession, *, organization_id: int) -> dict:
    """What the invite card shows: the code and link, who came, who paid."""
    organization = await session.get(OrganizationModel, organization_id)
    if organization is None:
        raise ValueError(f"Organization {organization_id} not found")
    code = await referrals.ensure_code(session, organization)

    referred = list(
        (
            await session.scalars(
                select(OrganizationModel)
                .where(OrganizationModel.referred_by_organization_id == organization_id)
                .order_by(OrganizationModel.referred_at.desc(), OrganizationModel.id)
            )
        ).all()
    )
    paid_ids = set(
        (
            await session.scalars(
                select(CreditLedgerModel.ref_id).where(
                    CreditLedgerModel.organization_id == organization_id,
                    CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                    CreditLedgerModel.ref_type == REF_TYPE,
                )
            )
        ).all()
    )
    # "Paid" is the friend's own row, not the referrer's: a friend past the
    # referrer's cap still paid, and the list should say so honestly.
    friend_paid_ids = set(
        (
            await session.scalars(
                select(CreditLedgerModel.organization_id).where(
                    CreditLedgerModel.organization_id.in_(
                        [r.id for r in referred] or [0]
                    ),
                    CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                    CreditLedgerModel.ref_type == REF_TYPE,
                )
            )
        ).all()
    )
    earned = len(paid_ids) * REWARD_CREDITS
    return {
        "enabled": ENABLED,
        "code": code,
        "link": _link(code),
        "reward_credits": REWARD_CREDITS,
        "monthly_cap": await monthly_cap(session, organization_id=organization_id),
        "this_month": await _paid_this_month(session, referrer_id=organization_id),
        "earned_credits": earned,
        "accounts": [
            {
                "name": row.billing_name or f"Account {row.id}",
                "referred_at": row.referred_at.isoformat() if row.referred_at else None,
                "status": "paid" if row.id in friend_paid_ids else "signed_up",
                "rewarded": str(row.id) in paid_ids,
            }
            for row in referred
        ],
    }

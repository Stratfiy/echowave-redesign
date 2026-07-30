"""Free credit for a new account.

Prepaid means a brand-new account cannot make a single call, which is a poor
first five minutes for someone who has just signed up to try the thing. The
bonus buys them a real conversation before they have to think about money.

It is **not a sale**. No money changed hands, so there is no GST on it and no
receipt voucher — a tax document for a gift would misstate a supply that never
happened. It reaches the ledger as :attr:`CreditLedgerKind.TRIAL`, which keeps
it distinguishable from bought credit forever: revenue reporting can exclude it,
and "how much of the balance did they pay for" stays an answerable question.

Denominated in **dollars**, like the list price, and converted at the FX rate in
force when the account signs up. A rupee-denominated bonus would silently get
cheaper in dollar terms every time the rupee weakened, which is the same drift
the platform rate avoids by quoting in USD.

Granted **once per organization**, enforced by a partial unique index on the
ledger rather than by a check in application code. Two requests racing during
signup would otherwise both find no bonus and both grant one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import SIGNUP_BONUS_MICROS_USD
from api.db.models import CreditLedgerModel
from api.enums import CreditLedgerKind
from api.services.billing.money import MICROS_PER_USD, round_half_up_div

#: Every bonus row carries this, so one query answers "what have we given away".
REF_TYPE = "signup_bonus"


async def _already_granted(session: AsyncSession, *, organization_id: int) -> bool:
    existing = await session.scalar(
        select(CreditLedgerModel.id).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
            CreditLedgerModel.ref_type == REF_TYPE,
        )
    )
    return existing is not None


async def bonus_paise(session: AsyncSession, *, at: datetime | None = None) -> int:
    """The bonus in paise, converted at today's rate."""
    from api.services.billing.rates import resolve_usd_inr

    at = at or datetime.now(UTC)
    fx = await resolve_usd_inr(session, at=at)
    return round_half_up_div(SIGNUP_BONUS_MICROS_USD * fx.paise_per_usd, MICROS_PER_USD)


async def grant_signup_bonus(
    session: AsyncSession, *, organization_id: int, at: datetime | None = None
) -> int:
    """Give a new organization its free credit. Returns the paise granted.

    Returns 0 when the bonus is switched off or has already been given, both of
    which are ordinary rather than errors.

    Never raises for a duplicate. Signup can be retried, and two concurrent
    requests can reach here for the same brand-new organization; the unique
    index catches what the check above races on, and losing that race means the
    account already has its bonus — which is the desired end state either way.
    """
    if SIGNUP_BONUS_MICROS_USD <= 0:
        return 0

    if await _already_granted(session, organization_id=organization_id):
        return 0

    amount = await bonus_paise(session, at=at)
    if amount <= 0:
        return 0

    # A trial credit is the account's first ledger row, so the running balance
    # it records is just the amount itself. Read rather than assumed, because an
    # organization created by an import could already have adjustments on it.
    from api.services.billing.costing import current_balance_paise

    balance = await current_balance_paise(session, organization_id=organization_id)

    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=amount,
            kind=CreditLedgerKind.TRIAL.value,
            ref_type=REF_TYPE,
            ref_id=str(organization_id),
            balance_after_paise=balance + amount,
            note=(f"Signup bonus (${SIGNUP_BONUS_MICROS_USD / MICROS_PER_USD:.2f})"),
        )
    )

    try:
        await session.flush()
    except IntegrityError:
        # Lost the race. The other request granted it; nothing more to do.
        await session.rollback()
        logger.debug(
            "Signup bonus for org {} was granted concurrently", organization_id
        )
        return 0

    logger.info(
        "Granted org {} a signup bonus of {} paise (${:.2f})",
        organization_id,
        amount,
        SIGNUP_BONUS_MICROS_USD / MICROS_PER_USD,
    )
    return amount

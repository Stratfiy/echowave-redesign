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

Granted **after the address is proved**, where proving it is possible. Free
credit that lands the moment a form is submitted is free vendor minutes for
anyone with a loop and a list of addresses; a code in the inbox is the cheapest
thing that makes each bonus cost the claimant something. Google and Stack vouch
for the address themselves, and a deployment with no mail server has no code
to send, so on those the bonus still lands at creation — withholding it there
would be withholding it forever.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import SIGNUP_BONUS_MICROS_USD
from api.db.models import CreditLedgerModel
from api.enums import CreditLedgerKind, PostHogEvent
from api.services.billing.money import MICROS_PER_USD, round_half_up_div
from api.services.posthog_client import capture_event

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


def verification_gates_the_bonus() -> bool:
    """Must a new account prove its address before the bonus lands?

    Only where proof is possible: local email/password auth with mail
    configured. Every other door either vouches for the address itself or has
    no way to ask.
    """
    from api.services.auth.email_verification import verification_is_enforceable

    return verification_is_enforceable()


async def grant_bonus_if_due(
    session: AsyncSession, *, organization_id: int, user
) -> int:
    """The bonus at account creation, unless it is waiting on a proved address."""
    if (
        verification_gates_the_bonus()
        and getattr(user, "email_verified_at", None) is None
    ):
        logger.info(
            "Signup bonus for org {} waits for email verification", organization_id
        )
        return 0
    return await grant_signup_bonus(session, organization_id=organization_id)


async def grant_bonus_on_verification(organization_id: int | None) -> int:
    """The address is proved: give the organization its bonus if it has none.

    Best effort and never raises. Verification has already succeeded by the
    time this runs, and a ledger problem must not turn that into an error
    the person sees; the once-only index means a retry is safe.
    """
    if organization_id is None:
        return 0
    from api.db import db_client

    try:
        async with db_client.async_session() as session:
            granted = await grant_signup_bonus(session, organization_id=organization_id)
            if granted:
                await session.commit()
            return granted
    except Exception as exc:
        logger.error(
            "Could not grant the signup bonus to org {} on verification: {}",
            organization_id,
            exc,
        )
        return 0


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
    # After the flush, so this only fires for the request that actually won the
    # race and wrote the row — the loser returns above having granted nothing,
    # and counting it here would report twice as much given away as we gave.
    from api.services.notifications import inbox

    await inbox.post(
        organization_id=organization_id,
        kind="signup_bonus",
        dedupe_key=str(organization_id),
        title="Your free credits are in",
        body=(
            "Enough for your first conversations. Build an agent and hear it "
            "in the browser; every call is paid from these until you top up."
        ),
        link="/start",
    )
    capture_event(
        distinct_id=str(organization_id),
        event=PostHogEvent.SIGNUP_BONUS_GRANTED,
        properties={"organization_id": organization_id, "amount_paise": amount},
    )
    return amount

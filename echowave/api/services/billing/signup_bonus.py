"""Free credit for a new account: the first onboarding step.

Prepaid means a brand-new account cannot make a single call, which is a poor
first five minutes for someone who has just signed up to try the thing. A
proved address is worth the first tranche of the Free credits, enough for a
real conversation before they have to think about money.

Since KAN-132 the rest of the allowance is earned step by step; see
``onboarding_credits``. This module keeps the entry points signup and the
verify route already call, so nothing upstream had to learn a new name.

Granted **after the address is proved**, where proving it is possible. Free
credit that lands the moment a form is submitted is free vendor minutes for
anyone with a loop and a list of addresses; a code in the inbox is the cheapest
thing that makes each bonus cost the claimant something. Google and Stack vouch
for the address themselves, and a deployment with no mail server has no code
to send, so on those the bonus still lands at creation — withholding it there
would be withholding it forever.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

#: Every bonus row carries this, so one query answers "what have we given away".
REF_TYPE = "signup_bonus"


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
    """Pay the first onboarding step: a proved address. Returns the paise.

    Since KAN-132 the free allowance arrives in steps, and this is step one.
    ``at`` is accepted for callers that still pass it and ignored: the tranche
    is a fixed number of credits, not a dollar figure converted on the day.
    """
    from api.services.billing import onboarding_credits

    return await onboarding_credits.grant_step(
        session, organization_id=organization_id, key=onboarding_credits.VERIFY_EMAIL
    )

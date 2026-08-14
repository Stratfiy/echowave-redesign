"""A ceiling on what an account can spend in a day.

Concurrency caps how many calls run at once. Nothing capped how much they could
cost, so a stolen card plus a loop was an unbounded vendor bill — unbounded
because the bill is ours: the calls are placed on our carrier account and the
inference on our keys, and a prepaid balance only stops spending once it is
already gone.

This is a circuit breaker, not a commercial limit, and the distinction decides
the default. A ceiling that trips on an ordinary busy day is one somebody
switches off, and an account with the ceiling switched off is the account this
exists to protect. So the default is deliberately generous and the number is
per account.

Measured against the ledger rather than a counter, for the same reason every
other figure here is: a counter and a ledger disagree eventually, and the one
somebody trusts is whichever they happen to be looking at.
"""

from __future__ import annotations

from datetime import date, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import DEFAULT_DAILY_SPEND_CEILING_PAISE
from api.db.models import CreditLedgerModel
from api.enums import CreditLedgerKind, OrganizationConfigurationKey


async def ceiling_for(session: AsyncSession, *, organization_id: int) -> int:
    """The account's ceiling in paise. 0 means no ceiling."""
    from api.db import db_client

    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE.value,
        )
        if config and config.value:
            value = config.value.get("value")
            if value is not None:
                return max(0, int(value))
    except Exception:
        logger.warning(
            "Could not read the spend ceiling for organization {}",
            organization_id,
            exc_info=True,
        )
    return int(DEFAULT_DAILY_SPEND_CEILING_PAISE)


async def spent_today_paise(
    session: AsyncSession, *, organization_id: int, now: datetime | None = None
) -> int:
    """What the account has spent since midnight IST.

    Usage and rentals both count. They are different kinds of spend but the
    same money, and a ceiling that ignored rentals would be one a runaway
    number-buying loop walked straight through.
    """
    from api.services.billing.rollup import IST, ist_day_bounds_utc

    today: date = (now or datetime.now(IST)).astimezone(IST).date()
    start_utc, end_utc = ist_day_bounds_utc(today)

    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.created_at >= start_utc,
            CreditLedgerModel.created_at < end_utc,
            CreditLedgerModel.kind.in_(
                (CreditLedgerKind.USAGE.value, CreditLedgerKind.RENTAL.value)
            ),
        )
    )
    # Debits are negative in the ledger; spend is the positive of that.
    return abs(int(total or 0))


async def would_exceed(
    session: AsyncSession, *, organization_id: int, now: datetime | None = None
) -> bool:
    """Whether this account has already hit its ceiling for today.

    Checked before a call starts rather than after it is costed, because a
    ceiling enforced after the spend is a report rather than a brake.
    """
    ceiling = await ceiling_for(session, organization_id=organization_id)
    if ceiling <= 0:
        return False

    spent = await spent_today_paise(session, organization_id=organization_id, now=now)
    if spent < ceiling:
        return False

    logger.warning(
        "Organization {} has hit its daily spend ceiling ({} of {} paise)",
        organization_id,
        spent,
        ceiling,
    )
    return True

"""Move referral awards out of hold and into the ledger.

An award is earned when the referred account's first payment settles and
becomes spendable a week later, so that a refund or chargeback in between can
take it back before anybody has spent it. Something has to notice the week has
passed, and this is it.

Hourly rather than daily: the hold is measured in days, so an hour of slack on
either end is invisible to the referrer and means a worker restart never leaves
an award stranded until tomorrow.

Idempotent — ``credit_matured`` skips anything already credited — so a retried
run pays nothing twice.
"""

from loguru import logger

from api.db import db_client
from api.services.billing import referrals


async def credit_matured_referrals(_ctx) -> None:
    async with db_client.async_session() as session:
        credited = await referrals.credit_matured(session)
        await session.commit()

    if credited:
        logger.info("Credited {} matured referral award(s)", len(credited))

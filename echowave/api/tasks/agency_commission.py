"""Snapshot last month's agency commission.

Commission is recorded as it stood rather than derived on read, so something
has to do the recording. This is it.

Runs on the 2nd of the month rather than the 1st: the daily rollups the accrual
reads are themselves refreshed on a lag, and accruing at midnight on the 1st
would snapshot a month whose last day had not finished being counted.

Re-runnable. ``accrue_month`` updates an unpaid row rather than adding to it,
so a retry converges instead of doubling what an agency is owed.
"""

from datetime import date

from loguru import logger

from api.db import db_client
from api.services.billing import agency


def _previous_month(today: date) -> date:
    first = today.replace(day=1)
    return (
        date(first.year - 1, 12, 1)
        if first.month == 1
        else date(first.year, first.month - 1, 1)
    )


async def accrue_agency_commission(_ctx) -> None:
    month = _previous_month(date.today())
    async with db_client.async_session() as session:
        accrued = await agency.accrue_month(session, month=month)
        await session.commit()

    logger.info(
        "Accrued agency commission for {}: {} client-month(s)",
        month.isoformat(),
        len(accrued),
    )

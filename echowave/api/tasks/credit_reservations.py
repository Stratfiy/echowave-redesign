"""Scheduled release of stranded credit reservations.

A call holds an estimate while it runs, and the hold is released when the call
is costed. Two things break that pairing, and both take real spending power
away from a paying customer:

* the worker handling a call dies, so the run is never costed and the hold is
  never released;
* a crash between releasing and debiting, or a run costed before enforcement
  existed, leaves a hold against an already-settled call.

Neither is visible to anyone. The customer sees a balance that is lower than
what they bought, and eventually cannot place a call despite having credit —
with nothing in the product able to explain why. So this runs on a schedule
rather than waiting for someone to notice.
"""

from loguru import logger

from api.db import db_client
from api.services.billing.reservations import sweep_stale


async def sweep_credit_reservations(_ctx) -> None:
    """Release holds against calls that will never be costed.

    Safe at any cadence: releasing a hold is idempotent, and a hold that is
    still doing its job — a live call, inside the age cut-off — is left alone.
    """
    async with db_client.async_session() as session:
        released_rows, released_paise = await sweep_stale(session)
        if released_rows:
            await session.commit()

    if released_rows:
        logger.info(
            "Swept {} stranded reservation(s), returning {} paise to accounts",
            released_rows,
            released_paise,
        )

"""Scheduled refresh of the daily billing rollup.

The dashboard's headline figures — Overview totals, the daily series, top
accounts, the accounts list — all read ``daily_organization_rollup`` rather
than scanning ``workflow_runs``, which is what keeps those pages inside the
performance budget at a million calls. Nothing refreshed that table outside the
seed script, so in production every one of those screens read an empty table
and reported zero while calls were billing correctly underneath.
"""

from loguru import logger

from api.db import db_client
from api.services.billing.rollup import refresh_recent_rollups

# A trailing window, not just today. A call is costed by the post-call job,
# which can land after the call's own IST day has rolled over — and a recost
# can revise a receipt later still. Three days is comfortably wider than either.
ROLLUP_WINDOW_DAYS = 3


async def refresh_billing_rollups(_ctx) -> None:
    """Recompute the recent daily rollups from the underlying calls.

    Safe to run at any cadence: :func:`refresh_daily_rollup` recomputes each
    day from scratch and replaces the row, so a repeated run converges rather
    than accumulating.
    """
    async with db_client.async_session() as session:
        written = await refresh_recent_rollups(session, days=ROLLUP_WINDOW_DAYS)
        await session.commit()

    logger.info(
        "Refreshed billing rollups for the last {} IST day(s): {} account-day row(s)",
        ROLLUP_WINDOW_DAYS,
        written,
    )

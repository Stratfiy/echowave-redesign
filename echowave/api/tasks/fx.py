"""Keep the exchange rate current.

The rate card carries two kinds of number. Provider prices are quoted on
marketing pages with no API behind them and stay operator-set. The USD→INR rate
has a real feed, and there is no reason for it to be whatever was true on the
day somebody ran a migration.

**Failures are quiet here, deliberately** — the opposite of the backup job.
A missed fetch leaves yesterday's rate in force, which misprices a call by
whatever the currency moved overnight; raising would take down the worker over
a rounding error. The fetcher itself refuses implausible values and writes
nothing on failure, so the worst case is staleness rather than a wrong number.
"""

from loguru import logger

from api.db import db_client
from api.services.billing import fx_source


async def refresh_exchange_rate(_ctx) -> None:
    """Fetch USD→INR and record it if it moved enough to matter."""
    try:
        async with db_client.async_session() as session:
            changed = await fx_source.refresh(session)
            await session.commit()
    except fx_source.FxFetchError as exc:
        logger.warning(
            "USD→INR refresh failed, keeping the rate already in force: {}", exc
        )
        return
    except Exception as exc:  # noqa: BLE001 — a bad night must not stop the worker
        logger.exception("Unexpected failure refreshing USD→INR: {}", exc)
        return

    if not changed:
        logger.debug("USD→INR unchanged; no new history row written.")

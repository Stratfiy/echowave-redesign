"""ARQ entry point for the settlement backstop.

See ``services/billing/settlement.py`` for why this exists. In short: the
completion job that costs a call is enqueued from exactly one place and is not
retried when it fails, so a lost enqueue or a transient error means a call we
paid providers for is never billed and nothing notices.
"""

from loguru import logger

from api.services.billing.settlement import settle_uncosted_runs


async def sweep_uncosted_runs(_ctx) -> None:
    """Cost completed runs that the normal path missed.

    A no-op on a healthy deployment: it is a query that returns nothing.
    """
    try:
        costed, failed = await settle_uncosted_runs()
    except Exception as error:  # noqa: BLE001 - a cron must not die on one bad tick
        logger.error("Settlement backstop sweep failed: {}", error)
        return

    if costed or failed:
        logger.info("Settlement backstop swept: {} costed, {} failed", costed, failed)

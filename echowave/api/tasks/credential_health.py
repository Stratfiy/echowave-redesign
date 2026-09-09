"""Scheduled check that our own provider keys still work.

See ``services/configuration/credential_validation.py`` for why. In short: a
revoked platform key stayed ``is_active``, the managed tier it backed stayed on
offer, and the first person to find out was an inbound caller listening to
silence.

Runs at startup and hourly. Hourly rather than per-call because a probe on the
dial path would add latency to every call to catch something that changes maybe
twice a year, and because vendors rate-limit key checks like any other request.
"""

from loguru import logger

from api.db import db_client
from api.services.configuration.credential_validation import (
    validate_stored_credentials,
)


async def check_platform_credentials(_ctx) -> None:
    """Probe every active platform key and record the verdict."""
    try:
        async with db_client.async_session() as session:
            results = await validate_stored_credentials(session)
    except Exception as error:  # noqa: BLE001 - a cron must not die on one bad tick
        logger.error("Platform credential check failed: {}", error)
        return

    bad = [
        f"{component}/{provider}" for component, provider, ok in results if ok is False
    ]
    if bad:
        logger.error(
            "{} of {} platform key(s) are being rejected by their provider: {}. "
            "Managed tiers on them are now withdrawn from the model picker.",
            len(bad),
            len(results),
            ", ".join(bad),
        )
    else:
        logger.info("Platform credential check: {} key(s), none rejected", len(results))

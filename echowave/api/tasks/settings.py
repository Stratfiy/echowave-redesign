"""Background work for launch stream `settings` (SETTINGS.md). Each is a
no-op while its switch is off, so registering them changes nothing until
then."""

from __future__ import annotations

from loguru import logger


async def build_personal_export(_ctx, request_id: int) -> None:
    """Build one person's export of their own data
    (services/settings/privacy.py)."""
    from api.services.settings import privacy

    await privacy.build_export(int(request_id))


async def purge_temporary_conversations(_ctx) -> None:
    """Delete temporary conversations past their time
    (services/settings/temporary.py). Runs whatever the switch says once a
    conversation exists: a promise to delete made while the switch was on is
    kept after it is turned off."""
    from api.services.settings import temporary

    purged = await temporary.purge_expired()
    if purged:
        logger.info("Temporary conversations purged: {}", purged)

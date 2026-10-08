"""Worker jobs for People (PEOPLE.md). Each a no-op while the flag is off
for the workspace concerned, so registering them changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def sync_people(_ctx, organization_id: int, user_id: int, provider: str) -> dict:
    """One person's contacts from one provider, from the saved cursor."""
    from api.services.people import sync

    counts = await sync.run(organization_id, user_id, provider)
    logger.info("People sync {} for user {}: {}", provider, user_id, counts)
    return counts


async def write_due_briefs(_ctx) -> None:
    """Briefs whose debounce window has passed (services/people/briefs.py)."""
    from api.services.people import briefs

    written = await briefs.sweep()
    if written:
        logger.info("Wrote {} people briefs", written)


async def resync_people(_ctx) -> None:
    """Contacts kept current: re-run syncs that are hours old."""
    from api.services.people import sync

    started = await sync.resync_due()
    if started:
        logger.info("Started {} people re-syncs", started)

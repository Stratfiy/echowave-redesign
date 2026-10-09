"""Scheduled work for reminder calls (services/reminder_calls). Each is a
no-op while ``reminder_calls`` is off everywhere, so registering them
changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def reminder_calls_tick(_ctx) -> None:
    """Make due occurrences and ring every attempt that has come due."""
    from api.services.reminder_calls import calls

    handled = await calls.tick()
    if handled:
        logger.info("reminder_calls: {} attempts handled", handled)


async def reminder_calls_sweep(_ctx) -> None:
    """Attempts with no outcome after the answer window are reconciled
    against their runs: settled on evidence, or unknown and said so."""
    from api.services.reminder_calls import calls

    changed = await calls.sweep()
    if changed:
        logger.info("reminder_calls: {} attempts reconciled", changed)

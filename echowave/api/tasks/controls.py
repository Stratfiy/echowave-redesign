"""Scheduled work for launch stream `controls`. Each is a no-op while its
switch is off, so registering them changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def deliver_analytics_outbox(_ctx) -> None:
    """Send waiting catalogue events on to analytics, and prune old ones
    (services/events/outbox.py)."""
    from api.services.events import outbox

    if not outbox.enabled():
        return
    result = await outbox.deliver_pending()
    if result["sent"] or result["failed"]:
        logger.info("Analytics outbox: {}", result)
    await outbox.prune()


async def sweep_unknown_outcomes(_ctx) -> None:
    """Cards whose job died mid-action become ``outcome_unknown`` instead of
    reading as running forever (services/workflow/actions.py)."""
    from api.services.workflow import actions

    marked = await actions.sweep_stale_running()
    if marked:
        logger.warning("Marked {} cards as outcome unknown", marked)

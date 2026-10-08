"""Scheduled work for launch stream `today`. Each is a no-op while its
switch is off everywhere, so registering them changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def deliver_due_reminders(_ctx) -> None:
    """Reminders whose time has come, delivered once each
    (services/today/ticks.py)."""
    from api.services.today import ticks

    counts = await ticks.deliver_due_reminders()
    if counts["delivered"] or counts["missed"]:
        logger.info("Today reminders: {}", counts)


async def deliver_due_briefs(_ctx) -> None:
    """Daily briefs and end-of-day notes at each person's own time."""
    from api.services.today import ticks

    delivered = await ticks.deliver_due_briefs()
    if delivered:
        logger.info("Delivered {} brief(s)", delivered)

"""Background work for launch stream `staff` (STAFF.md). Each is a no-op
while ``staff_console`` is off, so registering them changes nothing until
then."""

from __future__ import annotations

from loguru import logger

from api.services import features


async def run_staff_command(_ctx, command_id: int) -> str | None:
    """Run one queued staff command, once (compare-and-swap in ``run``)."""
    from api.services.staff import commands

    return await commands.run(command_id)


async def sweep_staff_commands(_ctx) -> None:
    """Expire unapproved requests, re-enqueue lost ones, mark runs that never
    reported back as outcome unknown, and read back pending refunds."""
    if not features.is_on("staff_console"):
        return
    from api.services.staff import commands, refunds

    counts = await commands.sweep()
    if any(counts.values()):
        logger.info("Staff command sweep: {}", counts)
    if features.is_on("staff_refunds"):
        changed = await refunds.reconcile_pending()
        if changed:
            logger.info("Reconciled {} refunds", changed)

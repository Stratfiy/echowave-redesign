"""Scheduled work for launch stream `care`. Each is a no-op while
``care_medicine_calls`` is off everywhere, so registering them changes
nothing until then."""

from __future__ import annotations

from loguru import logger


async def care_medicine_tick(_ctx) -> None:
    """Ring for every medicine dose that has come due (services/care/calls.py)."""
    from api.services.care import calls

    placed = await calls.tick()
    if placed:
        logger.info("Care: {} reminder calls placed", placed)


async def care_call_sweep(_ctx) -> None:
    """Reminder calls with no outcome after the answer window are reconciled
    against their runs: settled on evidence, or unknown -- never "not
    answered" for want of a report (services/care/calls.py)."""
    from api.services.care import calls

    settled = await calls.sweep()
    if settled:
        logger.info("Care: {} reminder calls reconciled", settled)

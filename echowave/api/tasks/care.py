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
    """Reminder calls with no outcome after the answer window were not
    answered; the family is told (services/care/calls.py)."""
    from api.services.care import calls

    settled = await calls.sweep()
    if settled:
        logger.info("Care: {} reminder calls marked not answered", settled)

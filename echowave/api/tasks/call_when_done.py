"""Scheduled work for "call me when it's done" (services/call_when_done).
Each is a no-op while ``call_when_done`` is off everywhere, so registering
them changes nothing until then."""

from __future__ import annotations

from loguru import logger


async def call_when_done_tick(_ctx) -> None:
    """Place every "it's done" call that has come due."""
    from api.services.call_when_done import calls

    handled = await calls.tick()
    if handled:
        logger.info("call_when_done: {} calls handled", handled)


async def call_when_done_sweep(_ctx) -> None:
    """Calls with no outcome after the answer window were not answered; the
    person is told in the app."""
    from api.services.call_when_done import calls

    settled = await calls.sweep()
    if settled:
        logger.info("call_when_done: {} calls marked not answered", settled)

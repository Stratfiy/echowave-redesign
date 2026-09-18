"""A text run that is over the moment its turn is.

A routine firing, a trigger, a task and a reply in a channel each run the
bot for exactly one turn and return. The engine only marks a run complete
when the conversation reached an end node, which one turn never does, so
every one of these stayed ``is_completed = False`` for ever and the Logs
screen called an eight-day-old reply "In Progress". On the live account it
was 97 of 214 runs.

One call, in the runner's ``finally``: whatever the turn did -- answered,
had nothing to say, ran out of credit, raised -- the run is finished, and
the row says so.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunState


async def close(run_id: int | None) -> None:
    """Mark a one-shot run finished. Never raises: the run already happened."""
    if run_id is None:
        return
    try:
        await db_client.update_workflow_run(
            run_id, is_completed=True, state=WorkflowRunState.COMPLETED.value
        )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail the run
        logger.warning("Run {} finished but could not be marked so: {}", run_id, exc)

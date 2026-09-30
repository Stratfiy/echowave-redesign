"""Scheduled deletion of call data past its retention window.

Storage limitation only exists if something enforces it. A retention policy
nobody runs is a sentence in a privacy notice, and the gap between the sentence
and the data still sitting in a bucket is exactly what a regulator asks about.

Runs nightly and in bounded batches, so a large backlog is worked through over
several nights rather than in one transaction that holds locks for an hour.
"""

from loguru import logger

from api.db import db_client
from api.services.privacy.retention import purge_expired

#: Runs examined per sweep. A busy platform simply catches up on the next pass.
BATCH_SIZE = 500


async def purge_expired_call_data(_ctx) -> None:
    """Delete recordings and transcripts that have outlived their window."""
    async with db_client.async_session() as session:
        result = await purge_expired(session, limit=BATCH_SIZE)
        if result["runs_purged"]:
            await session.commit()

    if result["runs_purged"]:
        logger.info(
            "Retention sweep purged {} run(s) and {} object(s) from {} examined",
            result["runs_purged"],
            result["objects_deleted"],
            result["candidates_examined"],
        )


async def close_due_workspaces(_ctx) -> None:
    """Delete workspaces whose owners asked, once the grace period has passed."""
    from api.services.privacy.workspace_closure import run_due

    async with db_client.async_session() as session:
        closed = await run_due(session)
        if closed:
            await session.commit()
    if closed:
        logger.info("Closed {} workspace(s) past their grace period", closed)

"""Writing one episode, and the accounting that stops it costing unboundedly.

Every episode handed to Graphiti is at least one LLM call: it extracts the
entities, resolves them against what is already in the partition, and decides
which existing facts the new one invalidates. That is the whole value and it is
also the whole cost, so the two rules here are that a write never blocks
anything a caller is waiting for, and that it never happens without a partition.

:func:`remember` returns whether the episode was written rather than raising,
because every caller's correct response to a failure is the same: log it and
carry on. A call that was handled well and not remembered is a call handled
well.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from api.services.knowledge_graph.client import episode_source_type, get_graph
from api.services.knowledge_graph.episodes import Episode

#: Seconds an ingestion may take before it is abandoned. Generous, because this
#: runs on the queue and nobody is waiting -- but bounded, because an
#: extraction that has hung holds a worker slot that completed calls need.
INGEST_TIMEOUT_SECONDS = 120.0


async def remember(episode: Episode) -> bool:
    """Write one episode to its organization's partition.

    Returns True only if the graph accepted it. False covers every other
    outcome -- no graph configured, graph unreachable, extraction failed,
    timed out -- because none of them changes what the caller should do next.
    """
    if not episode.group_id:
        # Unreachable through the public builders, which take a group_id
        # derived by `scoping`. Checked anyway: an episode written without a
        # partition is readable by every other tenant, and that is not a
        # failure worth discovering in production.
        logger.error(f"Refusing to write episode {episode.name} with no partition")
        return False

    graph = await get_graph()
    if graph is None:
        return False

    try:
        await asyncio.wait_for(
            graph.add_episode(
                name=episode.name,
                episode_body=episode.body,
                source_description=episode.source_description,
                reference_time=episode.reference_time,
                source=episode_source_type(episode.source),
                group_id=episode.group_id,
            ),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        logger.info(f"Remembered {episode.name} in {episode.group_id}")
        return True
    except Exception as exception:
        logger.warning(f"Could not remember {episode.name}: {exception}")
        return False

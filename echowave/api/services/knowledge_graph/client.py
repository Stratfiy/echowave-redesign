"""One graph connection per process, and no way for it to break a call.

The graph is a platform capability: every organization's calls go into it, on
the same terms, separated by the partition :mod:`scoping` derives. It is not a
per-account feature and there is no flag that turns it on for one customer.

What *is* optional is the infrastructure. ``KNOWLEDGE_GRAPH_URL`` being unset
means no graph is reachable from this deployment, and every function here
returns ``None`` quietly. A self-hosted install with no graph server keeps
behaving exactly as it does today rather than failing at import or on the first
call, and the same is true of a local checkout.

**Nothing in this module raises.** It is reached from post-call processing and
will eventually be reached from the call path, and a graph that is down, slow,
misconfigured or missing an API key must cost a caller nothing. Every failure
is a log line and a ``None``. That is a deliberate choice about what the graph
is for: it makes answers better when it works, and its absence is the product
as it is today, which is a product that works.

**Raw episode text is not stored in the graph.** ``store_raw_episode_content``
defaults to True, which would make the graph a second copy of every transcript
-- personal data in a store that :mod:`api.services.privacy.erasure` does not
know how to purge, sitting outside the retention window that governs the
recordings. The derived facts are what the graph is for; the transcript already
lives in object storage, where erasure can reach it and where a re-ingestion
can read it again if the extraction is ever improved.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from api.constants import KNOWLEDGE_GRAPH_URL
from api.services.knowledge_graph.episodes import SOURCE_TEXT

#: Built once per process, on first use. ``None`` means either not yet built or
#: not buildable; :data:`_build_attempted` tells the two apart so a deployment
#: without a graph does not retry the import on every completed call.
_client: Any | None = None
_build_attempted = False
_build_lock = asyncio.Lock()


def graph_is_configured() -> bool:
    """Whether this deployment has a graph to write to at all."""
    return bool(KNOWLEDGE_GRAPH_URL)


async def get_graph() -> Any | None:
    """The process's Graphiti client, or None if there isn't one.

    ``graphiti_core`` is imported here rather than at module scope on purpose.
    The pipeline worker imports this package for its episode builders while a
    call is being set up, and that process should not pay for a graph library,
    a Neo4j driver and an OpenAI client it will never use on that path.
    """
    global _client, _build_attempted

    if not graph_is_configured():
        return None
    if _client is not None:
        return _client

    async with _build_lock:
        # Re-checked inside the lock: two calls completing at once would
        # otherwise build two clients and two connection pools.
        if _client is not None:
            return _client
        if _build_attempted:
            return None
        _build_attempted = True

        try:
            client = _construct_graphiti(KNOWLEDGE_GRAPH_URL)
            # Graphiti needs its indices before the first write, and creating
            # them is idempotent. Doing it here rather than in a migration
            # keeps the graph's schema owned by the library that defines it.
            await client.build_indices_and_constraints()
            _client = client
            logger.info("Knowledge graph connected")
            return _client
        except Exception as exception:
            # Includes the library being absent, the server being unreachable,
            # and no model credentials in this process's environment. All of
            # them mean the same thing to a caller: no graph today.
            logger.warning(
                f"Knowledge graph unavailable, continuing without it: {exception}"
            )
            return None


def _construct_graphiti(uri: str) -> Any:
    """Build the library's client. One of only two places that name it.

    A function rather than an inline import so that everything above can be
    tested without ``graphiti_core`` installed -- which is also the honest
    shape, since a deployment without a graph never loads it either.
    """
    from graphiti_core import Graphiti

    # Raw episode text stays out of the graph: see the module docstring.
    return Graphiti(uri=uri, store_raw_episode_content=False)


def episode_source_type(source: str) -> Any:
    """Map our source name to the library's enum. The other of the two."""
    from graphiti_core.nodes import EpisodeType

    return EpisodeType.text if source == SOURCE_TEXT else EpisodeType.message


async def reset_for_tests() -> None:
    """Forget the cached client. Tests only."""
    global _client, _build_attempted
    _client = None
    _build_attempted = False

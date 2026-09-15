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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from api.constants import (
    KNOWLEDGE_GRAPH_EMBEDDING_MODEL,
    KNOWLEDGE_GRAPH_MODEL,
    KNOWLEDGE_GRAPH_SMALL_MODEL,
    KNOWLEDGE_GRAPH_URL,
)
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
            client = await _construct_graphiti(KNOWLEDGE_GRAPH_URL)
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


@dataclass(frozen=True)
class GraphTarget:
    """Where the graph lives, parsed from ``KNOWLEDGE_GRAPH_URL``.

    ``falkor://[:password@]host[:port][/database]`` is the server this
    deployment runs (Redis with the FalkorDB module -- see requirements.txt);
    ``bolt://`` and ``neo4j://`` go to the library's Neo4j driver unchanged.
    """

    scheme: str
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    database: str = "default_db"
    uri: str | None = None


def parse_graph_url(url: str) -> GraphTarget:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("falkor", "falkordb", "redis"):
        return GraphTarget(
            scheme="falkor",
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            password=parsed.password or None,
            database=(parsed.path or "").strip("/") or "default_db",
        )
    return GraphTarget(scheme=scheme or "bolt", uri=url)


async def _platform_openai_key() -> str | None:
    """The platform's own OpenAI key, from the vault. Graphiti extracts and
    embeds with it; a deployment with no managed OpenAI key has no graph."""
    from api.db import db_client
    from api.services.configuration.platform_credentials import resolve_api_key

    async with db_client.async_session() as session:
        return await resolve_api_key(session, component="llm", provider="openai")


async def _construct_graphiti(uri: str) -> Any:
    """Build the library's client. One of only two places that name it.

    A function rather than an inline import so that everything above can be
    tested without ``graphiti_core`` installed -- which is also the honest
    shape, since a deployment without a graph never loads it either.
    """
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import (
        OpenAIRerankerClient,
    )
    from graphiti_core.embedder import OpenAIEmbedder
    from graphiti_core.embedder.openai import OpenAIEmbedderConfig
    from graphiti_core.llm_client import OpenAIClient
    from graphiti_core.llm_client.config import LLMConfig

    api_key = await _platform_openai_key()
    if not api_key:
        raise RuntimeError("no managed OpenAI key for the graph's extraction")
    llm = OpenAIClient(
        config=LLMConfig(
            api_key=api_key,
            model=KNOWLEDGE_GRAPH_MODEL,
            small_model=KNOWLEDGE_GRAPH_SMALL_MODEL,
        )
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=api_key, embedding_model=KNOWLEDGE_GRAPH_EMBEDDING_MODEL
        )
    )
    reranker = OpenAIRerankerClient(
        config=LLMConfig(api_key=api_key, model=KNOWLEDGE_GRAPH_SMALL_MODEL)
    )
    target = parse_graph_url(uri)
    common = dict(
        llm_client=llm,
        embedder=embedder,
        cross_encoder=reranker,
        # Raw episode text stays out of the graph: see the module docstring.
        store_raw_episode_content=False,
    )
    if target.scheme == "falkor":
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        driver = FalkorDriver(
            host=target.host,
            port=target.port,
            password=target.password,
            database=target.database,
        )
        return Graphiti(graph_driver=driver, **common)
    return Graphiti(uri=target.uri, **common)


def episode_source_type(source: str) -> Any:
    """Map our source name to the library's enum. The other of the two."""
    from graphiti_core.nodes import EpisodeType

    return EpisodeType.text if source == SOURCE_TEXT else EpisodeType.message


@dataclass(frozen=True)
class Fact:
    """One thing the graph holds, as a person would read it."""

    uuid: str
    fact: str
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime | None
    episodes: tuple[str, ...]
    #: "confirmed" when a confirmed fact in the account's own record says
    #: the same thing; "inferred" otherwise. Only the record confers belief
    #: -- the graph never does.
    status: str = "inferred"

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "status": self.status,
            "from": self.valid_at.date().isoformat() if self.valid_at else None,
            "until": self.invalid_at.date().isoformat() if self.invalid_at else None,
            "recorded": self.created_at.date().isoformat() if self.created_at else None,
        }


async def search_facts(
    organization_id: int,
    query: str,
    *,
    limit: int = 12,
    since: datetime | None = None,
    until: datetime | None = None,
    include_closed: bool = False,
) -> list[Fact] | None:
    """Facts in this organisation's partition that bear on ``query``, newest
    first, with the confirmed/inferred overlay. None when there is no graph
    (the caller says so); an empty list when the graph has nothing.

    An edge the graph has closed (``invalid_at`` set -- a later episode,
    such as a correction, contradicted it) is history and left out unless
    ``include_closed``: a present-tense answer must not carry it."""
    from api.services.knowledge_graph.scoping import group_ids_for_search

    graph = await get_graph()
    if graph is None:
        return None
    try:
        edges = await asyncio.wait_for(
            graph.search(
                query,
                group_ids=group_ids_for_search(organization_id),
                num_results=max(limit * 2, 20),
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except Exception as exception:  # noqa: BLE001 - no graph answer is an answer
        logger.warning(f"Graph search failed for org {organization_id}: {exception}")
        return None
    facts = _facts_from_edges(
        edges or [], since=since, until=until, include_closed=include_closed
    )[:limit]
    return await _overlay_confirmed(organization_id, facts)


def _facts_from_edges(
    edges: Any,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    include_closed: bool = False,
    by: str = "valid_at",
) -> list[Fact]:
    """Edges to facts, newest first, within the window. ``by`` names which
    time the window and order use: when the fact held (recall) or when the
    graph learned it (the Sunday review's "added this week")."""
    facts: list[Fact] = []
    for edge in edges:
        valid_at = getattr(edge, "valid_at", None)
        created_at = getattr(edge, "created_at", None)
        invalid_at = getattr(edge, "invalid_at", None)
        if invalid_at and not include_closed:
            continue
        when = created_at if by == "created_at" else (valid_at or created_at)
        if since and when and when < since:
            continue
        if until and when and when > until:
            continue
        facts.append(
            Fact(
                uuid=str(getattr(edge, "uuid", "")),
                fact=str(getattr(edge, "fact", "") or ""),
                valid_at=valid_at,
                invalid_at=invalid_at,
                created_at=created_at,
                episodes=tuple(str(e) for e in (getattr(edge, "episodes", None) or [])),
            )
        )

    def _when(f: Fact) -> datetime:
        t = f.created_at if by == "created_at" else (f.valid_at or f.created_at)
        return (t or datetime.min).replace(tzinfo=None)

    facts.sort(key=_when, reverse=True)
    return facts


async def recent_facts(
    organization_id: int, *, since: datetime, limit: int = 200
) -> list[Fact] | None:
    """What the graph learned about this organisation since ``since``,
    newest first -- not a query, a listing, for the Sunday review (B3).
    None when there is no graph."""
    from api.services.knowledge_graph.scoping import group_ids_for_search

    graph = await get_graph()
    if graph is None:
        return None
    try:
        from graphiti_core.edges import EntityEdge

        # Listed by uuid, not time, so over-fetch and filter here. Small
        # accounts by a wide margin; a large one gets its newest page.
        edges = await asyncio.wait_for(
            EntityEdge.get_by_group_ids(
                graph.driver,
                group_ids_for_search(organization_id),
                limit=max(limit * 4, 400),
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except Exception as exception:  # noqa: BLE001
        logger.warning(f"Graph listing failed for org {organization_id}: {exception}")
        return None
    facts = _facts_from_edges(
        edges or [], since=since, include_closed=True, by="created_at"
    )
    return await _overlay_confirmed(organization_id, facts[:limit])


async def _overlay_confirmed(organization_id: int, facts: list[Fact]) -> list[Fact]:
    """Mark a graph fact confirmed only when the account's own record holds a
    confirmed fact whose value appears in it. Cheap and conservative: a miss
    leaves the fact inferred, which is the honest default."""
    if not facts:
        return facts
    try:
        from api.db import db_client

        rows = list(
            await db_client.organisation_memory(
                organization_id=organization_id,
                kind="fact",
                status="confirmed",
                limit=500,
            )
        ) + list(
            await db_client.subject_facts(
                organization_id=organization_id, status="confirmed"
            )
        )
    except Exception as exception:  # noqa: BLE001
        logger.warning(
            f"Could not read confirmed facts for org {organization_id}: {exception}"
        )
        return facts
    values = [str(getattr(r, "value", "") or "").strip().lower() for r in rows]
    values = [v for v in values if len(v) >= 4]
    out: list[Fact] = []
    for fact in facts:
        text = fact.fact.lower()
        if any(v in text for v in values):
            fact = Fact(**{**fact.__dict__, "status": "confirmed"})
        out.append(fact)
    return out


#: A graph search is on the answer path; a slow graph must not hold a reply.
SEARCH_TIMEOUT_SECONDS = 8.0


async def reset_for_tests() -> None:
    """Forget the cached client. Tests only."""
    global _client, _build_attempted
    _client = None
    _build_attempted = False

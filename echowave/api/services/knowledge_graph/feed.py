"""What the graph is fed, and from where (Family B substrate).

Three sources, three functions, one rule: none of them can break the thing
that called them. A call that ended, a line on the Home thread, a document
that arrived on a channel -- each becomes an episode in the account's own
partition (``scoping``) if there is a graph, and nothing at all if there
is not. Postgres stays the record of what is *confirmed*; the graph holds
what was said and done, with time, so relations can be asked about.

Every writer here is ``await``-ed by its caller inside a try, and returns
False on any failure, so the worst a dead graph costs is a log line.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.services.knowledge_graph import episodes, ingest, scoping
from api.services.knowledge_graph.client import graph_is_configured

#: A thread exchange shorter than this teaches the graph nothing.
MIN_EXCHANGE_CHARS = 40
MAX_EXCHANGE_CHARS = 6_000


async def remember_call(
    *,
    organization_id: int,
    workflow_run_id: int,
    agent_name: str,
    transcript: str | None,
    happened_at: datetime,
    gathered_context: dict[str, Any] | None = None,
) -> bool:
    if not graph_is_configured():
        return False
    try:
        episode = episodes.episode_from_call(
            organization_id=organization_id,
            group_id=scoping.group_id_for_organization(organization_id),
            workflow_run_id=workflow_run_id,
            agent_name=agent_name,
            transcript=transcript,
            happened_at=happened_at,
            gathered_context=gathered_context,
        )
        if episode is None:
            return False
        return await ingest.remember(episode)
    except Exception as exc:  # noqa: BLE001 - the call is already over
        logger.warning(
            "Could not remember call {} in the graph: {}", workflow_run_id, exc
        )
        return False


async def remember_exchange(
    *,
    organization_id: int,
    person_said: str,
    decibyl_said: str,
    at: datetime | None = None,
    channel: str | None = None,
) -> bool:
    """One turn on the Home thread: what the person said and what Decibyl
    answered. The person's words are where promises, decisions and
    complaints live; the answer is kept so a fact Decibyl stated (from the
    record) is anchored to the same moment."""
    if not graph_is_configured():
        return False
    person_said = (person_said or "").strip()
    decibyl_said = (decibyl_said or "").strip()
    body = f"Person: {person_said}\nDecibyl: {decibyl_said}".strip()
    if len(person_said) < MIN_EXCHANGE_CHARS and len(body) < MIN_EXCHANGE_CHARS * 2:
        return False
    body = body[:MAX_EXCHANGE_CHARS]
    at = at or datetime.now(UTC)
    try:
        episode = episodes.Episode(
            name=f"thread-{organization_id}-{int(at.timestamp())}",
            body=body,
            source_description=f"Home thread with Decibyl"
            + (f" via {channel}" if channel else ""),
            reference_time=at,
            group_id=scoping.group_id_for_organization(organization_id),
            source=episodes.SOURCE_MESSAGE,
        )
        return await ingest.remember(episode)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not remember a thread exchange for org {}: {}", organization_id, exc
        )
        return False


async def remember_document(
    *,
    organization_id: int,
    document_uuid: str,
    filename: str,
    chunks: list[str],
    published_at: datetime | None = None,
) -> int:
    """A document that arrived on a channel, one episode per chunk (the
    knowledge base's own chunks). Returns how many the graph took."""
    if not graph_is_configured() or not chunks:
        return 0
    taken = 0
    try:
        for episode in episodes.episodes_from_document(
            group_id=scoping.group_id_for_organization(organization_id),
            document_uuid=document_uuid,
            filename=filename,
            chunks=chunks,
            published_at=published_at or datetime.now(UTC),
        ):
            if await ingest.remember(episode):
                taken += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not remember document {} in the graph: {}", document_uuid, exc
        )
    return taken


async def remember_correction(
    *,
    organization_id: int,
    subject: str,
    key: str,
    value: str,
    was: str | None,
    at: datetime,
) -> bool:
    """A correction the person made (B5), as its own dated episode, so the
    graph closes the edge it contradicts. Stated plainly and marked
    confirmed, which is how the extractor is told it outranks the rest."""
    if not graph_is_configured():
        return False
    body = f"Correction confirmed by the person: {subject} — {key}: {value}."
    if was:
        body += f" Not {was}; that was wrong."
    try:
        episode = episodes.Episode(
            name=f"correction-{organization_id}-{int(at.timestamp())}",
            body=body,
            source_description="Correction on the Home thread, confirmed by the person",
            reference_time=at,
            group_id=scoping.group_id_for_organization(organization_id),
            source=episodes.SOURCE_TEXT,
        )
        return await ingest.remember(episode)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not remember a correction for org {}: {}", organization_id, exc
        )
        return False


__all__ = [
    "remember_call",
    "remember_correction",
    "remember_document",
    "remember_exchange",
]

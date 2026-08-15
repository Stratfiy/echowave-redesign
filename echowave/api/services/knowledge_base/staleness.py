"""Documents that were ingested under a different embedding model.

``search_similar_chunks`` filters on ``embedding_model``, and it is right to:
a 1536-dimension OpenAI vector and a 384-dimension MiniLM vector are not
comparable, and searching across both returns confident nonsense.

The consequence is the problem. An account that switches embedding provider
keeps every document listed as ``completed`` on the files screen, and the agent
retrieves nothing from any of them. No error is raised, nothing is logged at
the moment it starts happening, and the first sign is an agent that has
apparently forgotten everything it was told. That is the same shape as the
zero-chunk bug: a screen that says ready, and an agent that knows nothing.

This module does not fix it by re-embedding. Re-ingesting a customer's whole
library spends their money on their behalf, at a moment nobody chose, and the
bill for a few thousand documents is not small. It makes the state **visible**
instead, so the decision to re-ingest is one somebody makes.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import KnowledgeBaseChunkModel


async def current_embedding_model(organization_id: int) -> str | None:
    """The embedding model this organization's *next* ingestion would use.

    This has to agree with two other places or it is worse than useless: the
    ingestion task stores ``embedding_service.get_model_id()`` on every chunk,
    and the search filters on the same value. Both get it from
    :func:`build_embedding_service`, which resolves an unset model to
    ``DEFAULT_EMBEDDING_MODEL`` — so an organization that has never opened the
    model screen is not "unknown", it is on the default, and a document
    ingested under a since-abandoned custom model genuinely is stranded from
    it. Reading the configured value alone would miss exactly that case.

    ``None`` is reserved for "could not tell", which reports nothing rather
    than guessing.
    """
    from api.services.configuration.ai_model_configuration import (
        get_resolved_ai_model_configuration,
    )
    from api.services.gen_ai.embedding.factory import DEFAULT_EMBEDDING_MODEL

    try:
        resolved = await get_resolved_ai_model_configuration(
            organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001 — this decorates a list, never gates it
        logger.warning(
            "Could not resolve the embedding model for organization {}: {}",
            organization_id,
            exc,
        )
        return None

    embeddings = getattr(getattr(resolved, "effective", None), "embeddings", None)
    configured = getattr(embeddings, "model", None) if embeddings else None
    return configured or DEFAULT_EMBEDDING_MODEL


async def embedding_model_by_document(
    session: AsyncSession, organization_id: int
) -> dict[int, str]:
    """Which model each document's chunks were embedded with.

    One grouped query rather than one per document: this decorates a list, and
    a list endpoint that issues a query per row is how a screen that was fine
    with ten documents becomes unusable at four hundred.

    A document whose chunks somehow carry more than one model — which should be
    impossible, since re-ingestion replaces them wholesale — is reported under
    whichever sorts last, and is stranded either way.
    """
    rows = await session.execute(
        select(
            KnowledgeBaseChunkModel.document_id,
            func.max(KnowledgeBaseChunkModel.embedding_model),
        )
        .where(KnowledgeBaseChunkModel.organization_id == organization_id)
        .group_by(KnowledgeBaseChunkModel.document_id)
    )
    return {document_id: model for document_id, model in rows.all() if model}


def is_stranded(stored: str | None, current: str | None) -> bool:
    """Whether a document embedded with ``stored`` is invisible to ``current``.

    Both unknowns mean "do not claim a problem". A document with no chunks yet
    is pending or failed, and has its own status saying so; an organization
    with no configured model is on defaults and nothing is mismatched.
    """
    if not stored or not current:
        return False
    return stored != current


async def stranded_document_ids(
    session: AsyncSession, organization_id: int
) -> set[int]:
    """The documents this organization can no longer retrieve from."""
    current = await current_embedding_model(organization_id)
    if not current:
        return set()

    by_document = await embedding_model_by_document(session, organization_id)
    return {
        document_id
        for document_id, stored in by_document.items()
        if is_stranded(stored, current)
    }

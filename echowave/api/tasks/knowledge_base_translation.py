"""The job behind "translate this document" -- see
services/knowledge_base/translate_document.py."""

from __future__ import annotations

from api.services.knowledge_base import translate_document


async def translate_knowledge_base_document(
    _ctx,
    document_id: int,
    source_id: int,
    organization_id: int,
    provider_id: str,
    target: str,
) -> None:
    await translate_document.run(
        document_id=int(document_id),
        source_id=int(source_id),
        organization_id=int(organization_id),
        provider_id=str(provider_id),
        target=str(target),
    )

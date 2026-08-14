"""Which backend converts and chunks a document, and the shape both return.

The ingestion task downstream of this module does not branch on the backend.
It receives one dict — ``mode``, ``docling_metadata``, ``full_text``,
``chunks`` — and embeds and stores whatever is in it. That is deliberate: the
seam is the only reason swapping the remote service for local code was a
contained change rather than a rewrite of the task.

``KB_DOCUMENT_PROCESSOR`` picks the backend:

``local`` (default)
    Convert and chunk in this process. No network call, so ingestion works on
    a deployment that has nothing else stood up.
``mps``
    Delegate to the Model Proxy Service, as before. For a deployment that runs
    MPS and wants its higher-fidelity conversion.
``auto``
    MPS when ``MPS_API_URL`` is configured, local otherwise, and **local on any
    MPS failure**. The fallback is the point: a customer uploading a policy
    document during an MPS outage gets a working knowledge base rather than a
    transport error.

Default is ``local`` rather than ``auto`` because a default that reaches for a
hostname a deployment never configured is how this became a ship blocker in
the first place.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.constants import KB_DOCUMENT_PROCESSOR, MPS_API_URL
from api.services.knowledge_base.chunking import chunk_blocks
from api.services.knowledge_base.errors import EmptyDocumentError, KnowledgeBaseError
from api.services.knowledge_base.extraction import extract_document

LOCAL_BACKEND = "local"
MPS_BACKEND = "mps"
AUTO_BACKEND = "auto"

VALID_BACKENDS = (LOCAL_BACKEND, MPS_BACKEND, AUTO_BACKEND)

CHUNKED = "chunked"
FULL_DOCUMENT = "full_document"


def resolve_backend(configured: str | None = None) -> str:
    """Which backend this deployment should use, as a concrete choice.

    ``auto`` is resolved here rather than left to the call site so the answer
    is one thing, logged once, and testable without a document.
    """
    choice = (configured or KB_DOCUMENT_PROCESSOR or LOCAL_BACKEND).strip().lower()

    if choice not in VALID_BACKENDS:
        logger.warning(
            f"KB_DOCUMENT_PROCESSOR={choice!r} is not one of "
            f"{VALID_BACKENDS}; falling back to {LOCAL_BACKEND}."
        )
        return LOCAL_BACKEND

    if choice == AUTO_BACKEND:
        return MPS_BACKEND if MPS_API_URL else LOCAL_BACKEND

    return choice


def process_document_locally(
    *,
    file_path: str,
    filename: str,
    content_type: str | None = None,
    retrieval_mode: str = CHUNKED,
    max_tokens: int = 128,
    chunk_overlap_tokens: int = 0,
    merge_peers: bool = True,
) -> dict[str, Any]:
    """Convert and chunk in this process, in the MPS response shape.

    Raises :class:`~api.services.knowledge_base.errors.EmptyDocumentError` when
    the file yields nothing to retrieve — including the chunked mode where
    extraction succeeded but produced no chunk. Zero chunks is a failed
    ingestion wearing a success's clothes: the document lists as ready and the
    agent knows nothing from it.
    """
    extracted = extract_document(file_path, filename, content_type)
    metadata = dict(extracted.metadata)
    metadata["retrieval_mode"] = retrieval_mode

    if retrieval_mode == FULL_DOCUMENT:
        full_text = extracted.full_text
        if not full_text.strip():
            raise EmptyDocumentError(
                f"No text extracted from {filename}",
                user_message=(
                    "This file contains no text we can read, so there would be "
                    "nothing for the agent to answer from."
                ),
            )
        return {
            "mode": FULL_DOCUMENT,
            "docling_metadata": metadata,
            "full_text": full_text,
            "chunks": [],
        }

    chunks = chunk_blocks(
        extracted.blocks,
        max_tokens=max_tokens,
        overlap_tokens=chunk_overlap_tokens,
        merge_peers=merge_peers,
        source_name=filename,
    )

    if not chunks:
        raise EmptyDocumentError(
            f"Chunking produced nothing for {filename}",
            user_message=(
                "We read this document but could not split it into anything "
                "the agent can quote. If it is mostly images or tables, try "
                "uploading a text version."
            ),
        )

    metadata["chunk_count"] = len(chunks)
    metadata["max_tokens"] = max_tokens
    metadata["chunk_overlap_tokens"] = chunk_overlap_tokens

    return {
        "mode": CHUNKED,
        "docling_metadata": metadata,
        "full_text": None,
        "chunks": [chunk.as_mps_chunk() for chunk in chunks],
    }


async def process_document(
    *,
    file_path: str,
    filename: str,
    content_type: str | None = None,
    retrieval_mode: str = CHUNKED,
    max_tokens: int = 128,
    chunk_overlap_tokens: int = 0,
    merge_peers: bool = True,
    organization_id: int | None = None,
    created_by: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Convert and chunk ``file_path`` using whichever backend applies."""
    chosen = resolve_backend(backend)

    if chosen == LOCAL_BACKEND:
        return process_document_locally(
            file_path=file_path,
            filename=filename,
            content_type=content_type,
            retrieval_mode=retrieval_mode,
            max_tokens=max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            merge_peers=merge_peers,
        )

    from api.services.mps_service_key_client import mps_service_key_client

    try:
        response = await mps_service_key_client.process_document(
            file_path=file_path,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            retrieval_mode=retrieval_mode,
            max_tokens=max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            merge_peers=merge_peers,
            organization_id=organization_id,
            created_by=created_by,
        )
    except KnowledgeBaseError:
        raise
    except Exception as exc:
        if (backend or KB_DOCUMENT_PROCESSOR or "").strip().lower() == MPS_BACKEND:
            # Explicitly pinned to MPS: falling back silently would hide an
            # outage from whoever pinned it, and they pinned it for a reason.
            raise
        logger.warning(
            f"MPS document processing failed ({exc}); processing {filename} "
            "locally instead."
        )
        return process_document_locally(
            file_path=file_path,
            filename=filename,
            content_type=content_type,
            retrieval_mode=retrieval_mode,
            max_tokens=max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            merge_peers=merge_peers,
        )

    metadata = response.get("docling_metadata") or {}
    metadata.setdefault("backend", MPS_BACKEND)
    response["docling_metadata"] = metadata

    # MPS reporting zero chunks is the same failed ingestion as ours reporting
    # zero, and it used to be recorded as completed. Same rule, both backends.
    if retrieval_mode == CHUNKED and not response.get("chunks"):
        raise EmptyDocumentError(
            f"Document processing returned zero chunks for {filename}",
            user_message=(
                "We read this document but could not split it into anything "
                "the agent can quote. If it is a scan or mostly images, run it "
                "through OCR and upload it again."
            ),
        )

    return response

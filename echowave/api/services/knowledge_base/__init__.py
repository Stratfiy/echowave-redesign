"""Turning an uploaded file into retrievable chunks, in this process.

Until now the middle of the ingestion path — convert the file, chunk the text —
was the one stage that left the box, delegated to MPS. Everything downstream
(embedding, the pgvector write, the per-organization search) already ran here.
That single remote hop was the difference between a deployment that can answer
questions from a customer's documents and one that returns a transport error,
so it is now implemented locally and used by default.

The public entry point is :func:`process_document`, which returns the same
shape MPS returned. That shape is the seam: the ingestion task does not know
or care which backend produced the chunks, so ``KB_DOCUMENT_PROCESSOR=mps``
still works for a deployment that has MPS and wants it.
"""

from api.services.knowledge_base import staleness
from api.services.knowledge_base.chunking import Chunk, chunk_text, count_tokens
from api.services.knowledge_base.errors import (
    DocumentExtractionError,
    EmptyDocumentError,
    KnowledgeBaseError,
    UnsupportedDocumentTypeError,
    user_facing_message,
)
from api.services.knowledge_base.extraction import ExtractedDocument, extract_document
from api.services.knowledge_base.processor import (
    LOCAL_BACKEND,
    MPS_BACKEND,
    process_document,
    process_document_locally,
    resolve_backend,
)

__all__ = [
    "Chunk",
    "DocumentExtractionError",
    "EmptyDocumentError",
    "ExtractedDocument",
    "KnowledgeBaseError",
    "LOCAL_BACKEND",
    "MPS_BACKEND",
    "UnsupportedDocumentTypeError",
    "chunk_text",
    "count_tokens",
    "extract_document",
    "process_document",
    "process_document_locally",
    "resolve_backend",
    "staleness",
    "user_facing_message",
]

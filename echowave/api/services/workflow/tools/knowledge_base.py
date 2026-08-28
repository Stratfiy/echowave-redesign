"""Knowledge Base retrieval tool for workflow execution.

This module provides vector similarity search capabilities for retrieving
relevant information from the knowledge base during conversations.

Implements OpenTelemetry tracing for observability in Langfuse.

Two things about the returned payload are load-bearing, because pipecat
serialises the whole dictionary into the LLM's context as the tool result
(``json.dumps(frame.result)`` in ``llm_response_universal``) — so every key
here is something the model reads, and can repeat to the person on the phone.

**A failed lookup is not an empty one.** Both used to come back as zero chunks,
and the model has no way to tell them apart, so a caller asking about a policy
we document perfectly well hears "I don't have that information" when the truth
is "I could not reach the search". That is a wrong answer delivered
confidently, which is worse than an admission of trouble. :data:`STATUS_OK`,
:data:`STATUS_NO_MATCH` and :data:`STATUS_UNAVAILABLE` separate them, and each
non-ok result carries a plain-language ``instruction`` telling the model what
to do about it.

**Internal error text never goes in.** ``str(exception)`` from an embedding
provider carries base URLs, host names and console instructions ("set your API
key in Model Configurations"), and the model will happily read them out to a
stranger. The detail belongs in the log and on the span, where an operator
reads it; the model gets a sentence written for a caller's ears.
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger
from opentelemetry import trace

from api.db import db_client
from api.services.gen_ai import build_embedding_service
from api.services.pipecat.tracing_config import ensure_tracing

#: The search ran and found something.
STATUS_OK = "ok"
#: The search ran and nothing matched. The knowledge base is working.
STATUS_NO_MATCH = "no_match"
#: The search could not be performed. Says nothing about what we know.
STATUS_UNAVAILABLE = "unavailable"

NO_MATCH_INSTRUCTION = (
    "The knowledge base was searched successfully and nothing relevant was "
    "found. It is correct to tell the caller you do not have that information."
)

UNAVAILABLE_INSTRUCTION = (
    "The knowledge base could not be searched. This is a fault on our side, "
    "not a gap in what we know, so do NOT tell the caller we have no "
    "information about this — that would be misleading. Say you are unable to "
    "look it up right now, offer to have someone follow up, and continue "
    "helping with anything else. Do not describe the technical problem and do "
    "not read out any error message."
)


def retrieval_unavailable(query: str, exception: BaseException) -> Dict[str, Any]:
    """The payload for a lookup that could not be performed.

    The exception is logged and recorded on the current span — the payload
    itself carries nothing but the status and an instruction, because
    everything in it is read by the model and can be spoken to a caller.
    """
    logger.error(f"Error retrieving from knowledge base: {exception}")

    span = trace.get_current_span()
    if span is not None:
        # A no-op span when tracing is off; harmless either way.
        span.set_attribute("retrieval.error", str(exception))
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))

    return {
        "status": STATUS_UNAVAILABLE,
        "instruction": UNAVAILABLE_INSTRUCTION,
        "chunks": [],
        "query": query,
        "total_results": 0,
    }


async def retrieve_from_knowledge_base(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]] = None,
    limit: int = 3,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
    embeddings_base_url: Optional[str] = None,
    embeddings_provider: Optional[str] = None,
    embeddings_endpoint: Optional[str] = None,
    embeddings_api_version: Optional[str] = None,
    correlation_id: Optional[str] = None,
    tracing_context=None,
    billing_sink: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Retrieve relevant information from the knowledge base using vector similarity search.

    Uses OpenAI text-embedding-3-small for embeddings by default. This provides
    high-quality 1536-dimensional embeddings for accurate retrieval.

    This function includes OpenTelemetry tracing for Langfuse observability.

    Args:
        query: The search query to find relevant information
        organization_id: Organization ID for scoping the search
        document_uuids: Optional list of document UUIDs to filter by
        limit: Maximum number of chunks to return (default: 3)
        embeddings_api_key: Optional API key for embedding service
        embeddings_model: Optional model ID for embedding service
        embeddings_base_url: Optional base URL for embedding service
        tracing_context: Optional OpenTelemetry context for tracing
        billing_sink: Optional dict the caller owns. If the query embedding
            actually runs, this is populated in place with
            ``{"provider", "model", "tokens"}`` before this function returns —
            an out-parameter rather than a return value, deliberately, because
            it must never become a key on the returned dict below: every key
            there is serialised straight into the LLM's context, and a billing
            figure has no business in a conversation. ``tokens`` is ``None``
            when the vendor's response carried no usage figure to read.

    Returns:
        Dictionary containing:
        - status: One of ``ok``, ``no_match`` or ``unavailable``
        - chunks: List of relevant text chunks with metadata
        - query: The original query
        - total_results: Number of results returned
        - instruction: What the model should do, when the status is not ``ok``

        Every key is serialised into the LLM's context, so nothing internal
        goes in — see the module docstring.
    """
    # Create span for retrieval operation if tracing is enabled
    if ensure_tracing():
        try:
            parent_context = tracing_context

            # Get tracer
            tracer = trace.get_tracer("pipecat")
        except Exception as e:
            logger.debug(f"Failed to setup tracing context: {e}")
            # Fall back to non-traced execution
            return await _perform_retrieval(
                query,
                organization_id,
                document_uuids,
                limit,
                embeddings_api_key,
                embeddings_model,
                embeddings_base_url,
                embeddings_provider,
                embeddings_endpoint,
                embeddings_api_version,
                correlation_id,
                billing_sink=billing_sink,
            )

        # Create span with parent context
        if parent_context:
            with tracer.start_as_current_span(
                "knowledge_base_retrieval", context=parent_context
            ) as span:
                try:
                    # Mark trace as public for Langfuse
                    span.set_attribute("langfuse.trace.public", True)

                    # Add operation metadata
                    span.set_attribute(
                        "gen_ai.operation.name", "knowledge_base_retrieval"
                    )
                    span.set_attribute("retrieval.query", query)
                    span.set_attribute("retrieval.limit", limit)
                    span.set_attribute("retrieval.organization_id", organization_id)

                    # Add document filter info
                    if document_uuids:
                        span.set_attribute(
                            "retrieval.document_count", len(document_uuids)
                        )
                        span.set_attribute(
                            "retrieval.document_uuids", json.dumps(document_uuids)
                        )

                    # Perform the actual retrieval
                    result = await _perform_retrieval(
                        query,
                        organization_id,
                        document_uuids,
                        limit,
                        embeddings_api_key,
                        embeddings_model,
                        embeddings_base_url,
                        embeddings_provider,
                        embeddings_endpoint,
                        embeddings_api_version,
                        correlation_id,
                        billing_sink=billing_sink,
                    )

                    # Add result metadata to span
                    span.set_attribute(
                        "retrieval.results_count", result["total_results"]
                    )

                    span.set_attribute("retrieval.status", result["status"])

                    # The failure detail is recorded by retrieval_unavailable
                    # on this same span, rather than travelling back through
                    # the payload the model reads.
                    if result["status"] != STATUS_UNAVAILABLE:
                        # Add similarity scores
                        if result["chunks"]:
                            similarities = [
                                chunk["similarity"] for chunk in result["chunks"]
                            ]
                            span.set_attribute(
                                "retrieval.avg_similarity",
                                round(sum(similarities) / len(similarities), 4),
                            )
                            span.set_attribute(
                                "retrieval.max_similarity", max(similarities)
                            )
                            span.set_attribute(
                                "retrieval.min_similarity", min(similarities)
                            )

                        # Add retrieved documents info
                        filenames = list(
                            set(chunk["filename"] for chunk in result["chunks"])
                        )
                        span.set_attribute(
                            "retrieval.source_files", json.dumps(filenames)
                        )

                        # Add output as JSON for Langfuse
                        output_data = {
                            "query": query,
                            "chunks_retrieved": len(result["chunks"]),
                            "chunks": [
                                {
                                    "text": chunk["text"][:200] + "..."
                                    if len(chunk["text"]) > 200
                                    else chunk["text"],
                                    "filename": chunk["filename"],
                                    "similarity": chunk["similarity"],
                                }
                                for chunk in result["chunks"]
                            ],
                        }
                        span.set_attribute("output", json.dumps(output_data))

                    return result

                except Exception as e:
                    logger.error(f"Error in traced retrieval: {e}")
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        else:
            # No parent context - perform retrieval without tracing
            logger.debug(
                "No parent context available for knowledge base retrieval tracing"
            )
            return await _perform_retrieval(
                query,
                organization_id,
                document_uuids,
                limit,
                embeddings_api_key,
                embeddings_model,
                embeddings_base_url,
                embeddings_provider,
                embeddings_endpoint,
                embeddings_api_version,
                correlation_id,
                billing_sink=billing_sink,
            )
    else:
        # Tracing is disabled - perform retrieval without tracing
        return await _perform_retrieval(
            query,
            organization_id,
            document_uuids,
            limit,
            embeddings_api_key,
            embeddings_model,
            embeddings_base_url,
            embeddings_provider,
            embeddings_endpoint,
            embeddings_api_version,
            correlation_id,
            billing_sink=billing_sink,
        )


async def _perform_retrieval(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]],
    limit: int,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
    embeddings_base_url: Optional[str] = None,
    embeddings_provider: Optional[str] = None,
    embeddings_endpoint: Optional[str] = None,
    embeddings_api_version: Optional[str] = None,
    correlation_id: Optional[str] = None,
    billing_sink: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Internal function to perform the actual retrieval operation.

    Separated from tracing logic for cleaner code organization.
    Handles both chunked (vector search) and full_document (full text) modes.
    """
    try:
        chunks = []

        # Check for full_document mode documents and return their full text
        if document_uuids:
            full_text_docs = await db_client.get_full_text_documents(
                organization_id=organization_id,
                document_uuids=document_uuids,
            )
            for doc in full_text_docs:
                if doc.full_text:
                    chunks.append(
                        {
                            "text": doc.full_text,
                            "filename": doc.filename,
                            "similarity": 1.0,
                            "chunk_index": 0,
                        }
                    )

            # Filter out full_document UUIDs so vector search only hits chunked docs
            full_doc_uuids = {doc.document_uuid for doc in full_text_docs}
            chunked_uuids = [u for u in document_uuids if u not in full_doc_uuids]
        else:
            chunked_uuids = document_uuids

        # Perform vector similarity search on chunked documents
        if chunked_uuids is None or len(chunked_uuids) > 0:
            if not embeddings_api_key:
                raise ValueError(
                    "Embeddings API key not configured. Please set your API key in "
                    "Model Configurations > Embedding."
                )

            # Search runs inside a workflow run: reuse the run's MPS correlation
            # id. The Decibyl-managed path forwards it via request metadata.
            embedding_service = await build_embedding_service(
                db_client=db_client,
                provider=embeddings_provider,
                api_key=embeddings_api_key,
                model=embeddings_model,
                base_url=embeddings_base_url,
                endpoint=embeddings_endpoint,
                api_version=embeddings_api_version,
                correlation_id=correlation_id,
            )

            results = await embedding_service.search_similar_chunks(
                query=query,
                organization_id=organization_id,
                limit=limit,
                document_uuids=chunked_uuids if chunked_uuids else None,
            )

            # The query embedding above is real vendor usage, paid for on
            # whichever key `embeddings_api_key` resolved to. Handed back
            # through the out-parameter rather than the returned dict — see
            # this function's own docstring on `billing_sink` for why it must
            # not become a key the LLM reads. `embeddings_provider` is the
            # configured provider name ("openai", "decibyl", ...), which is
            # what the rate card is keyed on; `search_similar_chunks` has
            # already run by this point, so `last_usage_tokens` reflects the
            # call that just happened, not a stale value from construction.
            if billing_sink is not None:
                billing_sink["provider"] = embeddings_provider or "openai"
                billing_sink["model"] = embedding_service.get_model_id()
                billing_sink["tokens"] = getattr(
                    embedding_service, "last_usage_tokens", None
                )

            for result in results:
                chunk_info = {
                    "text": result.get("contextualized_text")
                    or result.get("chunk_text"),
                    "filename": result.get("filename"),
                    "similarity": round(result.get("similarity", 0), 4),
                    "chunk_index": result.get("chunk_index"),
                }
                chunks.append(chunk_info)

        logger.info(
            f"Knowledge base retrieval: query='{query}', "
            f"results={len(chunks)}, "
            f"document_filter={document_uuids}"
        )

        if not chunks:
            # A real answer: the search worked, and this organization has
            # nothing on the subject. Said explicitly so the model does not
            # have to guess whether the silence means "no" or "broken".
            return {
                "status": STATUS_NO_MATCH,
                "instruction": NO_MATCH_INSTRUCTION,
                "chunks": [],
                "query": query,
                "total_results": 0,
            }

        return {
            "status": STATUS_OK,
            "chunks": chunks,
            "query": query,
            "total_results": len(chunks),
        }

    except Exception as e:
        return retrieval_unavailable(query, e)


def get_knowledge_base_tool(
    document_uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get knowledge base retrieval tool definition for LLM function calling.

    Args:
        document_uuids: Optional list of document UUIDs to include in description

    Returns:
        Tool definition compatible with LLM function calling
    """
    # Build description based on whether specific documents are filtered
    if document_uuids and len(document_uuids) > 0:
        description = (
            "Retrieve relevant information from specific documents in the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the available documents. The search will only look in the "
            f"documents associated with this conversation step ({len(document_uuids)} document(s) available)."
        )
    else:
        description = (
            "Retrieve relevant information from the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the knowledge base documents."
        )

    return {
        "type": "function",
        "function": {
            "name": "retrieve_from_knowledge_base",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query to find relevant information. "
                            "Be specific and use natural language. "
                            "Example: 'What is the refund policy for canceled orders?'"
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }

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
import os
from typing import Any

from loguru import logger
from opentelemetry import trace

from api.db import db_client
from api.services.gen_ai import build_embedding_service
from api.services.knowledge_base import citations
from api.services.pipecat.tracing_config import ensure_tracing

#: The search ran and found something.
STATUS_OK = "ok"
#: The search ran and nothing matched. The knowledge base is working.
STATUS_NO_MATCH = "no_match"
#: The search could not be performed. Says nothing about what we know.
STATUS_UNAVAILABLE = "unavailable"
#: The search ran, and everything it found was too far from the question to
#: answer from. Distinct from :data:`STATUS_NO_MATCH` only for the operator's
#: benefit — the model is told the same thing either way — because "the caller
#: asked about something we nearly have" is worth seeing in a log.
STATUS_WEAK_MATCH = "weak_match"

#: Cosine similarity below which a chunk is not an answer to the question.
#:
#: Vector search always returns the nearest chunks, however far away they are.
#: There is no such thing as "no results" while any document exists, so without
#: a floor the model receives the nearest paragraph to every question and reads
#: it as context worth answering from.
#:
#: Measured rather than picked, on the live knowledge base (a DHL rate guide,
#: 117 chunks, text-embedding-3-small), ten queries:
#:
#:     on topic  ("what is the fuel surcharge?")     0.478 - 0.719
#:     off topic ("what is the capital of Mongolia?") 0.143 - 0.322
#:
#: 0.40 is the middle of that gap, leaving about 0.08 either side. Before it
#: existed, "what is the capital of Mongolia?" returned the rate guide at 0.316
#: with status ok, and the agent would have answered from it.
#:
#: It is one corpus and one embedding model, so this is evidence and not a law
#: — hence the environment variable. Raise it and the agent starts saying it
#: does not know things it does know; lower it and it starts making things up
#: from whatever was nearest. The second failure is the worse one, so this errs
#: high.
#:
#: **The scale is the model's, not the world's.** The end-to-end tests embed
#: with a bag-of-words stand-in, and under it a correct on-topic match scores
#: 0.3264 — below this floor. That is not a broken test, it is a second
#: embedding model disagreeing about what 0.4 means, and it showed up within
#: minutes of the floor landing. Anyone running a model other than
#: text-embedding-3-small should measure their own corpus the same way (a
#: handful of on-topic and off-topic queries, look at the gap) and set
#: KNOWLEDGE_BASE_MIN_SIMILARITY from what they find, rather than inheriting a
#: number calibrated against somebody else's vector space.
WEAK_MATCH_BELOW = float(os.getenv("KNOWLEDGE_BASE_MIN_SIMILARITY") or 0.40)

WEAK_MATCH_INSTRUCTION = (
    "The knowledge base was searched successfully and nothing close enough to "
    "the question was found. Do NOT answer from memory or from anything that "
    "looks related — there is no source for it here. Tell the caller you do "
    "not have that information, and offer to have someone follow up."
)

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


def retrieval_unavailable(query: str, exception: BaseException) -> dict[str, Any]:
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
    document_uuids: list[str] | None = None,
    limit: int = 3,
    embeddings_api_key: str | None = None,
    embeddings_model: str | None = None,
    embeddings_base_url: str | None = None,
    embeddings_provider: str | None = None,
    embeddings_endpoint: str | None = None,
    embeddings_api_version: str | None = None,
    correlation_id: str | None = None,
    tracing_context=None,
    billing_sink: dict[str, Any] | None = None,
    full_documents: bool = True,
) -> dict[str, Any]:
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
                full_documents=full_documents,
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
                        full_documents=full_documents,
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
                full_documents=full_documents,
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
            full_documents=full_documents,
        )


async def _perform_retrieval(
    query: str,
    organization_id: int,
    document_uuids: list[str] | None,
    limit: int,
    embeddings_api_key: str | None = None,
    embeddings_model: str | None = None,
    embeddings_base_url: str | None = None,
    embeddings_provider: str | None = None,
    embeddings_endpoint: str | None = None,
    embeddings_api_version: str | None = None,
    correlation_id: str | None = None,
    billing_sink: dict[str, Any] | None = None,
    full_documents: bool = True,
) -> dict[str, Any]:
    """Internal function to perform the actual retrieval operation.

    Separated from tracing logic for cleaner code organization.
    Handles both chunked (vector search) and full_document (full text) modes.

    Passages are found two ways and merged: by meaning (the vector search,
    when the account has embeddings) and by their words (always). Words
    catch what a vector misses -- a SKU, an invoice number, a value under
    its column header -- and are the only way into a file read without
    embeddings. Every passage says where it came from (``citation``): the
    file's name and folder as they are now, and the page or the sheet and
    rows.

    ``full_documents=False`` treats a full-document file like any other:
    its passages, cited, rather than its whole text (Decibyl, which reads
    many files for one question).
    """
    try:
        chunks = []
        paths = await _folder_paths(organization_id)

        # Check for full_document mode documents and return their full text
        if document_uuids and full_documents:
            full_text_docs = await db_client.get_full_text_documents(
                organization_id=organization_id,
                document_uuids=document_uuids,
            )
            for doc in full_text_docs:
                if doc.full_text:
                    folder = _folder_of(doc, paths)
                    chunks.append(
                        {
                            "text": doc.full_text,
                            "similarity": 1.0,
                            "chunk_index": 0,
                            **citations.fields(
                                filename=doc.filename,
                                folder_path=folder,
                                document_uuid=doc.document_uuid,
                                chunk_metadata=None,
                            ),
                        }
                    )

            # Filter out full_document UUIDs so vector search only hits chunked docs
            full_doc_uuids = {doc.document_uuid for doc in full_text_docs}
            chunked_uuids = [u for u in document_uuids if u not in full_doc_uuids]
        else:
            chunked_uuids = document_uuids

        # Perform vector similarity search on chunked documents
        if chunked_uuids is None or len(chunked_uuids) > 0:
            terms = _search_terms(query)
            words = await _word_hits(
                organization_id, terms, limit, chunked_uuids if chunked_uuids else None
            )
            results: list[dict[str, Any]] = []
            if embeddings_api_key:
                # Search runs inside a workflow run: reuse the run's MPS
                # correlation id. The Decibyl-managed path forwards it via
                # request metadata.
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
                # through the out-parameter rather than the returned dict --
                # see this function's own docstring on `billing_sink` for why
                # it must not become a key the LLM reads. `embeddings_provider`
                # is the configured provider name ("openai", "decibyl", ...),
                # which is what the rate card is keyed on;
                # `search_similar_chunks` has already run by this point, so
                # `last_usage_tokens` reflects the call that just happened.
                if billing_sink is not None:
                    billing_sink["provider"] = embeddings_provider or "openai"
                    billing_sink["model"] = embedding_service.get_model_id()
                    billing_sink["tokens"] = getattr(
                        embedding_service, "last_usage_tokens", None
                    )
            elif not _usable(words, terms, by_meaning=False):
                # No embeddings and no passage holding the question's words:
                # a search that could not look properly is not a "no".
                raise ValueError(
                    "Embeddings API key not configured. Please set your API key in "
                    "Model Configurations > Embedding."
                )

            meaning = [
                _passage(result, paths, round(float(result.get("similarity") or 0), 4))
                for result in results
            ]

            # Drop what is too far away to be an answer. Full-document matches
            # carry similarity 1.0 and are unaffected; this is only about
            # vector search, which returns the nearest chunk whether or not
            # anything near exists.
            near_enough = [
                chunk
                for chunk in meaning
                if float(chunk.get("similarity") or 0) >= WEAK_MATCH_BELOW
            ]
            merged = _merge(
                near_enough,
                _usable(words, terms, by_meaning=bool(embeddings_api_key)),
                terms,
                paths,
                limit,
            )
            # Only "weak match" when nothing usable was found: full-document
            # text already in ``chunks`` (a bot's own index) is an answer too.
            if meaning and not near_enough and not merged and not chunks:
                best = max(float(c.get("similarity") or 0) for c in meaning)
                logger.info(
                    "Knowledge base weak match: query='{}', best={:.4f} < {:.2f}",
                    query,
                    best,
                    WEAK_MATCH_BELOW,
                )
                return {
                    "status": STATUS_WEAK_MATCH,
                    "instruction": WEAK_MATCH_INSTRUCTION,
                    "chunks": [],
                    "query": query,
                    "total_results": 0,
                    # For the operator reading a run, not for the model: it
                    # says how close the near miss was, which is the number
                    # that tells you whether the floor is set right.
                    "best_similarity": round(best, 4),
                }
            chunks.extend(merged)

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


#: Words too common to say what a question is about.
_STOPWORD_TEXT = (
    "a an and are as at be by can do does for from has have how i in is it "
    "its me my no not of on or our please tell than that the their them "
    "then there this to us was we what when where which who why will with "
    "you your about any much many"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _search_terms(query: str) -> list[str]:
    """The words of a question worth searching for, in order, once each."""
    import re

    out: list[str] = []
    for word in re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", (query or "").lower()):
        for part in re.split(r"[-']", word):
            worth = (len(part) > 1 or part.isdigit()) and part not in _STOPWORDS
            if worth and part not in out:
                out.append(part)
    return out[:12]


async def _word_hits(
    organization_id: int,
    terms: list[str],
    limit: int,
    document_uuids: list[str] | None,
) -> list[dict[str, Any]]:
    """Passages holding the question's words. Never raises: a word search
    that fails leaves the meaning search to answer alone."""
    if not terms:
        return []
    try:
        return await db_client.keyword_search_chunks(
            organization_id=organization_id,
            terms=terms,
            limit=max(limit, 3) * 2,
            document_uuids=document_uuids,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Word search over Files failed: {}", exc)
        return []


async def _folder_paths(organization_id: int) -> dict[int, str]:
    """Folder paths as they are now, for citations. Empty when unreadable:
    a citation without its folder still names the file."""
    try:
        from api.services.knowledge_base import folders

        return await folders.folder_paths(organization_id)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Could not read file folders for citations: {}", exc)
        return {}


def _folder_of(row: Any, paths: dict[int, str]) -> str:
    folder_id = (
        row.get("file_folder_id") if isinstance(row, dict) else row.file_folder_id
    )
    return paths.get(folder_id, "") if folder_id is not None else ""


def _passage(
    row: dict[str, Any], paths: dict[int, str], similarity: float
) -> dict[str, Any]:
    metadata = row.get("chunk_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    return {
        "text": row.get("contextualized_text") or row.get("chunk_text"),
        "similarity": similarity,
        "chunk_index": row.get("chunk_index"),
        "_chunk_id": row.get("id"),
        **citations.fields(
            filename=row.get("filename") or "",
            folder_path=_folder_of(row, paths),
            document_uuid=row.get("document_uuid"),
            chunk_metadata=metadata,
        ),
    }


def _usable(
    words: list[dict[str, Any]], terms: list[str], *, by_meaning: bool
) -> list[dict[str, Any]]:
    """The word hits worth handing over.

    Beside a search by meaning, a passage sharing a single word with a
    longer question is noise next to better matches, so it needs two. With
    words alone -- no embeddings on this account -- one of the question's
    words is the evidence there is, and the passages holding the most of
    them still come first.
    """
    need = 2 if by_meaning and len(terms) > 1 else 1
    return [row for row in words if int(row.get("matched") or 0) >= need]


def _merge(
    meaning: list[dict[str, Any]],
    words: list[dict[str, Any]],
    terms: list[str],
    paths: dict[int, str],
    limit: int,
) -> list[dict[str, Any]]:
    """Meaning and word hits as one list, best first, each passage once.

    A passage holding every word of the question leads (it is about exactly
    that); then the meaning matches; then the other word hits, which
    :func:`_usable` has already thinned.
    """
    strong: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for row in words:
        matched = int(row.get("matched") or 0)
        # Shown on the passage like a vector score, so a reader can compare:
        # the share of the question's words it holds.
        passage = _passage(row, paths, round(matched / max(len(terms), 1), 4))
        (strong if matched >= len(terms) and len(terms) > 1 else partial).append(
            passage
        )
    out: list[dict[str, Any]] = []
    seen: set = set()
    for passage in [*strong, *meaning, *partial]:
        # The same passage found both ways is one passage. Without its row id
        # (never the case for a stored chunk) nothing is assumed to repeat.
        if passage.get("_chunk_id") is not None:
            key: Any = ("chunk", passage["_chunk_id"])
        elif passage.get("document_uuid"):
            key = (passage["document_uuid"], passage.get("chunk_index"))
        else:
            key = ("object", id(passage))
        if key in seen:
            continue
        seen.add(key)
        out.append(passage)
    for passage in out:
        passage.pop("_chunk_id", None)
    return out[: max(limit, 1)]


def get_knowledge_base_tool(
    document_uuids: list[str] | None = None,
) -> dict[str, Any]:
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
    # Every passage comes back with where it came from; an answer that says
    # so can be checked, and one that does not cannot.
    description += (
        " Each passage carries a citation (the file, its folder, and the page "
        "or sheet); when you answer from one, say which file it came from."
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

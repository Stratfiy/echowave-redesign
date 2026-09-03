"""ARQ background task for processing knowledge base documents.

Download from S3, convert and chunk, embed, store. Conversion and chunking run
in this process by default (``api/services/knowledge_base/``); the Model Proxy
Service remains selectable through ``KB_DOCUMENT_PROCESSOR`` for a deployment
that runs it. Everything else in this path — embeddings, the pgvector write,
the per-organization scoping — has always been local and is unchanged.

Two rules this task is responsible for holding:

* **A document that produced nothing is ``failed``, never ``completed``.** The
  worst outcome available here is a document listed as ready that the agent
  cannot answer a single question from.
* **What is stored in ``processing_error`` is written for the customer.** It is
  rendered verbatim on the files screen, so a raw ``ConnectError`` there is
  both useless to them and a leak of our internals.
"""

import os
import tempfile

from loguru import logger

from api.constants import KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES
from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel
from api.services.gen_ai import build_embedding_service
from api.services.knowledge_base import (
    EmptyDocumentError,
    KnowledgeBaseError,
    process_document,
    user_facing_message,
)
from api.services.storage import storage_fs

#: The deployment ceiling, used when an account has no plan figure of its own.
#: A plan's own limit is resolved per document — see :func:`_max_file_bytes`.
MAX_FILE_SIZE_BYTES = KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES
EMBEDDING_BATCH_SIZE = 64


async def _max_file_bytes(organization_id: int) -> int:
    """The largest document this account's plan accepts.

    Resolved here as well as at the upload gates, and it has to be: a presigned
    PUT cannot carry a size limit, so nothing between the browser and this
    worker has actually enforced the number the upload endpoint claimed. This
    is the only check an object in the store has passed.

    Falls back to the deployment ceiling if the plan cannot be read. A worker
    that refuses every document because the billing tables were briefly
    unreachable is worse than one that briefly applies the platform default.
    """
    from api.db import db_client as _db
    from api.services.billing import subscription_plans

    try:
        async with _db.async_session() as session:
            allowance = await subscription_plans.knowledge_base_allowance_for(
                session, organization_id=organization_id
            )
    except Exception as exc:  # noqa: BLE001 — a limit lookup is not a verdict
        logger.warning(
            f"Could not resolve the plan file limit for {organization_id}: {exc}"
        )
        return MAX_FILE_SIZE_BYTES
    return allowance.max_file_bytes or MAX_FILE_SIZE_BYTES


def _too_large_message(size_bytes: int, limit_bytes: int) -> str:
    return (
        f"File size ({size_bytes / (1024 * 1024):.1f}MB) exceeds the "
        f"maximum allowed size of {limit_bytes // (1024 * 1024)}MB."
    )


async def _oversized_before_download(s3_key: str, limit_bytes: int) -> int | None:
    """The object's size, if storage already says it is too big to accept.

    A presigned PUT cannot carry a size limit — SigV4 signs a URL, not a
    request body — so the ceiling the upload endpoint claims is not something
    the object store enforces. Anything holding an upload URL can push an
    arbitrarily large object, and the worker used to find out by downloading
    all of it to a container with a fixed disk allowance and measuring the file
    afterwards. One HEAD first turns a filled disk into a failed document.

    ``None`` means proceed: either the object is within the limit, or storage
    would not say, in which case the post-download check still catches it.
    """
    try:
        metadata = await storage_fs.aget_file_metadata(s3_key)
    except Exception as exc:  # noqa: BLE001 — a HEAD that fails is not a verdict
        logger.warning(
            f"Could not check the size of {s3_key} before downloading: {exc}"
        )
        return None

    size = (metadata or {}).get("size")
    if isinstance(size, int) and size > limit_bytes:
        return size
    return None


async def _embed_texts_in_batches(
    embedding_service,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> tuple[list[list[float]], int]:
    """Generate embeddings in bounded batches for provider/MPS stability.

    Returns the embeddings alongside the total vendor-reported token usage
    across every batch call. ``embedding_service.last_usage_tokens`` is
    overwritten on each call (see ``BaseEmbeddingService``'s docstring), so it
    has to be accumulated here rather than read once after the loop.
    """
    embeddings: list[list[float]] = []
    total_tokens = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        logger.info(
            f"Generating embedding batch {start // batch_size + 1} ({len(batch)} texts)"
        )
        embeddings.extend(await embedding_service.embed_texts(batch))
        total_tokens += getattr(embedding_service, "last_usage_tokens", None) or 0
    return embeddings, total_tokens


async def _can_afford_ingestion(
    *, organization_id: int, document_id: int, provider: str, model: str, tokens: int
) -> bool:
    """Whether this account can be charged for embedding ~``tokens`` tokens.

    Fails open (returns True) on a lookup error, the same "an unreachable
    billing table is not a verdict" reasoning ``_max_file_bytes`` above uses:
    an account is better served by an ingestion that runs and is later found
    unbilled than by every upload failing because billing was briefly down.
    """
    from api.services.billing import embedding_ingestion as billing

    try:
        async with db_client.async_session() as session:
            estimate = await billing.estimate_ingestion_cost_paise(
                session, provider=provider, model=model, tokens=tokens
            )
            return await billing.has_balance_for_estimate(
                session, organization_id=organization_id, estimate=estimate
            )
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(
            f"Could not check balance before embedding document {document_id}: {exc}"
        )
        return True


async def _debit_ingestion(
    *, organization_id: int, document_id: int, provider: str, model: str, tokens: int
) -> None:
    """Debit the ledger for what embedding this document's chunks cost.

    Called only after the vendor call has already happened, so a failure here
    is logged at error level rather than raised: the money is already spent
    either way, and failing the document on top of that would cost the
    customer their upload for a problem that is entirely ours.
    """
    from api.services.billing import embedding_ingestion as billing

    try:
        async with db_client.async_session() as session:
            await billing.debit_ingestion_cost(
                session,
                organization_id=organization_id,
                document_id=document_id,
                provider=provider,
                model=model,
                tokens=tokens,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.error(
            f"Could not debit ledger for document {document_id}'s ingestion "
            f"embeddings ({tokens} tokens on {provider}/{model}): {exc}"
        )


async def process_knowledge_base_document(
    ctx,
    document_id: int,
    s3_key: str,
    organization_id: int,
    created_by_provider_id: str,
    max_tokens: int = 128,
    retrieval_mode: str = "chunked",
):
    """Download, convert, chunk, embed and store one uploaded document.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        created_by_provider_id: Uploading user's provider ID (for OSS-mode auth to MPS)
        max_tokens: Maximum number of tokens per chunk (default: 128)
        retrieval_mode: "chunked" for vector search or "full_document" for full text
    """
    logger.info(
        f"Processing knowledge base document: document_id={document_id}, "
        f"s3_key={s3_key}, org={organization_id}, mode={retrieval_mode}"
    )

    temp_file_path = None

    try:
        await db_client.update_document_status(document_id, "processing")

        filename = s3_key.split("/")[-1]
        file_extension = os.path.splitext(filename)[1] or ".bin"

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file_path = temp_file.name
        temp_file.close()

        limit_bytes = await _max_file_bytes(organization_id)
        oversized = await _oversized_before_download(s3_key, limit_bytes)
        if oversized is not None:
            error_message = _too_large_message(oversized, limit_bytes)
            logger.warning(f"Document {document_id}: {error_message} (not downloaded)")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        logger.info(f"Downloading file from S3: {s3_key}")
        download_success = await storage_fs.adownload_file(s3_key, temp_file_path)
        if not download_success:
            raise Exception(f"Failed to download file from S3: {s3_key}")
        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(f"Downloaded file not found: {temp_file_path}")

        file_size = os.path.getsize(temp_file_path)
        logger.info(f"Downloaded file size: {file_size} bytes")

        if file_size > limit_bytes:
            # Reached when storage would not report a size before the download.
            error_message = _too_large_message(file_size, limit_bytes)
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        file_hash = db_client.compute_file_hash(temp_file_path)
        mime_type = db_client.get_mime_type(temp_file_path)

        document = await db_client.get_document_by_id(document_id)
        if not document:
            raise Exception(f"Document {document_id} not found")

        # Reject duplicates (same hash already ingested for this org).
        existing_doc = await db_client.get_document_by_hash(file_hash, organization_id)
        if existing_doc and existing_doc.id != document_id:
            error_message = (
                f"This file is a duplicate of '{existing_doc.filename}'. "
                f"Please delete the duplicate files and consolidate them into a "
                f"single unique file before uploading."
            )
            logger.warning(
                f"Duplicate document detected: {document_id} is duplicate of "
                f"{existing_doc.id} ({existing_doc.filename})"
            )
            await db_client.update_document_metadata(
                document_id,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
            )
            await db_client.update_document_status(
                document_id,
                "failed",
                error_message=error_message,
                docling_metadata={
                    "duplicate_of": existing_doc.document_uuid,
                    "duplicate_filename": existing_doc.filename,
                },
            )
            return

        await db_client.update_document_metadata(
            document_id,
            file_size_bytes=file_size,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        embeddings_provider = None
        embeddings_api_key = None
        embeddings_model = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None
        # "managed" unless the resolved config says this org brought its own
        # embeddings key -- same fallback run_pipeline.py uses for the
        # query-time path, and the same reasoning: an absent key_source on an
        # old/partial config is not license to skip billing usage nobody
        # confirmed was BYOK.
        embeddings_key_source = "managed"
        if retrieval_mode == "chunked":
            from api.services.configuration.ai_model_configuration import (
                apply_managed_embeddings_base_url,
                get_effective_ai_model_configuration_for_workflow,
            )

            # Resolved, for the reason spelled out in routes/knowledge_base.py:
            # a managed account's embeddings section carries no key until
            # managed resolution substitutes the platform one. Ingesting with
            # the stored shape failed every managed account's upload.
            effective_config = await get_effective_ai_model_configuration_for_workflow(
                organization_id=document.organization_id,
                workflow_configurations={},
            )
            if effective_config.embeddings:
                embeddings_provider = getattr(
                    effective_config.embeddings, "provider", None
                )
                embeddings_api_key = effective_config.embeddings.api_key
                embeddings_model = effective_config.embeddings.model
                embeddings_base_url = apply_managed_embeddings_base_url(
                    provider=embeddings_provider,
                    base_url=getattr(effective_config.embeddings, "base_url", None),
                )
                embeddings_endpoint = getattr(
                    effective_config.embeddings, "endpoint", None
                )
                embeddings_api_version = getattr(
                    effective_config.embeddings, "api_version", None
                )
                embeddings_key_source = (
                    getattr(effective_config.embeddings, "key_source", None)
                    or "managed"
                )
                logger.info(
                    f"Using user embeddings config: provider={embeddings_provider}, "
                    f"model={embeddings_model}"
                )

        logger.info(f"Converting and chunking document (mode={retrieval_mode})")
        processed = await process_document(
            file_path=temp_file_path,
            filename=filename,
            content_type=mime_type or "application/octet-stream",
            retrieval_mode=retrieval_mode,
            max_tokens=max_tokens,
            organization_id=organization_id,
            created_by=created_by_provider_id,
        )

        docling_metadata = processed.get("docling_metadata", {})

        if retrieval_mode == "full_document":
            full_text = processed.get("full_text") or ""
            if not full_text.strip():
                # Same rule as the chunked path: an empty document that reads
                # as 'completed' is the failure the customer never finds out
                # about until an agent cannot answer from it.
                raise EmptyDocumentError(
                    f"Document {document_id} produced no text",
                    user_message=(
                        "This file contains no text we can read, so there "
                        "would be nothing for the agent to answer from."
                    ),
                )
            await db_client.update_document_full_text(document_id, full_text)
            await db_client.update_document_status(
                document_id,
                "completed",
                total_chunks=0,
                docling_metadata=docling_metadata,
            )
            logger.info(
                f"Successfully processed full_document {document_id}. "
                f"Text length: {len(full_text)} chars"
            )
            return

        if not embeddings_api_key:
            error_message = (
                "API key not configured. Please set your API key in "
                "Model Configurations > Embedding to process documents."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        # Ingestion runs outside any workflow run, so resolve the MPS correlation
        # id here.
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            resolve_correlation=True,
        )

        # Zero chunks cannot reach here: process_document raises
        # EmptyDocumentError rather than returning an empty list, precisely so
        # this path cannot mark an unanswerable document 'completed'. The
        # assertion is a tripwire for a future backend that forgets.
        source_chunks = processed.get("chunks", [])
        if not source_chunks:
            raise EmptyDocumentError(
                f"Document {document_id} produced no chunks",
                user_message=(
                    "We read this document but could not split it into "
                    "anything the agent can quote. If it is a scan or mostly "
                    "images, run it through OCR and upload it again."
                ),
            )

        chunk_records = []
        chunk_texts = []
        for chunk in source_chunks:
            contextualized = chunk.get("contextualized_text") or chunk["chunk_text"]
            chunk_records.append(
                KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk["chunk_text"],
                    contextualized_text=contextualized,
                    chunk_index=chunk["chunk_index"],
                    chunk_metadata=chunk.get("chunk_metadata") or {},
                    embedding_model=embedding_service.get_model_id(),
                    embedding_dimension=embedding_service.get_embedding_dimension(),
                    token_count=chunk.get("token_count", 0),
                )
            )
            chunk_texts.append(contextualized)

        # Checked before the vendor is ever called -- the money-losing
        # direction is paying for embeddings and then finding out the account
        # cannot be charged for them. The estimate uses each chunk's own
        # token_count (from chunking, not the vendor), because the real
        # figure isn't known until after the call this check exists to gate.
        if embeddings_key_source == "managed":
            estimated_tokens = sum(
                chunk.get("token_count", 0) for chunk in source_chunks
            )
            if not await _can_afford_ingestion(
                organization_id=organization_id,
                document_id=document_id,
                provider=embeddings_provider or "openai",
                model=embedding_service.get_model_id(),
                tokens=estimated_tokens,
            ):
                error_message = (
                    "Your account balance is too low to process this document. "
                    "Add funds and try again."
                )
                logger.warning(f"Document {document_id}: {error_message}")
                await db_client.update_document_status(
                    document_id, "failed", error_message=error_message
                )
                return

        logger.info(
            f"Generating embeddings for {len(chunk_texts)} chunks "
            f"using {embedding_service.get_model_id()}"
        )
        embeddings, embedding_tokens = await _embed_texts_in_batches(
            embedding_service, chunk_texts
        )
        if len(embeddings) != len(chunk_records):
            raise ValueError(
                "Embedding count mismatch: "
                f"expected {len(chunk_records)}, got {len(embeddings)}"
            )
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding

        # The vendor has already been paid for embedding_tokens by this
        # point, regardless of what happens next -- so this debits for real
        # rather than for an estimate, and a failure here is logged loudly
        # rather than failing the document: the customer's upload succeeded
        # and they should not lose it over a billing-write error on money
        # already spent.
        if embeddings_key_source == "managed" and embedding_tokens > 0:
            await _debit_ingestion(
                organization_id=organization_id,
                document_id=document_id,
                provider=embeddings_provider or "openai",
                model=embedding_service.get_model_id(),
                tokens=embedding_tokens,
            )

        logger.info("Storing chunks in database")
        await db_client.replace_chunks_for_document(
            document_id=document_id,
            organization_id=organization_id,
            chunks=chunk_records,
        )

        await db_client.update_document_status(
            document_id,
            "completed",
            total_chunks=len(chunk_records),
            docling_metadata=docling_metadata,
        )

        logger.info(
            f"Successfully processed knowledge base document {document_id}. "
            f"Total chunks: {len(chunk_records)}"
        )

    except Exception as e:
        # Two audiences, two texts. The log gets the exception; the document
        # row gets a sentence written for whoever uploaded the file, because
        # that is what the files screen renders. str(e) on an httpx failure
        # reads "[Errno -2] Name or service not known" and names our host.
        logger.exception(
            "Error processing knowledge base document {}: {}", document_id, e
        )
        await db_client.update_document_status(
            document_id, "failed", error_message=user_facing_message(e)
        )

        # A file the customer must fix — wrong format, no text layer, password
        # protected — is not a job to retry. Re-raising would burn ARQ's retry
        # budget re-reading the same PDF to the same conclusion, and the
        # document is already marked failed with the reason.
        if isinstance(e, KnowledgeBaseError):
            return
        raise

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")

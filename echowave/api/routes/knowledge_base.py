"""API routes for knowledge base operations."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.enums import KnowledgeScope, PostHogEvent
from api.schemas.knowledge_base import (
    ChangesResponseSchema,
    ChunkSearchRequestSchema,
    ChunkSearchResponseSchema,
    DocumentListResponseSchema,
    DocumentResponseSchema,
    DocumentUpdateSchema,
    DocumentUploadRequestSchema,
    DocumentUploadResponseSchema,
    FileFolderCreateSchema,
    FileFolderDeleteResponseSchema,
    FileFolderEnsureSchema,
    FileFolderListResponseSchema,
    FileFolderSchema,
    FileFolderUpdateSchema,
    ProcessDocumentRequestSchema,
    SyncFileSchema,
    SyncFolderSchema,
)
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.billing import subscription_plans
from api.services.knowledge_base import (
    folders,
    staleness,
    sync,
    translate_document,
    upload_keys,
    versions,
)
from api.services.posthog_client import capture_event
from api.services.storage import storage_fs
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _document_response(document, *, needs_reingest: bool = False, **overrides):
    """One document row as the API shows it. The one place the row is
    turned into the schema, so a new column cannot reach one endpoint and
    not another. ``overrides`` state a value instead of reading it from the
    row (a copy that has only just been queued, say)."""
    readers = {
        "id": lambda d: d.id,
        "document_uuid": lambda d: d.document_uuid,
        "filename": lambda d: d.filename,
        "file_size_bytes": lambda d: d.file_size_bytes or 0,
        "file_hash": lambda d: d.file_hash or "",
        "mime_type": lambda d: d.mime_type or "application/octet-stream",
        "processing_status": lambda d: d.processing_status,
        "processing_error": lambda d: d.processing_error,
        "total_chunks": lambda d: d.total_chunks or 0,
        "retrieval_mode": lambda d: d.retrieval_mode or "chunked",
        "custom_metadata": lambda d: d.custom_metadata or {},
        "docling_metadata": lambda d: d.docling_metadata or {},
        "source_url": lambda d: d.source_url,
        "scope": lambda d: d.scope,
        "folder_id": lambda d: d.folder_id,
        "workflow_id": lambda d: d.workflow_id,
        "file_folder_id": lambda d: d.file_folder_id,
        "created_at": lambda d: d.created_at,
        "updated_at": lambda d: d.updated_at,
        "organization_id": lambda d: d.organization_id,
        "created_by": lambda d: d.created_by,
        "is_active": lambda d: d.is_active,
    }
    values = {
        name: overrides[name] if name in overrides else read(document)
        for name, read in readers.items()
    }
    return DocumentResponseSchema(
        needs_reingest=needs_reingest, **values, **versions.describe(values)
    )


def _folder_error(exc: folders.FolderError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _mb(value: int) -> int:
    """Bytes as whole megabytes, for a message a person can act on."""
    return value // (1024 * 1024)


async def _allowance(organization_id: int):
    """What this account's plan buys it: a total, and a per-file ceiling."""
    async with db_client.async_session() as session:
        return await subscription_plans.knowledge_base_allowance_for(
            session, organization_id=organization_id
        )


async def _assert_room_to_ingest(organization_id: int):
    """Refuse an upload the account has no plan for, or no room left in.

    One function rather than the check written out at each gate, because there
    are three of them — the browser is refused before it starts sending, the
    presigned URL is minted against the same ceiling, and the call that queues
    the embedding job checks again — and they have to agree. They agree by
    being the same code.

    The two refusals are deliberately different sentences. "Full" is something a
    customer fixes by deleting a document; "not on your plan" is something they
    fix by subscribing, and telling them to delete their way out of it would be
    advice that cannot work.

    Returns the allowance so the caller can mint a URL against the per-file
    limit without resolving the plan a second time.
    """
    allowance = await _allowance(organization_id)
    if not allowance.includes_a_knowledge_base:
        raise HTTPException(
            status_code=402,
            detail=(
                "The knowledge base is part of a subscription. Choose a plan to "
                "upload documents your agents can answer from."
            ),
        )
    used = await db_client.get_knowledge_base_bytes_used(organization_id)
    if used >= allowance.total_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                "Your knowledge base is full "
                f"({_mb(used)} of {_mb(allowance.total_bytes)} MB used). "
                "Delete a document to make room, or choose a plan for a larger one."
            ),
        )
    return allowance


@router.get(
    "/allowance",
    summary="What an upload will cost before it runs",
)
async def get_allowance(
    pages: Annotated[int, Query(ge=0)] = 0,
    scanned_pages: Annotated[int, Query(ge=0)] = 0,
    user=Depends(get_user),
):
    """The plan's page cap, what is held, and what an upload of ``pages``
    (``scanned_pages`` of them scanned) would cost in credits (KAN-57).

    Pages past the cap are a tenth of a credit typed and two credits scanned;
    inside the cap nothing is charged. The browser does not always know a
    file's page count before it uploads, so with ``pages`` omitted this is
    the rate and the room left, which is still the cost stated before it
    runs. The bytes ceiling is reported beside it: that one refuses, this one
    prices.
    """
    from api.services.billing import knowledge_pages

    organization_id = user.selected_organization_id
    allowance = await _allowance(organization_id)
    async with db_client.async_session() as session:
        quote = await knowledge_pages.quote(
            session,
            organization_id=organization_id,
            pages=knowledge_pages.Pages(
                total=max(pages, scanned_pages), scanned=scanned_pages
            ),
        )
    bytes_used = await db_client.get_knowledge_base_bytes_used(organization_id)
    return {
        **quote.as_dict(),
        "bytes_used": bytes_used,
        "bytes_cap": allowance.total_bytes,
        "max_file_bytes": allowance.max_file_bytes,
    }


@router.post(
    "/upload-url",
    response_model=DocumentUploadResponseSchema,
    summary="Get presigned URL for document upload",
)
async def get_upload_url(
    request: DocumentUploadRequestSchema,
    user=Depends(get_user),
):
    """Generate a presigned PUT URL for uploading a document.

    This endpoint:
    1. Generates a unique document UUID for organizing the S3 key
    2. Generates a presigned S3/MinIO URL for uploading the file
    3. Returns the upload URL and document metadata

    After uploading to the returned URL, call /process-document to create
    the document record and trigger processing.

    Access Control:
    * All authenticated users can upload documents scoped to their organization.
    """

    # Checked before the URL is minted, not after the upload. A presigned PUT
    # cannot carry a size limit, so the only honest place to refuse is before
    # the browser starts sending — the alternative is a progress bar reaching
    # 100% and then being told there was no room.
    allowance = await _assert_room_to_ingest(user.selected_organization_id)

    try:
        # Generate unique document UUID for S3 organization
        document_uuid = str(uuid.uuid4())

        # Generate S3 key: knowledge_base/{org_id}/{document_uuid}/{filename}.
        # Built by the same function /process-document validates against, so
        # the two cannot disagree about what a legitimate key looks like.
        s3_key = upload_keys.build_document_key(
            user.selected_organization_id, document_uuid, request.filename
        )

        # Generate presigned PUT URL (valid for 30 minutes).
        #
        # max_size is advisory — a presigned PUT cannot carry a size limit, see
        # BaseFileSystem.aget_presigned_put_url — so it is stated here to match
        # what the worker will actually accept rather than the 100MB it used to
        # claim while ingestion refused anything over five. It is the *plan's*
        # ceiling, which is the one the worker enforces too.
        upload_url = await storage_fs.aget_presigned_put_url(
            file_path=s3_key,
            expiration=1800,  # 30 minutes
            content_type=request.mime_type,
            max_size=allowance.max_file_bytes,
        )

        if not upload_url:
            raise HTTPException(
                status_code=500, detail="Failed to generate presigned upload URL"
            )

        logger.info(
            f"Generated upload URL for document {document_uuid}, "
            f"user {user.id}, org {user.selected_organization_id}"
        )

        return DocumentUploadResponseSchema(
            upload_url=upload_url,
            document_uuid=document_uuid,
            s3_key=s3_key,
        )

    except Exception as exc:
        logger.error(f"Error generating upload URL: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to generate upload URL"
        ) from exc


async def _checked_scope(
    organization_id: int, request
) -> tuple[str, int | None, int | None]:
    """The scope as it will be stored, or a 4xx that says what was wrong."""
    scope = (request.scope or KnowledgeScope.LIBRARY.value).strip().lower()
    if scope not in {k.value for k in KnowledgeScope}:
        raise HTTPException(status_code=422, detail=f"Unknown scope {scope!r}")
    if scope == KnowledgeScope.CHANNEL.value:
        if request.folder_id is None:
            raise HTTPException(
                status_code=422, detail="A channel document needs folder_id"
            )
        folder = await db_client.get_folder(
            request.folder_id, organization_id=organization_id
        )
        if folder is None:
            raise HTTPException(status_code=404, detail="No such channel")
        return scope, request.folder_id, None
    if scope == KnowledgeScope.BOT.value:
        if request.workflow_id is None:
            raise HTTPException(
                status_code=422, detail="An agent document needs workflow_id"
            )
        workflow = await db_client.get_workflow(
            request.workflow_id, organization_id=organization_id
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="No such agent")
        return scope, None, request.workflow_id
    return scope, None, None


@router.post(
    "/process-document",
    response_model=DocumentResponseSchema,
    summary="Trigger document processing",
)
async def process_document(
    request: ProcessDocumentRequestSchema,
    user=Depends(get_user),
):
    """Trigger asynchronous processing of an uploaded document.

    This endpoint should be called after successfully uploading a file to the presigned URL.
    It will:
    1. Create a document record in the database with the specified UUID
    2. Enqueue a background task to process the document (chunking and embedding)

    The document status will be updated from 'pending' -> 'processing' -> 'completed' or 'failed'.

    Embedding:
    Uses OpenAI text-embedding-3-small (1536-dimensional embeddings, requires API key configured in Model Configurations).

    Access Control:
    * Users can only process documents in their organization.
    """

    # The key arrives from the client, and whatever is at it gets ingested into
    # this organization's knowledge base — where this organization's agent will
    # read it aloud. Another organization's key must not be accepted here, so
    # the key has to be exactly the one /upload-url would have minted for this
    # caller and this document.
    #
    # Ahead of the entitlement check, and the order is load-bearing: a caller
    # reaching for another tenant's object must be told it is not theirs, not
    # that their plan is too small for it. Billing first would turn a
    # tenant-isolation refusal into a pricing message, and would answer a
    # question about someone else's data with a fact about this account.
    if not upload_keys.key_belongs_to(
        user.selected_organization_id, request.document_uuid, request.s3_key
    ):
        logger.warning(
            "Rejected a process-document request for a key outside "
            "organization {}: {!r} (document {!r}, user {})",
            user.selected_organization_id,
            request.s3_key,
            request.document_uuid,
            user.id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "This upload key does not belong to your organization. "
                "Request a fresh upload URL and try again."
            ),
        )

    # Checked again here, not only when the URL was minted. This is the call
    # that creates the record and queues the embedding job, so it is the gate
    # that actually costs money — and it can be reached directly, or with a
    # presigned URL issued before the account filled up or its plan lapsed.
    await _assert_room_to_ingest(user.selected_organization_id)

    # The scope is checked against this organisation before anything is
    # stored: a folder or workflow id from another tenant would otherwise make
    # a document readable by that tenant's bots.
    scope, folder_id, workflow_id = await _checked_scope(
        user.selected_organization_id, request
    )
    # Where it sits on the Files page, checked against this organisation like
    # the scope above: a folder id from another workspace is "no such folder".
    try:
        file_folder_id = await folders.resolve(
            user.selected_organization_id, request.file_folder_id
        )
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc

    try:
        # Extract filename from s3_key
        filename = request.s3_key.split("/")[-1]

        # The same name in the same place is the same file, uploaded again:
        # its next version, read afresh, rather than a second copy.
        previous = await versions.previous_upload(
            user.selected_organization_id,
            filename=filename,
            scope=scope,
            folder_id=folder_id,
            workflow_id=workflow_id,
            file_folder_id=file_folder_id,
            document_uuid=request.document_uuid,
        )
        if previous is not None:
            document = await versions.begin(
                previous,
                organization_id=user.selected_organization_id,
                s3_key=request.s3_key,
                user_id=user.id,
                retrieval_mode=request.retrieval_mode,
            )
        else:
            # Create document record with the specific UUID from upload
            document = await db_client.create_document(
                organization_id=user.selected_organization_id,
                created_by=user.id,
                filename=filename,
                file_size_bytes=0,  # Will be updated by background task
                file_hash="",  # Will be computed by background task
                mime_type="application/octet-stream",  # Detected by the task
                custom_metadata={
                    "s3_key": request.s3_key,
                    versions.VERSIONS_KEY: versions.first_version(
                        s3_key=request.s3_key, user_id=user.id
                    ),
                },
                document_uuid=request.document_uuid,  # Use UUID from upload
                retrieval_mode=request.retrieval_mode,
                scope=scope,
                folder_id=folder_id,
                workflow_id=workflow_id,
                file_folder_id=file_folder_id,
            )

        # Enqueue background task for processing
        await enqueue_job(
            FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
            document.id,
            request.s3_key,
            user.selected_organization_id,
            str(user.provider_id),
            128,  # max_tokens (default)
            request.retrieval_mode,
        )

        logger.info(
            f"{'New version of' if previous is not None else 'Created'} document "
            f"{document.document_uuid} (id={document.id}) and enqueued processing, "
            f"org {user.selected_organization_id}"
        )

        capture_event(
            distinct_id=str(user.provider_id),
            event=PostHogEvent.KNOWLEDGE_BASE_CREATED,
            properties={
                "document_id": document.id,
                "document_uuid": str(document.document_uuid),
                "new_version": previous is not None,
                "filename": filename,
                "retrieval_mode": request.retrieval_mode,
                "organization_id": user.selected_organization_id,
            },
        )

        return _document_response(document)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error processing document: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to process document"
        ) from exc


class KnowledgeBaseUsageSchema(BaseModel):
    """How much of the knowledge-base allowance is spent.

    Shown before somebody hits the wall rather than after. A cap a customer
    cannot see is indistinguishable from a bug the first time it refuses them.
    """

    bytes_used: int
    bytes_limit: int
    #: The largest single document this plan accepts, so the file picker can
    #: refuse before an upload rather than after one.
    max_file_bytes: int
    documents: int


@router.get(
    "/usage",
    response_model=KnowledgeBaseUsageSchema,
    summary="Knowledge base storage used",
)
async def get_usage(user=Depends(get_user)) -> KnowledgeBaseUsageSchema:
    used = await db_client.get_knowledge_base_bytes_used(user.selected_organization_id)
    documents = await db_client.get_documents_for_organization(
        organization_id=user.selected_organization_id, limit=100
    )
    # The account's own allowance, not the deployment ceiling. A screen
    # reporting everyone the same limit is how a customer discovers at upload
    # time that the number they were shown was not theirs.
    allowance = await _allowance(user.selected_organization_id)
    return KnowledgeBaseUsageSchema(
        bytes_used=used,
        bytes_limit=allowance.total_bytes,
        max_file_bytes=allowance.max_file_bytes,
        documents=len(documents),
    )


@router.get(
    "/documents",
    response_model=DocumentListResponseSchema,
    summary="List documents",
    **sdk_expose(
        method="list_documents",
        description="List knowledge base documents available to the authenticated organization.",
    ),
)
async def list_documents(
    status: Annotated[
        str | None,
        Query(description="Filter by processing status"),
    ] = None,
    scope: Annotated[
        str | None, Query(description="library | org | channel | agent")
    ] = None,
    folder_id: Annotated[int | None, Query()] = None,
    workflow_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    file_folder_id: Annotated[
        int | None,
        Query(description="Only the files in this Files-page folder"),
    ] = None,
    top_level: Annotated[
        bool, Query(description="Only the files in no Files-page folder")
    ] = False,
    user=Depends(get_user),
):
    """List all documents for the user's organization.

    Access Control:
    * Users can only see documents from their organization.
    """

    try:
        documents = await db_client.get_documents_for_organization(
            organization_id=user.selected_organization_id,
            processing_status=status,
            scope=scope,
            folder_id=folder_id,
            workflow_id=workflow_id,
            limit=limit,
            offset=offset,
            file_folder_id=file_folder_id,
            top_level_only=top_level,
        )

        # Which of these the agent can no longer retrieve from, because the
        # organization changed embedding model after they were ingested. One
        # grouped query for the whole page rather than one per row.
        async with db_client.async_session() as session:
            stranded = await staleness.stranded_document_ids(
                session, user.selected_organization_id
            )
        if stranded:
            logger.warning(
                "Organization {} has {} document(s) embedded with a superseded "
                "model; the agent retrieves nothing from them until they are "
                "re-ingested.",
                user.selected_organization_id,
                len(stranded),
            )

        # Convert to response schema
        document_list = [
            _document_response(doc, needs_reingest=doc.id in stranded)
            for doc in documents
        ]

        return DocumentListResponseSchema(
            documents=document_list,
            total=len(document_list),
            limit=limit,
            offset=offset,
        )

    except Exception as exc:
        logger.error(f"Error listing documents: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list documents") from exc


@router.get(
    "/documents/{document_uuid}",
    response_model=DocumentResponseSchema,
    summary="Get document details",
)
async def get_document(
    document_uuid: str,
    user=Depends(get_user),
):
    """Get details of a specific document.

    Access Control:
    * Users can only access documents from their organization.
    """

    try:
        document = await db_client.get_document_by_uuid(
            document_uuid=document_uuid,
            organization_id=user.selected_organization_id,
        )

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        return _document_response(document)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting document: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get document") from exc


@router.delete(
    "/documents/{document_uuid}",
    summary="Delete document",
)
async def delete_document(
    document_uuid: str,
    user=Depends(get_user),
):
    """Soft delete a document and its chunks.

    Access Control:
    * Users can only delete documents from their organization.
    """

    try:
        success = await db_client.delete_document(
            document_uuid=document_uuid,
            organization_id=user.selected_organization_id,
        )

        if not success:
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info(
            f"Deleted document {document_uuid}, "
            f"user {user.id}, org {user.selected_organization_id}"
        )

        return {"success": True, "message": "Document deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deleting document: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to delete document"
        ) from exc


@router.post(
    "/search",
    response_model=ChunkSearchResponseSchema,
    summary="Search for similar chunks",
)
async def search_chunks(
    request: ChunkSearchRequestSchema,
    user=Depends(get_user),
):
    """Search for document chunks similar to the query.

    This endpoint uses vector similarity search to find relevant chunks.
    Results are returned without threshold filtering - apply similarity
    thresholds at the application layer after optional reranking.

    Access Control:
    * Users can only search documents from their organization.
    """

    try:
        # Import here to avoid circular dependency
        from api.services.configuration.ai_model_configuration import (
            apply_managed_embeddings_base_url,
            get_effective_ai_model_configuration_for_organization,
        )
        from api.services.gen_ai import build_embedding_service

        # Resolved *with its keys*, which is the whole point. This used to call
        # get_resolved_ai_model_configuration, which compiles the configuration
        # and never loads a key into it, so embeddings.api_key was the empty
        # string on every managed account and every search here answered 500.
        # The call path has always used the resolution that applies keys; this
        # route was the one place that did not.
        effective_config = await get_effective_ai_model_configuration_for_organization(
            user.selected_organization_id,
        )
        embeddings_api_key = None
        embeddings_model = None
        embeddings_provider = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None

        if effective_config.embeddings:
            embeddings_api_key = effective_config.embeddings.api_key
            embeddings_model = effective_config.embeddings.model
            embeddings_provider = getattr(effective_config.embeddings, "provider", None)
            embeddings_endpoint = getattr(effective_config.embeddings, "endpoint", None)
            embeddings_base_url = apply_managed_embeddings_base_url(
                provider=embeddings_provider,
                base_url=getattr(effective_config.embeddings, "base_url", None),
            )
            embeddings_api_version = getattr(
                effective_config.embeddings, "api_version", None
            )

        if not embeddings_api_key:
            # Distinguishable from a fault, and says what to do. A blanket 500
            # here is what made the outage above take an evening to find: the
            # reason existed only in the server log, so the screen showed
            # "Failed to search chunks" whether the key was missing, the vendor
            # was down, or the query was malformed.
            raise HTTPException(
                status_code=409,
                detail=(
                    "No embeddings key is configured for this account, so "
                    "documents cannot be searched. Set one under Model "
                    "Configurations > Embedding, or switch the slot to "
                    "Decibyl's managed models."
                ),
            )

        # Manual search runs outside any workflow run, so resolve the MPS
        # correlation id here.
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

        # Perform search
        results = await embedding_service.search_similar_chunks(
            query=request.query,
            organization_id=user.selected_organization_id,
            limit=request.limit,
            document_uuids=request.document_uuids,
        )

        # Apply similarity threshold if provided
        if request.min_similarity is not None:
            results = [r for r in results if r["similarity"] >= request.min_similarity]

        # Convert to response schema
        from api.schemas.knowledge_base import ChunkResponseSchema

        chunks = [
            ChunkResponseSchema(
                id=r["id"],
                document_id=r["document_id"],
                chunk_text=r["chunk_text"],
                contextualized_text=r.get("contextualized_text"),
                chunk_index=r["chunk_index"],
                chunk_metadata=r["chunk_metadata"],
                filename=r["filename"],
                document_uuid=r["document_uuid"],
                similarity=r["similarity"],
            )
            for r in results
        ]

        return ChunkSearchResponseSchema(
            chunks=chunks,
            query=request.query,
            total_results=len(chunks),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error searching chunks: {exc}")
        raise HTTPException(status_code=500, detail="Failed to search chunks") from exc


class TranslateDocumentRequest(BaseModel):
    #: A Sarvam language code. English by default.
    target_language_code: str = "en-IN"


@router.post(
    "/documents/{document_uuid}/translate",
    response_model=DocumentResponseSchema,
    status_code=202,
    summary="Make a copy of a document in another language",
)
async def translate_document_route(
    document_uuid: str,
    request: TranslateDocumentRequest,
    user=Depends(get_user),
):
    """A translated copy, as a document of its own in the same scope.

    Returned pending; the list already polls pending rows to completion. The
    ingestion quota applies as it does to an upload -- this is one.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _assert_room_to_ingest(user.selected_organization_id)
    try:
        document = await translate_document.start(
            source_uuid=document_uuid,
            organization_id=user.selected_organization_id,
            user_id=user.id,
            provider_id=str(user.provider_id),
            target=request.target_language_code,
        )
    except translate_document.NotTranslatable as exc:
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    # Stated rather than read: the copy has only just been queued.
    return _document_response(
        document,
        file_size_bytes=0,
        file_hash="",
        mime_type="text/plain",
        processing_status="pending",
        processing_error=None,
        total_chunks=0,
        docling_metadata={},
        source_url=None,
        organization_id=user.selected_organization_id,
        created_by=user.id,
        is_active=True,
    )


# --- Files: folders, renames and moves --------------------------------------
#
# "File folders" organise the Files page. They are not channels (those are
# /folder) and they never change who reads a file. The rules live in
# services/knowledge_base/folders.py; these handlers only resolve the caller's
# organisation and shape the answer.


@router.get(
    "/file-folders",
    response_model=FileFolderListResponseSchema,
    summary="List the Files page's folders",
)
async def list_file_folders(user=Depends(get_user)) -> FileFolderListResponseSchema:
    rows = await folders.listing(user.selected_organization_id)
    return FileFolderListResponseSchema(
        folders=[FileFolderSchema(**row) for row in rows]
    )


@router.post(
    "/file-folders",
    response_model=FileFolderSchema,
    status_code=201,
    summary="Create a folder on the Files page",
)
async def create_file_folder(
    request: FileFolderCreateSchema, user=Depends(get_user)
) -> FileFolderSchema:
    try:
        row = await folders.create(
            user.selected_organization_id,
            name=request.name,
            parent_id=request.parent_id,
            created_by=user.id,
        )
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc
    return FileFolderSchema(**row)


@router.post(
    "/file-folders/ensure",
    response_model=FileFolderSchema | None,
    summary="Find or create a path of folders (a dropped desktop folder)",
)
async def ensure_file_folder_path(
    request: FileFolderEnsureSchema, user=Depends(get_user)
) -> FileFolderSchema | None:
    try:
        row = await folders.ensure_path(
            user.selected_organization_id,
            parent_id=request.parent_id,
            segments=request.path,
            created_by=user.id,
        )
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc
    return FileFolderSchema(**row) if row else None


@router.patch(
    "/file-folders/{folder_id}",
    response_model=FileFolderSchema,
    summary="Rename or move a folder on the Files page",
)
async def update_file_folder(
    folder_id: int, request: FileFolderUpdateSchema, user=Depends(get_user)
) -> FileFolderSchema:
    organization_id = user.selected_organization_id
    try:
        row = None
        if request.name is not None:
            row = await folders.rename(organization_id, folder_id, request.name)
        if "parent_id" in request.model_fields_set:
            row = await folders.move(organization_id, folder_id, request.parent_id)
        if row is None:
            raise folders.FolderError("Send a new name, a new parent_id, or both.")
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc
    return FileFolderSchema(**row)


@router.delete(
    "/file-folders/{folder_id}",
    response_model=FileFolderDeleteResponseSchema,
    summary="Delete a folder on the Files page",
    responses={409: {"description": "Not empty; say what to do with its contents"}},
)
async def delete_file_folder(
    folder_id: int,
    contents: Annotated[
        str | None,
        Query(
            description=(
                "For a folder that is not empty: move_to_parent puts its files "
                "and folders one level up; delete removes them with it. "
                "Without it, a folder that is not empty is refused with 409."
            )
        ),
    ] = None,
    user=Depends(get_user),
) -> FileFolderDeleteResponseSchema:
    try:
        outcome = await folders.delete(
            user.selected_organization_id, folder_id, contents=contents
        )
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc
    logger.info(
        "Deleted file folder {} for org {} ({}): {}",
        folder_id,
        user.selected_organization_id,
        contents or "empty",
        outcome,
    )
    return FileFolderDeleteResponseSchema(**outcome)


@router.patch(
    "/documents/{document_uuid}",
    response_model=DocumentResponseSchema,
    summary="Rename a file or move it to another folder",
)
async def update_document(
    document_uuid: str, request: DocumentUpdateSchema, user=Depends(get_user)
) -> DocumentResponseSchema:
    """A rename or a move shows everywhere at once: citations name the file
    and its folder from the row as it is when they are read."""
    try:
        document = await folders.place_document(
            user.selected_organization_id,
            document_uuid,
            filename=request.filename,
            file_folder_id=request.file_folder_id,
            move="file_folder_id" in request.model_fields_set,
        )
    except folders.FolderError as exc:
        raise _folder_error(exc) from exc
    return _document_response(document)


@router.get(
    "/changes",
    response_model=ChangesResponseSchema,
    summary="Files and folders changed since a cursor (for sync clients)",
)
async def list_changes(
    cursor: Annotated[
        str | None,
        Query(description="The cursor from the last answer; none for everything"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=sync.MAX_PAGE)] = sync.MAX_PAGE,
    user=Depends(get_user),
) -> ChangesResponseSchema:
    """What a desktop or mobile client keeping a local copy of Files reads.
    Deleted files and folders come back marked ``deleted``; send ``cursor``
    back next time, and ask again straight away while ``has_more``."""
    try:
        page = await sync.changes(
            user.selected_organization_id, cursor=cursor, limit=limit
        )
    except sync.BadCursor as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ChangesResponseSchema(
        files=[
            SyncFileSchema(
                **_document_response(entry["document"]).model_dump(),
                folder_path=entry["folder_path"],
                deleted=entry["deleted"],
            )
            for entry in page["files"]
        ],
        folders=[SyncFolderSchema(**folder) for folder in page["folders"]],
        cursor=page["cursor"],
        has_more=page["has_more"],
    )

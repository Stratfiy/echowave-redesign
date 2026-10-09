"""Pydantic schemas for knowledge base operations."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentUploadRequestSchema(BaseModel):
    """Request schema for initiating document upload."""

    filename: str = Field(..., description="Name of the file to upload")
    mime_type: str = Field(..., description="MIME type of the file")
    custom_metadata: dict[str, Any] | None = Field(
        default=None, description="Optional custom metadata"
    )


class DocumentUploadResponseSchema(BaseModel):
    """Response schema containing upload URL and document metadata."""

    upload_url: str = Field(..., description="Signed URL for uploading the file")
    document_uuid: str = Field(..., description="Unique identifier for the document")
    s3_key: str = Field(..., description="S3 key where file should be uploaded")


class KnowledgeScopeFields(BaseModel):
    """Who a document is knowledge for. See api.enums.KnowledgeScope."""

    scope: str = Field(
        default="library",
        description=(
            "library: read only by a node that names it. org: every agent in the "
            "organisation. channel: the agents answering in folder_id. agent: the "
            "one agent in workflow_id."
        ),
    )
    folder_id: int | None = Field(default=None, description="For scope=channel.")
    workflow_id: int | None = Field(default=None, description="For scope=agent.")


class ProcessDocumentRequestSchema(KnowledgeScopeFields):
    """Request schema for triggering document processing."""

    file_folder_id: int | None = Field(
        default=None,
        description=(
            "The Files-page folder to put the file in (none: the top level). "
            "Organises only; it does not change who reads the file."
        ),
    )

    document_uuid: str = Field(..., description="Document UUID to process")
    s3_key: str = Field(..., description="S3 key of the uploaded file")
    retrieval_mode: str = Field(
        default="chunked",
        description="Retrieval mode: 'chunked' for vector search or 'full_document' for full text retrieval",
    )


class DocumentVersionSchema(BaseModel):
    """One upload of a file. The newest is the one agents read."""

    version: int
    uploaded_at: str | None = None
    uploaded_by: int | None = None
    file_size_bytes: int | None = None
    current: bool = False


class DocumentResponseSchema(BaseModel):
    """Response schema for document metadata."""

    id: int
    document_uuid: str
    filename: str
    file_size_bytes: int
    file_hash: str
    mime_type: str
    processing_status: str  # pending, processing, completed, failed
    processing_error: str | None = None
    # True when this document was embedded with a model the organization has
    # since changed away from. The document is intact and listed as completed,
    # and the agent cannot retrieve a word of it — vectors from two different
    # models are not comparable, so the search filters it out. Re-ingest to fix.
    needs_reingest: bool = False
    total_chunks: int
    retrieval_mode: str = "chunked"
    custom_metadata: dict[str, Any]
    docling_metadata: dict[str, Any]
    source_url: str | None = None
    scope: str = "library"
    folder_id: int | None = None
    workflow_id: int | None = None
    #: The Files-page folder it sits in (not a channel: that is folder_id).
    file_folder_id: int | None = None
    created_at: datetime
    updated_at: datetime
    organization_id: int
    created_by: int
    is_active: bool
    #: The newest upload of this file; re-uploading the same name into the
    #: same folder makes the next one (services/knowledge_base/versions.py).
    version: int = 1
    #: Every upload, oldest first.
    versions: list[DocumentVersionSchema] = Field(default_factory=list)
    #: reading | ready | failed -- what the Files page shows.
    state: str = "ready"
    #: Why it failed, or what agents read meanwhile; None when nothing to say.
    state_detail: str | None = None


class SyncFileSchema(DocumentResponseSchema):
    """A file as the "changed since" listing reports it."""

    #: "Pricing/2026"; "" at the top level.
    folder_path: str = ""
    #: The file was deleted: remove the local copy.
    deleted: bool = False


class SyncFolderSchema(BaseModel):
    id: int
    folder_uuid: str
    name: str
    parent_id: int | None = None
    path: str = ""
    deleted: bool = False
    updated_at: datetime | None = None


class ChangesResponseSchema(BaseModel):
    """Files and folders changed since a cursor, for a client keeping a local
    copy in step. Send ``cursor`` back next time; fetch again straight away
    while ``has_more``."""

    files: list[SyncFileSchema]
    folders: list[SyncFolderSchema]
    cursor: str
    has_more: bool


class DocumentListResponseSchema(BaseModel):
    """Response schema for list of documents."""

    documents: list[DocumentResponseSchema]
    total: int
    limit: int
    offset: int


class ChunkSearchRequestSchema(BaseModel):
    """Request schema for searching similar chunks."""

    query: str = Field(..., description="Search query text")
    limit: int = Field(default=5, ge=1, le=50, description="Maximum number of results")
    document_uuids: list[str] | None = Field(
        default=None, description="Filter by specific document UUIDs"
    )
    min_similarity: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class ChunkResponseSchema(BaseModel):
    """Response schema for a document chunk."""

    id: int
    document_id: int
    chunk_text: str
    contextualized_text: str | None
    chunk_index: int
    chunk_metadata: dict[str, Any]
    filename: str
    document_uuid: str
    similarity: float


class ChunkSearchResponseSchema(BaseModel):
    """Response schema for chunk search results."""

    chunks: list[ChunkResponseSchema]
    query: str
    total_results: int


class FileFolderSchema(BaseModel):
    """A folder on the Files page. Organises files; never a channel."""

    id: int
    folder_uuid: str
    name: str
    parent_id: int | None = None
    #: "Pricing/2026"; what a citation names.
    path: str
    #: Files directly inside it.
    file_count: int = 0
    #: Folders directly inside it.
    folder_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FileFolderListResponseSchema(BaseModel):
    folders: list[FileFolderSchema]


class FileFolderCreateSchema(BaseModel):
    name: str = Field(..., description="The folder's name")
    parent_id: int | None = Field(
        default=None, description="The folder to create it in; none: the top level"
    )


class FileFolderEnsureSchema(BaseModel):
    """A path of folders to find or create, for a dropped desktop folder."""

    parent_id: int | None = Field(default=None)
    path: list[str] = Field(..., description='e.g. ["Contracts", "2026"]')


class FileFolderUpdateSchema(BaseModel):
    """Rename and/or move. Sending ``parent_id: null`` moves it to the top
    level; leaving ``parent_id`` out leaves it where it is."""

    name: str | None = None
    parent_id: int | None = None


class FileFolderDeleteResponseSchema(BaseModel):
    files_moved: int = 0
    folders_moved: int = 0
    files_deleted: int = 0
    folders_deleted: int = 0


class DocumentUpdateSchema(BaseModel):
    """Rename and/or move a file. ``file_folder_id: null`` moves it to the
    top level; leaving it out leaves it where it is."""

    filename: str | None = None
    file_folder_id: int | None = None

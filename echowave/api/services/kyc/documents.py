"""Storage for KYC documents.

These files are incorporation certificates, GST certificates and identity
documents — sensitive under the DPDP Act. They get their own bucket
(``KYC_BUCKET``) and, deliberately, their own storage client.

``MinioFileSystem`` is NOT reused here. Its constructor unconditionally applies
an anonymous ``s3:GetObject``/``PutObject``/``DeleteObject`` policy to whatever
bucket it is given — appropriate for the call-recording bucket it was written
for, catastrophic for identity documents. This module talks to MinIO directly
and never touches the bucket policy, so the bucket stays private.

For the same reason nothing here returns a URL. Reads go through
:func:`read_document`, which hands bytes back to a staff-gated route that
streams them. There is no link that can be forwarded, pasted into a ticket, or
guessed.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import PurePosixPath

from loguru import logger
from minio import Minio

from api.constants import (
    KYC_BUCKET,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
)

#: What a carrier will accept, and nothing else. An HTML or SVG upload streamed
#: back to a reviewer's browser would be a stored-XSS vector, so the allow-list
#: holds only types that cannot carry script.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
)

#: Certificates are a page or two. This cap is a denial-of-service guard.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


class KycDocumentError(ValueError):
    """A rejected upload — wrong type, too large, or empty."""


def _client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def _ensure_bucket(client: Minio) -> None:
    """Create the bucket if absent, and never set a policy on it.

    A bucket with no policy is private, which is the whole point. If it already
    exists we leave it exactly as the operator configured it.
    """
    if not client.bucket_exists(KYC_BUCKET):
        client.make_bucket(KYC_BUCKET)


def storage_key(*, organization_id: int, kind: str, filename: str) -> str:
    """Where a document lives in the bucket.

    Prefixed by organization so a deletion request for one account is a prefix
    operation, and so even a misconfigured policy segregates customers.

    The customer-supplied filename is reduced to a random name plus a validated
    extension. That name is untrusted input; path separators or traversal
    sequences in it must never reach an object key.
    """
    suffix = PurePosixPath(filename or "").suffix.lower()[:12]
    if suffix and not suffix[1:].isalnum():
        suffix = ""
    return f"kyc/{organization_id}/{kind}/{uuid.uuid4().hex}{suffix}"


def validate_upload(*, content_type: str | None, size_bytes: int) -> None:
    """Reject an upload we will not store. Raises :class:`KycDocumentError`."""
    if size_bytes <= 0:
        raise KycDocumentError("The file is empty.")
    if size_bytes > MAX_DOCUMENT_BYTES:
        raise KycDocumentError(
            f"File is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)}MB."
        )
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized not in ALLOWED_CONTENT_TYPES:
        raise KycDocumentError("Upload a PDF, JPEG or PNG.")


async def store_document(
    *,
    organization_id: int,
    kind: str,
    filename: str,
    content: bytes,
    content_type: str | None,
) -> str:
    """Put a validated document in the bucket and return its storage key."""
    validate_upload(content_type=content_type, size_bytes=len(content))
    key = storage_key(organization_id=organization_id, kind=kind, filename=filename)

    def _put() -> None:
        client = _client()
        _ensure_bucket(client)
        client.put_object(
            KYC_BUCKET,
            key,
            io.BytesIO(content),
            length=len(content),
            content_type=(content_type or "application/octet-stream").split(";")[0],
        )

    try:
        await asyncio.to_thread(_put)
    except Exception as e:
        logger.error("Failed to store KYC document for org {}: {}", organization_id, e)
        raise KycDocumentError("Could not store the file. Try again.") from e

    # The key, never the filename — a log line should not carry a customer's
    # document name next to their organization id.
    logger.info(
        "Stored KYC document for org {} at {} ({} bytes)",
        organization_id,
        key,
        len(content),
    )
    return key


async def read_document(storage_key_: str) -> bytes:
    """Fetch a document's bytes for a staff reviewer.

    Streaming through the API rather than handing out a URL means every view is
    authenticated, attributable and loggable, and it does not depend on the
    bucket policy being right.
    """

    def _get() -> bytes:
        response = None
        try:
            response = _client().get_object(KYC_BUCKET, storage_key_)
            return response.read()
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    return await asyncio.to_thread(_get)


async def delete_document(storage_key_: str) -> bool:
    """Remove a document from the bucket.

    Used when a customer replaces a file, and to honour a deletion request once
    the retention purpose has ended.
    """

    def _remove() -> None:
        _client().remove_object(KYC_BUCKET, storage_key_)

    try:
        await asyncio.to_thread(_remove)
        return True
    except Exception as e:
        logger.error("Failed to delete KYC document {}: {}", storage_key_, e)
        return False

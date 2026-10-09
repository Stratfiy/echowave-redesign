"""A file uploaded again is the same file, newer -- not a second copy.

Files behave like a synced vault: the same name uploaded into the same
folder (and the same channel or agent, for a file given to one) becomes the
next version of the file already there. That version is read and indexed
again; the earlier ones stay listed. The file keeps its id, so everything
that refers to it -- a citation, a step that names it, a chat attachment --
now means the new content.

The history lives on the row, in ``custom_metadata["versions"]``: one entry
per upload, oldest first, each with the storage key it was uploaded to. The
previous version's passages stay in place until the new one has been read,
so an agent answering meanwhile still has the file, and a new version that
cannot be read leaves the previous one answering and says so.

Each file also has one plain **state** for the screen: Reading, Ready, or
Couldn't read with the reason. Nothing fails silently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.db import db_client

READING = "reading"
READY = "ready"
FAILED = "failed"

VERSIONS_KEY = "versions"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def history_from(
    metadata: dict[str, Any] | None, *, created_at: Any, created_by: Any
) -> list[dict[str, Any]]:
    """Every version recorded in ``metadata``, oldest first. A file uploaded
    before versions existed is version 1 of itself."""
    metadata = metadata or {}
    stored = metadata.get(VERSIONS_KEY)
    if isinstance(stored, list) and stored:
        return [dict(v) for v in stored if isinstance(v, dict)]
    return [
        {
            "version": 1,
            "s3_key": metadata.get("s3_key"),
            "uploaded_at": _iso(created_at),
            "uploaded_by": created_by,
        }
    ]


def history(document: Any) -> list[dict[str, Any]]:
    return history_from(
        document.custom_metadata,
        created_at=document.created_at,
        created_by=document.created_by,
    )


def current(document: Any) -> int:
    return max((int(v.get("version") or 1) for v in history(document)), default=1)


def state(
    *,
    processing_status: str,
    processing_error: str | None,
    version: int,
    total_chunks: int,
) -> tuple[str, str | None]:
    """The file's state for the screen, and what to say beside it."""
    earlier_answers = version > 1 and total_chunks > 0
    if processing_status in ("pending", "processing"):
        if earlier_answers:
            return READING, (
                f"Reading version {version}. Agents answer from version "
                f"{version - 1} until it is read."
            )
        return READING, None
    if processing_status == "failed":
        reason = (processing_error or "").strip() or (
            "We could not read this file. Upload it again, or contact support "
            "if it keeps happening."
        )
        if earlier_answers:
            reason = f"{reason} Agents still answer from version {version - 1}."
        return FAILED, reason
    return READY, None


def describe(values: dict[str, Any]) -> dict[str, Any]:
    """The version fields of a document response, from the values the
    response is being built from (which may state the status outright)."""
    versions = history_from(
        values["custom_metadata"],
        created_at=values["created_at"],
        created_by=values["created_by"],
    )
    version = max((int(v.get("version") or 1) for v in versions), default=1)
    label, detail = state(
        processing_status=values["processing_status"],
        processing_error=values["processing_error"],
        version=version,
        total_chunks=values["total_chunks"] or 0,
    )
    listed = []
    for entry in versions:
        number = int(entry.get("version") or 1)
        is_current = number == version
        listed.append(
            {
                "version": number,
                "uploaded_at": entry.get("uploaded_at"),
                "uploaded_by": entry.get("uploaded_by"),
                # The row carries the current version's size; earlier ones
                # were recorded when they were superseded.
                "file_size_bytes": (
                    values["file_size_bytes"]
                    if is_current
                    else entry.get("file_size_bytes")
                ),
                "current": is_current,
            }
        )
    return {
        "version": version,
        "versions": listed,
        "state": label,
        "state_detail": detail,
    }


async def previous_upload(
    organization_id: int,
    *,
    filename: str,
    scope: str,
    folder_id: int | None,
    workflow_id: int | None,
    file_folder_id: int | None,
    document_uuid: str,
):
    """The file this upload is a new version of, or None for a new file."""
    return await db_client.find_document_by_name_in_file_folder(
        organization_id=organization_id,
        filename=filename,
        file_folder_id=file_folder_id,
        scope=scope,
        folder_id=folder_id,
        workflow_id=workflow_id,
        exclude_uuid=document_uuid,
    )


def first_version(*, s3_key: str, user_id: int) -> list[dict[str, Any]]:
    return [
        {
            "version": 1,
            "s3_key": s3_key,
            "uploaded_at": datetime.now(UTC).isoformat(),
            "uploaded_by": user_id,
        }
    ]


async def begin(
    document: Any,
    *,
    organization_id: int,
    s3_key: str,
    user_id: int,
    retrieval_mode: str,
):
    """Make ``s3_key`` the file's next version, to be read. Returns the row."""
    versions = history(document)
    # What the row says now is about the version being superseded; keep it
    # with that version before the worker overwrites it with the new one's.
    versions[-1] = {
        **versions[-1],
        "file_size_bytes": versions[-1].get("file_size_bytes")
        or document.file_size_bytes,
        "file_hash": versions[-1].get("file_hash") or document.file_hash,
    }
    versions.append(
        {
            "version": int(versions[-1].get("version") or len(versions)) + 1,
            "s3_key": s3_key,
            "uploaded_at": datetime.now(UTC).isoformat(),
            "uploaded_by": user_id,
        }
    )
    return await db_client.begin_document_version(
        document.id,
        organization_id=organization_id,
        s3_key=s3_key,
        versions=versions,
        retrieval_mode=retrieval_mode,
    )

"""Where images live: the workspace's object storage, indexed by a table.

Bytes go to the bucket the app already uses for recordings and documents
(MinIO or S3, ``services/storage``), under ``images/<organization_id>/``.
The ``generated_images`` row is how one is found again, and **every read
here takes the organisation and puts it in the query** -- an image id from
another workspace reads as no image at all, never as somebody else's poster.

A person sees an image through a short-lived signed URL, minted when the
card draws (the same way recordings and attachments are shown), or
downloads it through the API, which checks the session's organisation.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.image_models import GeneratedImageModel
from api.enums import StorageBackend
from api.services.filesystem import BaseFileSystem

#: Largest image read back into memory: a reference photo or a poster.
MAX_BYTES = 15 * 1024 * 1024
#: Largest reference image a person may attach.
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
#: Signed URLs live this long: long enough to look, short enough not to leak.
URL_SECONDS = 3600

REFERENCE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_ID = re.compile(r"^img_[0-9a-f]{32}$")

GENERATED = "generated"
REFERENCE = "reference"


def new_id() -> str:
    return f"img_{uuid4().hex}"


def is_image_id(value: str | None) -> bool:
    return bool(_ID.match(str(value or "")))


def _backend() -> str:
    return StorageBackend.get_current_backend().value


def _fs(backend: str | None = None) -> BaseFileSystem:
    """The filesystem an image was written to, or the current one when this
    deployment has no configuration for the recorded backend (a migrated
    deployment), said in the log rather than absorbed."""
    from api.services.storage import get_storage_for_backend, storage_fs

    if not backend or backend == _backend():
        return storage_fs
    try:
        return get_storage_for_backend(backend)
    except ValueError as exc:
        logger.error(
            "Image storage backend {!r} is not configured here ({}); reading "
            "from the current one",
            backend,
            exc,
        )
        return storage_fs


def _key(organization_id: int, image_uuid: str, mime_type: str) -> str:
    extension = REFERENCE_TYPES.get(mime_type, "png")
    return f"images/{organization_id}/{image_uuid}.{extension}"


async def _write(key: str, data: bytes) -> None:
    ok = await _fs().acreate_file_from_bytes(key, data)
    if not ok:
        raise OSError("The image could not be saved to storage.")


async def save(
    *,
    organization_id: int,
    data: bytes,
    mime_type: str,
    kind: str = GENERATED,
    user_id: int | None = None,
    workflow_id: int | None = None,
    filename: str | None = None,
    width: int | None = None,
    height: int | None = None,
    **fields: Any,
) -> GeneratedImageModel:
    """Write the bytes, then the row. A row is never written for bytes that
    did not land: an image card pointing at nothing is a lie on the screen."""
    image_uuid = new_id()
    key = _key(organization_id, image_uuid, mime_type)
    await _write(key, data)
    row = GeneratedImageModel(
        image_uuid=image_uuid,
        organization_id=organization_id,
        created_by_user_id=user_id,
        workflow_id=workflow_id,
        kind=kind,
        mime_type=mime_type,
        size_bytes=len(data),
        filename=(filename or None) and filename[:255],
        width=width,
        height=height,
        storage_key=key,
        storage_backend=_backend(),
        **fields,
    )
    async with db_client.async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def get(organization_id: int, image_uuid: str) -> GeneratedImageModel | None:
    """One image of this workspace's, or None -- including for another
    workspace's id."""
    if not is_image_id(image_uuid):
        return None
    async with db_client.async_session() as session:
        return await session.scalar(
            select(GeneratedImageModel).where(
                GeneratedImageModel.organization_id == organization_id,
                GeneratedImageModel.image_uuid == image_uuid,
            )
        )


async def get_many(
    organization_id: int, image_uuids: list[str]
) -> list[GeneratedImageModel]:
    """This workspace's images among ``image_uuids``, in the order asked."""
    wanted = [u for u in image_uuids if is_image_id(u)]
    if not wanted:
        return []
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(GeneratedImageModel).where(
                    GeneratedImageModel.organization_id == organization_id,
                    GeneratedImageModel.image_uuid.in_(wanted),
                )
            )
        ).all()
    by_id = {row.image_uuid: row for row in rows}
    return [by_id[u] for u in wanted if u in by_id]


async def read_bytes(row: GeneratedImageModel) -> bytes | None:
    return await _fs(row.storage_backend).aread_bytes(row.storage_key, MAX_BYTES)


async def signed_url(row: GeneratedImageModel) -> str | None:
    return await _fs(row.storage_backend).aget_signed_url(
        row.storage_key, expiration=URL_SECONDS
    )


def view(row: GeneratedImageModel) -> dict[str, Any]:
    """What the client may know about an image: never its storage key."""
    return {
        "image_uuid": row.image_uuid,
        "kind": row.kind,
        "provider": row.provider,
        "format": row.format,
        "width": row.width,
        "height": row.height,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "filename": row.filename,
        "option_index": row.option_index,
        "parent_uuid": row.parent_uuid,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }

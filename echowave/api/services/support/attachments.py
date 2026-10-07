"""Files on a ticket (screen 28, "optional attachments").

Stored through ``services/storage`` under ``support/<org>/<ticket>/``; the
row keeps the key. A storage failure is the honest state the design names,
"attachment failure": nothing is recorded, the ticket and any reply stay as
they are, and the person can try again. Customers add files to their own
tickets; staff open them through a short-lived signed link, audited.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import AdminActionLogModel
from api.db.support_models import SupportAttachmentModel, SupportTicketModel

MAX_BYTES = 5 * 1024 * 1024
#: Screenshots, PDFs and text. Anything else is refused by name, so a person
#: is told why rather than finding their file missing.
CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "application/pdf",
        "text/plain",
        "text/csv",
    }
)
LINK_SECONDS = 300


class AttachmentRefused(ValueError):
    pass


class NotFound(AttachmentRefused):
    pass


class StorageUnavailable(RuntimeError):
    """Storage is not set up or did not take the file."""


def _safe_name(name: str) -> str:
    name = (name or "file").strip().split("/")[-1].split("\\")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120]
    return name or "file"


def _storage():
    from api.services.storage import storage_fs

    return storage_fs


async def add(
    *,
    organization_id: int,
    user_id: int,
    ticket_id: int,
    file_name: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    """Store one file on the person's own ticket."""
    if content_type not in CONTENT_TYPES:
        raise AttachmentRefused("Attach a screenshot, a PDF or a text file.")
    if not data:
        raise AttachmentRefused("That file is empty.")
    if len(data) > MAX_BYTES:
        raise AttachmentRefused("Files up to 5 MB can be attached.")
    async with db_client.async_session() as session:
        ticket = await session.scalar(
            select(SupportTicketModel).where(
                SupportTicketModel.id == ticket_id,
                SupportTicketModel.organization_id == organization_id,
                SupportTicketModel.requester_user_id == user_id,
            )
        )
        if ticket is None:
            raise NotFound("That request is not here.")
    name = _safe_name(file_name)
    key = f"support/{organization_id}/{ticket_id}/{uuid.uuid4().hex}-{name}"
    try:
        stored = await _storage().acreate_file_from_bytes(key, data)
    except Exception as exc:  # noqa: BLE001 - reported as a state, not a crash
        logger.error("Support attachment for ticket {} not stored: {}", ticket_id, exc)
        stored = False
    if not stored:
        raise StorageUnavailable(
            "The file could not be stored. Your request and message are kept; try the file again later."
        )
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = SupportAttachmentModel(
            ticket_id=ticket_id,
            organization_id=organization_id,
            uploaded_by=user_id,
            file_name=name,
            content_type=content_type,
            size_bytes=len(data),
            storage_key=key,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        out = {
            "id": row.id,
            "file_name": row.file_name,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "created_at": now.isoformat(),
        }
        await session.commit()
    return out


async def staff_link(*, attachment_id: int, staff_id: int) -> str:
    """A short-lived link for staff, with an audit row."""
    async with db_client.async_session() as session:
        row = await session.get(SupportAttachmentModel, attachment_id)
        if row is None:
            raise NotFound("No such file.")
        ticket = await session.get(SupportTicketModel, row.ticket_id)
        try:
            url = await _storage().aget_signed_url(
                row.storage_key, expiration=LINK_SECONDS
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Support attachment {} link failed: {}", attachment_id, exc)
            url = None
        if not url:
            raise StorageUnavailable(
                "Storage is not available, so the file cannot be opened."
            )
        session.add(
            AdminActionLogModel(
                actor_user_id=staff_id,
                action="support_attachment_opened",
                target_user_id=getattr(ticket, "requester_user_id", None),
                target_organization_id=row.organization_id,
                note=f"ticket={row.ticket_id}; attachment={row.id}",
            )
        )
        await session.commit()
    return url

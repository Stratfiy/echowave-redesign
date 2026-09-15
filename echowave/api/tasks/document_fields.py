"""Read a filed document's fields after processing, and the daily reminder
sweep. See services/workflow/document_fields.py."""

from __future__ import annotations

from loguru import logger


async def extract_document_fields(_ctx, document_id: int, organization_id: int) -> None:
    from api.services.workflow import document_fields

    try:
        await document_fields.propose(int(organization_id), int(document_id))
    except Exception as exc:  # noqa: BLE001 - the document is filed regardless
        logger.error("Could not read fields for document {}: {}", document_id, exc)


async def remind_due_tasks(_ctx) -> int:
    from api.services.workflow import document_fields

    return await document_fields.remind_due()

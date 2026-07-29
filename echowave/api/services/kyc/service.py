"""Telephony KYC orchestration.

Sits between the routes and the data layer. The state machine in
:mod:`api.services.kyc.state` decides what is legal; this module applies it,
records who did what, and keeps the stored objects and their rows consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.db.models import KycDocumentModel, OrganizationKycModel
from api.enums import KycBusinessType, KycDocumentKind, KycStatus
from api.services.kyc import documents as document_store
from api.services.kyc.state import (
    KycTransitionError,
    assert_transition,
    may_place_telephony_calls,
    missing_documents,
    required_documents,
)


@dataclass(frozen=True)
class KycView:
    """What a customer is told about their own verification."""

    status: str
    business_type: str | None
    legal_name: str | None
    gstin: str | None
    submitted_at: datetime | None
    rejection_reason: str | None
    carrier_rejection_reason: str | None
    required_documents: tuple[str, ...]
    missing_documents: tuple[str, ...]
    documents: tuple[dict, ...]
    can_submit: bool
    telephony_enabled: bool


def _document_summary(document: KycDocumentModel) -> dict:
    """A document as the customer and reviewer see it — never the storage key.

    The key is an internal locator; exposing it invites someone to try
    constructing a bucket URL from it.
    """
    return {
        "id": document.id,
        "kind": document.kind,
        "filename": document.filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at
        else None,
    }


def build_view(record: OrganizationKycModel) -> KycView:
    """Shape a record for display, including what is still outstanding."""
    supplied = {d.kind for d in (record.documents or [])}
    missing = missing_documents(record.business_type, supplied)

    return KycView(
        status=record.status,
        business_type=record.business_type,
        legal_name=record.legal_name,
        gstin=record.gstin,
        submitted_at=record.submitted_at,
        rejection_reason=record.rejection_reason,
        carrier_rejection_reason=record.carrier_rejection_reason,
        required_documents=tuple(
            sorted(k.value for k in required_documents(record.business_type))
        ),
        missing_documents=tuple(sorted(k.value for k in missing)),
        documents=tuple(_document_summary(d) for d in (record.documents or [])),
        # Submitting with documents missing would only produce a rejection, so
        # the button is off until the set is complete.
        can_submit=(
            not missing
            and record.business_type is not None
            and record.status
            in {
                KycStatus.NOT_STARTED.value,
                KycStatus.REJECTED.value,
                KycStatus.CARRIER_REJECTED.value,
            }
        ),
        telephony_enabled=may_place_telephony_calls(record.status),
    )


async def get_view(organization_id: int) -> KycView:
    record = await db_client.get_or_create_kyc(organization_id)
    return build_view(record)


async def set_business_details(
    *,
    organization_id: int,
    business_type: str,
    legal_name: str,
    gstin: str | None,
) -> KycView:
    """Record who the customer is. Decides which documents are required."""
    try:
        KycBusinessType(business_type)
    except ValueError as exc:
        raise ValueError("Choose a business type.") from exc

    await db_client.get_or_create_kyc(organization_id)
    record = await db_client.update_kyc(
        organization_id,
        business_type=business_type,
        legal_name=(legal_name or "").strip() or None,
        gstin=(gstin or "").strip() or None,
    )
    return build_view(record)


async def upload_document(
    *,
    organization_id: int,
    uploaded_by: int | None,
    kind: str,
    filename: str,
    content: bytes,
    content_type: str | None,
) -> KycView:
    """Store a document and attach it to this account's record.

    Uploading a kind that already exists replaces it, because the common case
    is a customer fixing a rejected scan and two copies would only make a
    reviewer guess which one counts.
    """
    try:
        KycDocumentKind(kind)
    except ValueError as exc:
        raise ValueError("Unknown document type.") from exc

    record = await db_client.get_or_create_kyc(organization_id)

    # Approved records are frozen. Re-verification means the carrier moved the
    # account back, not a customer quietly swapping a certificate.
    if record.status == KycStatus.CARRIER_APPROVED.value:
        raise KycTransitionError("Verification is complete; documents are locked.")

    key = await document_store.store_document(
        organization_id=organization_id,
        kind=kind,
        filename=filename,
        content=content,
        content_type=content_type,
    )

    superseded = [d for d in (record.documents or []) if d.kind == kind]

    await db_client.add_document(
        organization_id=organization_id,
        organization_kyc_id=record.id,
        kind=kind,
        storage_key=key,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )

    # Delete the row first: an orphaned object costs storage, but a row
    # pointing at a deleted object shows a reviewer a document that will not
    # open, which is worse.
    for old in superseded:
        removed = await db_client.delete_document(
            old.id, organization_id=organization_id
        )
        if removed is not None:
            await document_store.delete_document(old.storage_key)

    return build_view(await db_client.get_kyc(organization_id))


async def remove_document(*, organization_id: int, document_id: int) -> KycView:
    record = await db_client.get_or_create_kyc(organization_id)
    if record.status == KycStatus.CARRIER_APPROVED.value:
        raise KycTransitionError("Verification is complete; documents are locked.")

    removed = await db_client.delete_document(
        document_id, organization_id=organization_id
    )
    if removed is not None:
        await document_store.delete_document(removed.storage_key)
    return build_view(await db_client.get_kyc(organization_id))


async def submit(organization_id: int) -> KycView:
    """Hand the account to us for review."""
    record = await db_client.get_or_create_kyc(organization_id)

    supplied = {d.kind for d in (record.documents or [])}
    missing = missing_documents(record.business_type, supplied)
    if record.business_type is None:
        raise ValueError("Tell us your business type first.")
    if missing:
        readable = ", ".join(sorted(k.value.replace("_", " ") for k in missing))
        raise ValueError(f"Still needed: {readable}.")

    target = assert_transition(record.status, KycStatus.SUBMITTED)

    updated = await db_client.update_kyc(
        organization_id,
        status=target.value,
        submitted_at=datetime.now(UTC),
        # A resubmission answers the previous rejection, so the old reason
        # stops applying.
        rejection_reason=None,
        carrier_rejection_reason=None,
    )
    logger.info("KYC submitted for review by org {}", organization_id)
    return build_view(updated)

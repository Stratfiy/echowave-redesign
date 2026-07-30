"""Customer-facing telephony KYC.

Indian numbers require the carrier to verify each end customer, so this is the
flow that collects the documents and hands them to us. Everything here is
scoped to the caller's own organization — the organization id is taken from the
authenticated user and never from the request.

Staff review lives in ``routes/kyc_admin.py`` behind the superuser gate.
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api.constants import MANAGED_TELEPHONY_ENABLED
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.kyc import service as kyc_service
from api.services.kyc.documents import MAX_DOCUMENT_BYTES, KycDocumentError
from api.services.kyc.state import KycTransitionError

router = APIRouter(prefix="/kyc", tags=["kyc"])


class BusinessDetailsRequest(BaseModel):
    business_type: str
    legal_name: str
    gstin: str | None = None


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _view_response(view: kyc_service.KycView) -> dict[str, Any]:
    return {
        "status": view.status,
        "business_type": view.business_type,
        "legal_name": view.legal_name,
        "gstin": view.gstin,
        "submitted_at": view.submitted_at.isoformat() if view.submitted_at else None,
        "rejection_reason": view.rejection_reason,
        "carrier_rejection_reason": view.carrier_rejection_reason,
        "required_documents": list(view.required_documents),
        "missing_documents": list(view.missing_documents),
        "documents": list(view.documents),
        "can_submit": view.can_submit,
        # The one the UI gates phone calling on. It reflects the carrier's
        # decision, never our own review.
        "telephony_enabled": view.telephony_enabled,
        # Whether the flow is open at all. False while the carrier reseller
        # arrangement is still being set up — the screen says "coming soon"
        # rather than inviting uploads that go nowhere.
        "verification_open": MANAGED_TELEPHONY_ENABLED,
    }


@router.get("")
async def get_kyc(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's verification status and what is still outstanding."""
    return _view_response(await kyc_service.get_view(_organization_id(user)))


@router.put("/business-details")
async def set_business_details(
    payload: BusinessDetailsRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Record the legal entity. Determines which documents are required."""
    try:
        view = await kyc_service.set_business_details(
            organization_id=_organization_id(user),
            business_type=payload.business_type,
            legal_name=payload.legal_name,
            gstin=payload.gstin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view_response(view)


@router.post("/documents")
async def upload_document(
    kind: str = Form(...),
    file: UploadFile = File(...),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Upload one document. Re-uploading a kind replaces the previous file."""
    organization_id = _organization_id(user)

    # Read with a ceiling rather than trusting a client-supplied length: the
    # size header is not authoritative and an unbounded read is a memory DoS.
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)}MB.",
        )

    try:
        view = await kyc_service.upload_document(
            organization_id=organization_id,
            uploaded_by=user.id,
            kind=kind,
            filename=file.filename or "document",
            content=content,
            content_type=file.content_type,
        )
    except KycDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view_response(view)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    try:
        view = await kyc_service.remove_document(
            organization_id=_organization_id(user), document_id=document_id
        )
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view_response(view)


@router.post("/submit")
async def submit_kyc(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Hand the documents to us for review."""
    try:
        view = await kyc_service.submit(_organization_id(user))
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view_response(view)

"""Staff review of telephony KYC.

Cross-account by definition — a reviewer looks at other organizations'
incorporation and identity documents — so the whole router is behind the
superuser gate, declared once at router level so a new endpoint added here is
gated by default rather than by remembering.

Documents are streamed through this API rather than handed out as URLs. That
keeps every view authenticated and attributable, and does not depend on the
storage bucket's policy being right.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import CarrierNotConfigured, CarrierVerdict
from api.services.kyc.documents import read_document
from api.services.kyc.state import KycTransitionError

router = APIRouter(
    prefix="/admin/kyc",
    tags=["admin-kyc"],
    dependencies=[Depends(get_superuser)],
)


class RejectRequest(BaseModel):
    reason: str


class ForwardRequest(BaseModel):
    carrier: str | None = None


class CarrierVerdictRequest(BaseModel):
    """Records what the carrier decided.

    Used both by the status poll and by staff entering a verdict that arrived
    by email while the carrier's API is not wired up.
    """

    verdict: str
    raw_status: str | None = None
    rejection_reason: str | None = None


def _item(item: kyc_service.ReviewItem) -> dict[str, Any]:
    return {
        "organization_id": item.organization_id,
        "organization_name": item.organization_name,
        "status": item.status,
        "business_type": item.business_type,
        "legal_name": item.legal_name,
        "gstin": item.gstin,
        "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
        "forwarded_at": item.forwarded_at.isoformat() if item.forwarded_at else None,
        "carrier": item.carrier,
        "carrier_status": item.carrier_status,
        "rejection_reason": item.rejection_reason,
        "carrier_rejection_reason": item.carrier_rejection_reason,
        "documents": list(item.documents),
    }


def _view(view: kyc_service.KycView) -> dict[str, Any]:
    return {
        "status": view.status,
        "telephony_enabled": view.telephony_enabled,
        "missing_documents": list(view.missing_documents),
    }


@router.get("/queue")
async def review_queue(status: str | None = None) -> dict[str, Any]:
    """Everything a reviewer might act on, oldest submission first.

    Oldest-first because the queue is a promise about turnaround; newest-first
    would let an old submission starve.
    """
    statuses = [s.strip() for s in status.split(",")] if status else None
    items = await kyc_service.list_review_queue(statuses=statuses)
    return {"items": [_item(i) for i in items], "total": len(items)}


@router.get("/documents/{document_id}")
async def get_document(document_id: int) -> Response:
    """Stream one document to the reviewer.

    Deliberately not a redirect to storage: a URL can be forwarded, and the
    bucket must stay private. ``Content-Disposition: attachment`` and a
    restrictive CSP because these are customer-supplied files — even with the
    upload allow-list, nothing here should ever render in the browser as
    same-origin content.
    """
    document = await db_client.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        content = await read_document(document.storage_key)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Could not read the stored document"
        ) from exc

    filename = (document.filename or "document").replace('"', "")
    return Response(
        content=content,
        media_type=document.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{organization_id}/claim")
async def claim(
    organization_id: int, user: UserModel = Depends(get_superuser)
) -> dict[str, Any]:
    try:
        view = await kyc_service.claim_for_review(
            organization_id=organization_id, reviewer_id=user.id
        )
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(view)


@router.post("/{organization_id}/reject")
async def reject(
    organization_id: int,
    payload: RejectRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    try:
        view = await kyc_service.reject(
            organization_id=organization_id,
            reviewer_id=user.id,
            reason=payload.reason,
        )
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(view)


@router.post("/{organization_id}/approve")
async def approve(
    organization_id: int,
    payload: ForwardRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Accept the documents and forward them to the carrier.

    This does not enable calling. The account moves to ``forwarded`` and waits
    for the licensee, whose verdict is the gate.
    """
    try:
        view = await kyc_service.approve_and_forward(
            organization_id=organization_id,
            reviewer_id=user.id,
            carrier_name=payload.carrier,
        )
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CarrierNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(view)


@router.post("/{organization_id}/carrier-verdict")
async def carrier_verdict(
    organization_id: int, payload: CarrierVerdictRequest
) -> dict[str, Any]:
    """Record the licensee's decision. The call that opens telephony."""
    try:
        verdict = CarrierVerdict(payload.verdict)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown verdict") from exc

    try:
        view = await kyc_service.record_carrier_verdict(
            organization_id=organization_id,
            verdict=verdict,
            raw_status=payload.raw_status,
            rejection_reason=payload.rejection_reason,
        )
    except KycTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if view is None:
        return {"status": "forwarded", "telephony_enabled": False}
    return _view(view)

"""The partner queue, from our side.

Behind ``get_superuser`` at the router, so every route here is staff-only by
construction rather than one decorator at a time — the same shape as
``kyc_admin`` and the billing dashboard.

Granting a commission is the only thing on this router that costs money, and
it is the reason the queue exists rather than a settings field: somebody reads
what the applicant said they were and what volume they expect, and decides a
percentage against it. Nothing here is automatic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import PartnerApplicationModel, UserModel
from api.enums import CommissionBasis, PartnerApplicationStatus
from api.services.auth.depends import get_superuser
from api.services.partners import applications as partners
from api.services.partners.applications import PartnerError

router = APIRouter(
    prefix="/admin/partners",
    tags=["admin-partners"],
    dependencies=[Depends(get_superuser)],
)


class ApproveRequest(BaseModel):
    commission_bps: int = Field(
        ...,
        ge=0,
        le=10_000,
        description="Basis points, so 1250 is 12.5%",
    )
    basis: str = Field(
        ...,
        description=(
            "platform_fee (a share of what we keep — cannot go underwater) or "
            "total_spend (a share of everything the account is charged)"
        ),
    )
    note: str | None = Field(None, description="Why this rate. Shown to the applicant.")


class RejectRequest(BaseModel):
    note: str | None = Field(
        None, description="Why not. Shown to the applicant, so write it for them."
    )


class SetCommissionRequest(ApproveRequest):
    """A renegotiation with no new application behind it."""


async def _load(session, application_id: int) -> PartnerApplicationModel:
    application = await session.get(PartnerApplicationModel, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


def _queue_view(application: PartnerApplicationModel) -> dict[str, Any]:
    return {
        "id": application.id,
        "organization_id": application.organization_id,
        "kind": application.kind,
        "expected_minutes_per_month": application.expected_minutes_per_month,
        "company_website": application.company_website,
        "note": application.note,
        "status": application.status,
        "submitted_at": application.submitted_at.isoformat()
        if application.submitted_at
        else None,
        "decided_at": application.decided_at.isoformat()
        if application.decided_at
        else None,
        "decision_note": application.decision_note,
    }


@router.get("/queue")
async def get_queue(
    status: str = Query(PartnerApplicationStatus.PENDING.value),
) -> dict[str, Any]:
    """Applications waiting on us, oldest first."""
    async with db_client.async_session() as session:
        rows = await partners.queue(session, status=status)
        return {
            "applications": [_queue_view(r) for r in rows],
            "bases": [b.value for b in CommissionBasis],
        }


@router.post("/{application_id}/approve")
async def approve_application(
    application_id: int,
    request: ApproveRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Grant it, at a commission."""
    async with db_client.async_session() as session:
        application = await _load(session, application_id)
        try:
            commission = await partners.approve(
                session,
                application=application,
                decided_by=user.id,
                commission_bps=request.commission_bps,
                basis=request.basis,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {
            "application": _queue_view(application),
            "commission": {
                "commission_bps": commission.commission_bps,
                "basis": commission.basis,
                "effective_from": commission.effective_from.isoformat(),
            },
        }


@router.post("/{application_id}/reject")
async def reject_application(
    application_id: int,
    request: RejectRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Turn it down. The account keeps everything it already had."""
    async with db_client.async_session() as session:
        application = await _load(session, application_id)
        try:
            await partners.reject(
                session,
                application=application,
                decided_by=user.id,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {"application": _queue_view(application)}


@router.get("/accounts/{organization_id}/commission")
async def get_commission_history(organization_id: int) -> dict[str, Any]:
    """Every rate this account has been on, newest first.

    The audit trail behind a statement: a partner asking "why is this number
    what it is" is answered from here, not from the live row.
    """
    async with db_client.async_session() as session:
        rows = await partners.history_for(session, organization_id)
        return {
            "commissions": [
                {
                    "id": r.id,
                    "commission_bps": r.commission_bps,
                    "basis": r.basis,
                    "application_id": r.application_id,
                    "effective_from": r.effective_from.isoformat(),
                    "effective_to": r.effective_to.isoformat()
                    if r.effective_to
                    else None,
                    "note": r.note,
                }
                for r in rows
            ]
        }


@router.put("/accounts/{organization_id}/commission")
async def set_commission(
    organization_id: int,
    request: SetCommissionRequest,
    user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """Renegotiate, without a new application.

    Closes the open rate and starts a new one rather than editing, so an
    already-issued statement still reproduces its own number.
    """
    async with db_client.async_session() as session:
        try:
            commission = await partners.set_commission(
                session,
                organization_id=organization_id,
                commission_bps=request.commission_bps,
                basis=request.basis,
                set_by=user.id,
                note=request.note,
            )
        except PartnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {
            "commission": {
                "commission_bps": commission.commission_bps,
                "basis": commission.basis,
                "effective_from": commission.effective_from.isoformat(),
            }
        }

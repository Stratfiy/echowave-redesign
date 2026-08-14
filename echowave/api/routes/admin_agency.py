"""Staff putting an account under an agency, and settling what is owed.

**Every route is staff-only**, declared once at router level so a new endpoint
added here is gated by default rather than by remembering.

The commission rate is set here rather than by the agency, for the obvious
reason: it is what we pay, and a rate the earner can edit is not a rate.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.services.auth.depends import get_superuser
from api.services.billing import agency

router = APIRouter(
    prefix="/admin/agency",
    tags=["admin-agency"],
    dependencies=[Depends(get_superuser)],
)


class SetManagerRequest(BaseModel):
    client_organization_id: int
    #: None detaches the client from its agency. Past accruals stay — the
    #: agency did earn them, and "no longer manages" is not "never did".
    agency_organization_id: int | None = None
    commission_bps: int | None = Field(
        None,
        ge=agency.MIN_COMMISSION_BPS,
        le=agency.MAX_COMMISSION_BPS,
        description="Basis points of what the client is charged. 1000 = 10%.",
    )


@router.post("/manager")
async def set_manager(request: SetManagerRequest) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            client = await agency.set_manager(
                session,
                client_organization_id=request.client_organization_id,
                agency_organization_id=request.agency_organization_id,
                commission_bps=request.commission_bps,
            )
        except agency.AgencyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    return {
        "organization_id": client.id,
        "managed_by_organization_id": client.managed_by_organization_id,
        "commission_bps": client.agency_commission_bps,
    }


class AccrueRequest(BaseModel):
    #: Any day in the month to accrue. The service takes the month it falls in.
    month: date


@router.post("/accrue")
async def accrue(request: AccrueRequest) -> dict[str, Any]:
    """Snapshot a month's commission. Re-runnable without doubling anything."""
    async with db_client.async_session() as session:
        accrued = await agency.accrue_month(session, month=request.month)
        await session.commit()
    return {"accrued": len(accrued)}


class MarkPaidRequest(BaseModel):
    accrual_id: int
    note: str | None = Field(None, max_length=500)


@router.post("/paid")
async def mark_paid(request: MarkPaidRequest) -> dict[str, Any]:
    """Record that a month has been settled. Moves no money."""
    async with db_client.async_session() as session:
        try:
            accrual = await agency.mark_paid(
                session, accrual_id=request.accrual_id, note=request.note
            )
        except agency.AgencyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    return {"accrual_id": accrual.id, "paid_at": accrual.paid_at.isoformat()}

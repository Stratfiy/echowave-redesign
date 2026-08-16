"""Applying to be a partner, from the customer's side.

Deliberately open to any member rather than admin-gated. Asking is not
spending, granting anything, or binding the account to anything — the answer
is a staff decision either way — and the person at an agency who notices we
have a partner programme is rarely the one holding the billing profile.

The staff half lives in `partner_admin.py`, behind `get_superuser`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import PartnerKind
from api.services.auth.depends import get_user
from api.services.partners import applications as partners
from api.services.partners.applications import PartnerError

router = APIRouter(prefix="/partners", tags=["partners"])


class PartnerApplicationRequest(BaseModel):
    kind: str = Field(..., description="developer, agency or reseller")
    expected_minutes_per_month: int | None = Field(
        None,
        ge=0,
        description="Their own forecast. The number a commission is quoted against.",
    )
    company_website: str | None = None
    note: str | None = Field(
        None, description="Anything the fixed answers cannot carry."
    )


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _view(application) -> dict[str, Any]:
    return {
        "id": application.id,
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
        # Written for the applicant, so it is the one staff-authored field
        # that comes back to them.
        "decision_note": application.decision_note,
    }


@router.get("/application")
async def get_application(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """This account's most recent application, and the arrangement it is on.

    The commission is reported as a percentage and a basis and nothing else —
    a partner may see what they are paid, which is their own contract, and not
    the rate card it is computed against.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        application = await partners.latest_for(session, organization_id)
        commission = await partners.live_commission(session, organization_id)
        return {
            "kinds": [k.value for k in PartnerKind],
            "application": _view(application) if application else None,
            "commission": (
                {
                    "commission_bps": commission.commission_bps,
                    "basis": commission.basis,
                    "effective_from": commission.effective_from.isoformat(),
                }
                if commission
                else None
            ),
        }


@router.post("/application")
async def submit_application(
    request: PartnerApplicationRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Ask to be treated as a partner."""
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        try:
            application = await partners.submit(
                session,
                organization_id=organization_id,
                user_id=user.id,
                kind=request.kind,
                expected_minutes_per_month=request.expected_minutes_per_month,
                note=request.note,
                company_website=request.company_website,
            )
        except PartnerError as exc:
            # The service writes these for the person who will read them.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return {"application": _view(application)}

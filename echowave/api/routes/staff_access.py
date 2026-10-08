"""The customer's side of call-content consent (phase 3, `staff`).

A workspace owner or admin allows Decibyl staff to read one call's
transcript and recording -- or every call's -- for up to 30 days, and can
take it back at any time. Staff cannot make a grant; this is the only
writer. See ``services/staff/call_content.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.staff import call_content

router = APIRouter(prefix="/organizations/staff-access", tags=["organization-members"])


class GrantRequest(BaseModel):
    #: One call, or omitted for every call in the workspace.
    workflow_run_id: int | None = Field(default=None, gt=0)
    days: int = Field(default=7, ge=1, le=call_content.MAX_DAYS)
    reason: str | None = Field(default=None, max_length=300)


@router.get("")
async def list_staff_access(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Every permission this workspace has given staff, newest first."""
    async with db_client.async_session() as session:
        return {
            "grants": await call_content.list_grants(
                session, user.selected_organization_id
            )
        }


@router.post("", status_code=201)
async def grant_staff_access(
    request: GrantRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            return await call_content.grant(
                session,
                organization_id=user.selected_organization_id,
                user_id=user.id,
                workflow_run_id=request.workflow_run_id,
                days=request.days,
                reason=request.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except call_content.GrantRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{grant_id}")
async def revoke_staff_access(
    grant_id: int,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        try:
            return await call_content.revoke(
                session,
                organization_id=user.selected_organization_id,
                user_id=user.id,
                grant_id=grant_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

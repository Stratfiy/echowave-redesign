"""The staff side of launch stream `controls`: read-only checklists and the
one staff mutation, granting a person a temporary allowance.

Any staff tier (support or superadmin): support is who a person asks for a
higher limit, and every grant carries a reason and an expiry and writes the
staff audit log (services/quotas.grant).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import capabilities, features, feedback, quotas
from api.services.auth.depends import get_staff
from api.services.events import catalogue

router = APIRouter(
    prefix="/admin/controls",
    tags=["admin-controls"],
    dependencies=[Depends(get_staff)],
)


@router.get(
    "/capabilities",
    dependencies=[Depends(features.require("capability_checklist"))],
)
async def capability_checklist(organization_id: int | None = None) -> dict[str, Any]:
    """Each capability's source, configuration and tested status, and its
    honest state. With ``organization_id``, flags as that account sees them."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "capabilities": capabilities.checklist(organization_id),
    }


@router.get(
    "/events/catalogue",
    dependencies=[Depends(features.require("event_catalogue"))],
)
async def event_catalogue() -> dict[str, Any]:
    return {"events": catalogue.as_rows()}


@router.get(
    "/feedback/summary",
    dependencies=[Depends(features.require("reply_feedback"))],
)
async def feedback_summary(organization_id: int | None = None) -> dict[str, Any]:
    return await feedback.summary(organization_id)


_quota_flag = Depends(features.require("operational_quotas"))


@router.get("/quotas/users/{user_id}", dependencies=[_quota_flag])
async def user_quotas(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "allowances": await quotas.status(user_id),
        "grants": await quotas.grants(user_id),
    }


class GrantWrite(BaseModel):
    kind: str = Field(max_length=32)
    extra: int = Field(gt=0, le=quotas.MAX_GRANT_EXTRA)
    reason: str = Field(min_length=5, max_length=500)
    #: How long it lasts, in hours, from now.
    hours: int = Field(gt=0, le=quotas.MAX_GRANT_DAYS * 24)

    model_config = ConfigDict(extra="forbid")


@router.post(
    "/quotas/users/{user_id}/grants", status_code=201, dependencies=[_quota_flag]
)
async def grant_allowance(
    user_id: int,
    body: GrantWrite,
    staff: Annotated[UserModel, Depends(get_staff)],
) -> dict[str, Any]:
    try:
        row = await quotas.grant(
            user_id=user_id,
            kind=body.kind,
            extra=body.extra,
            reason=body.reason,
            expires_at=datetime.now(UTC) + timedelta(hours=body.hours),
            granted_by=staff.id,
        )
    except (quotas.GrantRefused, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "extra": row.extra,
        "reason": row.reason,
        "expires_at": row.expires_at.isoformat(),
    }


@router.delete(
    "/quotas/grants/{allowance_id}", status_code=204, dependencies=[_quota_flag]
)
async def revoke_allowance(
    allowance_id: int, staff: Annotated[UserModel, Depends(get_staff)]
) -> None:
    if not await quotas.revoke(allowance_id=allowance_id, revoked_by=staff.id):
        raise HTTPException(status_code=404, detail="No live grant with that id.")

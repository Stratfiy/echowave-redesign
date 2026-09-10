"""The bell.

Read by any member of the organization: a notice about the account is for
the account. Marking read is per organization too, not per person — the
inbox is shared, and a warning one colleague has dealt with is dealt with.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.notifications import inbox

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _organization_id(user: UserModel) -> int:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


class MarkReadRequest(BaseModel):
    #: Omit to mark everything read.
    ids: list[int] | None = None


@router.get("")
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    items = await inbox.list_items(organization_id=organization_id, limit=limit)
    return {
        "items": [item.as_dict() for item in items],
        "unread": await inbox.unread_count(organization_id=organization_id),
    }


@router.post("/read")
async def mark_notifications_read(
    request: MarkReadRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    marked = await inbox.mark_read(organization_id=organization_id, ids=request.ids)
    return {
        "marked": marked,
        "unread": await inbox.unread_count(organization_id=organization_id),
    }

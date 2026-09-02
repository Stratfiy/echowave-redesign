"""Rings on callback-mode numbers, and what happened to each.

Scoped to the caller's own organization on every route: a missed call carries
the phone number of a member of the public who rang someone else's business.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/missed-calls", tags=["telephony"])


class MissedCallOut(BaseModel):
    id: int
    caller: str
    received_at: datetime
    #: pending | called_back | refused | failed
    outcome: str
    refusal_reason: str | None
    workflow_run_id: int | None
    telephony_phone_number_id: int


@router.get("", response_model=list[MissedCallOut])
async def list_missed_calls(
    user: UserModel = Depends(get_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows = await db_client.list_missed_calls(
        user.selected_organization_id, limit=limit, offset=offset
    )
    return [
        MissedCallOut(
            id=r.id,
            caller=r.caller,
            received_at=r.received_at,
            outcome=r.outcome,
            refusal_reason=r.refusal_reason,
            workflow_run_id=r.workflow_run_id,
            telephony_phone_number_id=r.telephony_phone_number_id,
        )
        for r in rows
    ]

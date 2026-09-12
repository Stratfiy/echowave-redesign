"""What each version of an agent actually achieved.

The number the product is sold on, and until now the one thing nobody could
see. "We changed the prompt and bookings fell" was a story somebody told;
this is where it becomes a number somebody checks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/workflow", tags=["workflow-outcomes"])


class VersionOutcome(BaseModel):
    definition_id: Optional[int]
    version_number: Optional[int]
    published_at: Optional[datetime]
    calls: int
    calls_with_outcome: int
    #: Null rather than zero for a version nobody has run. "Nobody ran it" and
    #: "it fails" are different claims and must not look the same on a screen.
    outcome_rate: Optional[float]


class OutcomeRateResponse(BaseModel):
    versions: list[VersionOutcome]


@router.get("/{workflow_id}/outcome-rate", response_model=OutcomeRateResponse)
async def outcome_rate(
    workflow_id: int,
    days: int = Query(default=30, ge=1, le=365),
    user: UserModel = Depends(get_user),
) -> OutcomeRateResponse:
    """Calls and outcomes for each published version of one agent.

    A call counts as having produced an outcome when at least one of its
    actions reached an outside app and succeeded -- the product's own claim
    that a call is finished when the record exists, so the metric and the pitch
    are the same sentence.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Ownership before measurement: an id in the path proves nothing, and the
    # answer would otherwise describe somebody else's agent.
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    rows = await db_client.outcomes_by_version(
        organization_id=organization_id, workflow_id=workflow_id, days=days
    )
    return OutcomeRateResponse(versions=[VersionOutcome(**row) for row in rows])

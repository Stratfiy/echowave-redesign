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
from api.services.integrations.composio.client import connected_toolkits
from api.services.workflow import readiness

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


class ReadinessItem(BaseModel):
    app: str
    label: str
    #: "ready" | "missing" | "failing". Three states rather than a tick box,
    #: because "never connected" and "stopped working on Tuesday" mean the same
    #: thing to the business and different things to whoever fixes it.
    status: str
    needed_by: list[str]
    recent_failures: int
    #: False for the customer's own HTTP endpoint, which we cannot connect for
    #: them -- listing it without a button is still right, since an agent that
    #: depends on three things should not appear to depend on two.
    connectable: bool


class ReadinessResponse(BaseModel):
    ready: bool
    items: list[ReadinessItem]


@router.get("/{workflow_id}/readiness", response_model=ReadinessResponse)
async def agent_readiness(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> ReadinessResponse:
    """What this agent still needs before it can do its job.

    Not a setup wizard. A wizard is finished once and then lies -- a token
    revoked three months later leaves a green tick over an agent that has
    silently stopped filing anything. This is read live, every time, so the
    same list answers "what is left to set up" and "why did it stop working".
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    definition = await db_client.get_published_definition(workflow_id, organization_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    uuids = readiness.required_tool_uuids(
        definition.workflow_json, definition.workflow_configurations
    )
    tools = await db_client.get_tools_by_uuids(uuids, organization_id) if uuids else []

    connected = set(await connected_toolkits(organization_id))
    summary = await db_client.app_interaction_summary(
        organization_id=organization_id, days=7
    )
    failures = {
        row["app"]: row["errors"]
        for row in summary
        if row.get("app") and row.get("errors")
    }

    items = readiness.build_checklist(
        tools=tools, connected_apps=connected, failures_by_app=failures
    )
    return ReadinessResponse(
        ready=all(item["status"] == readiness.STATUS_READY for item in items),
        items=[ReadinessItem(**item) for item in items],
    )

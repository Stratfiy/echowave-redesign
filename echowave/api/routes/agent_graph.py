"""What the agent graph shows beyond its steps (G-1): what starts the agent,
and which steps its last run reached. Read-only and thin; a 404 while
AGENT_GRAPH_EXTRAS_ENABLED is off, and a 404 for an agent that is not the
caller's organization's.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import require_organization_role
from api.services.workflow import graph_extras

router = APIRouter(prefix="/agent-graph", tags=["agent-graph"])


def _enabled() -> None:
    if not graph_extras.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


_member = require_organization_role(OrganizationRole.MEMBER)


@router.get("/{workflow_id}/starts", dependencies=[Depends(_enabled)])
async def agent_starts(
    workflow_id: int, user: UserModel = Depends(_member)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await graph_extras.starts(
            session,
            organization_id=user.selected_organization_id,
            workflow_id=workflow_id,
        )
    if found is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"starts": found}


@router.get("/{workflow_id}/last-run", dependencies=[Depends(_enabled)])
async def agent_last_run(
    workflow_id: int, user: UserModel = Depends(_member)
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        found = await graph_extras.last_run(
            session,
            organization_id=user.selected_organization_id,
            workflow_id=workflow_id,
        )
    if found is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return found

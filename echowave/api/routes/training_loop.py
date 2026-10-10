"""The training loop's surfaces: the setting, the agent page's counts, and the
export of a workspace's own training data.

Thin, as routes are: the workspace comes from the session, never from the
request. Everything here is a 404 while ``training_loop`` is off for the
workspace.

* ``PUT /training-loop/settings`` -- "Use my feedback to improve my agents"
  (workspace admins and owners). Off is one of two choices: "Stop collecting"
  (keeps the past) or "Stop and delete" (``delete_past``: archives the past).
* ``GET /training-loop/settings`` -- what it is now (any member).
* ``GET /training-loop/agents/{workflow_id}/summary`` -- the Learning line.
  The agent is read through the workspace; another workspace's is a 404.
* ``GET /training-loop/export`` -- the workspace's data as JSONL (owners).
* ``GET /admin/training-loop/{organization_id}/export`` -- the same for one
  named workspace, for staff (top tier only), written to both audit logs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services import features
from api.services.auth.depends import (
    get_superuser,
    get_user_with_selected_organization,
    require_organization_role,
)
from api.services.training_loop import (
    ARCHIVE_RETENTION_DAYS,
    FLAG,
    consent,
    export,
    summary,
)

router = APIRouter(
    prefix="/training-loop",
    tags=["training-loop"],
    dependencies=[Depends(features.require(FLAG, per_organization=True))],
)

#: Staff reach one workspace at a time, by name, and only on the top tier.
admin_router = APIRouter(prefix="/admin/training-loop", tags=["admin-training-loop"])

NDJSON = "application/x-ndjson"


class TrainingLoopSettings(BaseModel):
    use_feedback: bool
    #: Rows archived by "Stop and delete", kept only as the law requires.
    archived: int = 0
    #: How long archived rows are kept, in days.
    retention_days: int = ARCHIVE_RETENTION_DAYS


class TrainingLoopSettingsUpdate(BaseModel):
    use_feedback: bool
    #: "Stop and delete": archive the rows already kept. Only with
    #: ``use_feedback`` off; "Stop collecting" leaves them as they are.
    delete_past: bool = False


class TrainingLoopSummary(BaseModel):
    approved_this_week: int
    rejected_this_week: int
    use_feedback: bool


def _organization_id(user: UserModel) -> int:
    return int(user.selected_organization_id)


@router.get("/settings", response_model=TrainingLoopSettings)
async def get_settings(
    user: Annotated[UserModel, Depends(get_user_with_selected_organization)],
):
    organization_id = _organization_id(user)
    return TrainingLoopSettings(
        use_feedback=await consent.use_feedback(organization_id),
        archived=await db_client.count_archived_learning_events(organization_id),
    )


@router.put("/settings", response_model=TrainingLoopSettings)
async def set_settings(
    body: TrainingLoopSettingsUpdate,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
):
    """Switch "Use my feedback to improve my agents". Off, nothing new is
    kept. "Stop collecting" leaves what was kept; ``delete_past`` ("Stop and
    delete") archives it: out of export and training, kept only as the law
    requires, never hard-deleted."""
    if body.use_feedback and body.delete_past:
        raise HTTPException(
            status_code=422, detail="Only turning it off can delete what was kept."
        )
    organization_id = _organization_id(user)
    await consent.set_use_feedback(
        organization_id=organization_id,
        user=user,
        allowed=body.use_feedback,
        delete_past=body.delete_past,
    )
    return TrainingLoopSettings(
        use_feedback=body.use_feedback,
        archived=await db_client.count_archived_learning_events(organization_id),
    )


@router.get("/agents/{workflow_id}/summary", response_model=TrainingLoopSummary)
async def agent_summary(
    workflow_id: int,
    user: Annotated[UserModel, Depends(get_user_with_selected_organization)],
):
    organization_id = _organization_id(user)
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return TrainingLoopSummary(
        **await summary.agent_summary(
            organization_id=organization_id, workflow_id=workflow_id
        )
    )


def _ndjson(result: export.Export, organization_id: int) -> Response:
    return Response(
        content=result.body(),
        media_type=NDJSON,
        headers={
            "Content-Disposition": (
                f'attachment; filename="training-{result.shape}-org{organization_id}.jsonl"'
            ),
            "X-Export-Truncated": "true" if result.truncated else "false",
            "X-Export-Rows": str(len(result.lines)),
            "Cache-Control": "no-store",
        },
    )


@router.get("/export")
async def export_mine(
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.OWNER))
    ],
    shape: Annotated[str, Query(description="sft, preference or kto")] = export.SFT,
    agent_id: Annotated[int | None, Query()] = None,
):
    """The workspace's training data as JSONL, in one of three shapes."""
    organization_id = _organization_id(user)
    if shape not in export.SHAPES:
        raise HTTPException(
            status_code=422, detail="shape must be sft, preference or kto"
        )
    if agent_id is not None and (
        await db_client.get_workflow(agent_id, organization_id=organization_id) is None
    ):
        raise HTTPException(status_code=404, detail="Agent not found")
    result = await export.build(
        organization_id=organization_id, shape=shape, workflow_id=agent_id
    )
    return _ndjson(result, organization_id)


@admin_router.get("/{organization_id}/export")
async def export_for_staff(
    organization_id: int,
    staff: Annotated[UserModel, Depends(get_superuser)],
    shape: Annotated[str, Query(description="sft, preference or kto")] = export.SFT,
    agent_id: Annotated[int | None, Query()] = None,
):
    """One workspace's training data, for staff. The workspace's own switch
    and the feature flag apply exactly as they do for the owner, and the
    export is written to the staff audit log and to the workspace's own."""
    if not features.is_on(FLAG, organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    if shape not in export.SHAPES:
        raise HTTPException(
            status_code=422, detail="shape must be sft, preference or kto"
        )
    if await db_client.get_organization_by_id(organization_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if agent_id is not None and (
        await db_client.get_workflow(agent_id, organization_id=organization_id) is None
    ):
        raise HTTPException(status_code=404, detail="Agent not found")
    result = await export.build(
        organization_id=organization_id, shape=shape, workflow_id=agent_id
    )
    await export.note_staff_export(
        organization_id=organization_id,
        staff=staff,
        shape=shape,
        rows=len(result.lines),
    )
    return _ndjson(result, organization_id)

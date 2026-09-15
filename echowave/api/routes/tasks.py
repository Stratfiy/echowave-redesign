"""The task board over HTTP (KAN-140 P1). Org-scoped from the user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.workflow import tasks_board

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class TaskWrite(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=4_000)
    #: A bot's @handle, or "team".
    assignee: str = Field(default="team", max_length=80)
    due: str | None = Field(default=None, max_length=40)

    model_config = ConfigDict(extra="forbid")


class TaskStatus(BaseModel):
    status: str = Field(min_length=1, max_length=16)
    result: str | None = Field(default=None, max_length=2_000)

    model_config = ConfigDict(extra="forbid")


@router.get("")
async def list_tasks(user: Annotated[UserModel, Depends(get_user)]) -> dict[str, Any]:
    organization_id = _organization_id(user)
    rows = await db_client.tasks_for_organization(organization_id)
    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    names = {w.id: w.name for w in roster}
    return {
        "tasks": [tasks_board.as_dict(t, names) for t in rows],
        "statuses": list(tasks_board.STATUSES),
        "bots": [
            {"id": w.id, "name": w.name, "handle": getattr(w, "handle", None)}
            for w in roster
        ],
    }


@router.post("", status_code=201)
async def create_task(
    body: TaskWrite, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    result = await tasks_board.create(
        organization_id=organization_id,
        from_workflow_id=None,
        workflow_run_id=None,
        arguments=body.model_dump(),
        created_by=user.id,
    )
    if result.get("status") != "filed":
        raise HTTPException(
            status_code=400, detail=result.get("reason") or "Could not file it."
        )
    task = await db_client.get_task(
        int(result["task_id"]), organization_id=organization_id
    )
    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    return tasks_board.as_dict(task, {w.id: w.name for w in roster})


@router.post("/{task_id}/status")
async def set_task_status(
    task_id: int, body: TaskStatus, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    try:
        return await tasks_board.set_status(
            organization_id=organization_id,
            task_id=task_id,
            status=body.status,
            result=body.result,
            user_id=user.id,
        )
    except tasks_board.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    organization_id = _organization_id(user)
    if not await db_client.delete_task(task_id, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="That task is not here.")

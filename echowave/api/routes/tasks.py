"""The task board over HTTP (KAN-140 P1; TB-1 makes it a board). Org-scoped
from the user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.workflow import tasks_board, visibility

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class TaskWrite(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=4_000)
    #: A bot's @handle, "team", or "user:<id>" for a person (TB-1).
    assignee: str = Field(default="team", max_length=80)
    due: str | None = Field(default=None, max_length=40)
    priority: str | None = Field(default=None, max_length=8)
    parent_id: int | None = None
    blocked_by: list[int] | None = None
    #: TB-3: short words to sort the board by.
    labels: list[str] | None = Field(default=None, max_length=32)
    #: File it into the backlog rather than to do (TB-1).
    backlog: bool = False

    model_config = ConfigDict(extra="forbid")


class TaskEdit(BaseModel):
    priority: str | None = Field(default=None, max_length=8)
    assignee: str | None = Field(default=None, max_length=80)
    parent_id: int | None = None
    blocked_by: list[int] | None = None
    due: str | None = Field(default=None, max_length=40)
    labels: list[str] | None = Field(default=None, max_length=32)

    model_config = ConfigDict(extra="forbid")


class TaskStatus(BaseModel):
    status: str = Field(min_length=1, max_length=16)
    result: str | None = Field(default=None, max_length=2_000)

    model_config = ConfigDict(extra="forbid")


class CommentWrite(BaseModel):
    body: str = Field(min_length=1, max_length=4_000)

    model_config = ConfigDict(extra="forbid")


_person_name = tasks_board.person_name


_context = tasks_board.board_context


@router.get("")
async def list_tasks(user: Annotated[UserModel, Depends(get_user)]) -> dict[str, Any]:
    organization_id = _organization_id(user)
    rows = await db_client.tasks_for_organization(organization_id)
    ctx = await tasks_board.board_context(
        organization_id,
        viewer_role=await visibility.role_of(
            getattr(user, "id", None), organization_id
        ),
        for_person=True,
    )
    counts = await db_client.comment_counts(organization_id, [t.id for t in rows])
    enabled = tasks_board.enabled()
    return {
        "tasks": [
            tasks_board.as_dict(
                t,
                ctx["names"],
                people=ctx["people"],
                prefix=ctx["prefix"],
                comment_count=counts.get(t.id, 0),
            )
            for t in rows
        ],
        "statuses": list(
            tasks_board.STATUSES if enabled else tasks_board.LEGACY_STATUSES
        ),
        "bots": [
            {"id": w.id, "name": w.name, "handle": getattr(w, "handle", None)}
            for w in ctx["roster"]
        ],
        "board": {
            "enabled": enabled,
            "priorities": list(tasks_board.PRIORITIES),
            "prefix": ctx["prefix"],
            # TB-3: every label on the board, for the filter and the picker.
            "labels": tasks_board.board_labels(rows),
            "people": [
                {"id": user_id, "name": name} for user_id, name in ctx["people"].items()
            ],
            "me": user.id,
        },
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
        arguments=body.model_dump(exclude_none=True),
        created_by=user.id,
    )
    if result.get("status") != "filed":
        raise HTTPException(
            status_code=400, detail=result.get("reason") or "Could not file it."
        )
    task = await db_client.get_task(
        int(result["task_id"]), organization_id=organization_id
    )
    ctx = await _context(organization_id)
    return tasks_board.as_dict(
        task, ctx["names"], people=ctx["people"], prefix=ctx["prefix"], comment_count=0
    )


@router.get("/{task_id}")
async def get_task(
    task_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    """One card with its comments (TB-1)."""
    organization_id = _organization_id(user)
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise HTTPException(status_code=404, detail="That task is not here.")
    ctx = await _context(organization_id)
    comments = await db_client.comments_for_task(
        task_id, organization_id=organization_id
    )
    out = tasks_board.as_dict(
        task,
        ctx["names"],
        people=ctx["people"],
        prefix=ctx["prefix"],
        comment_count=len(comments),
    )
    out["comments"] = [
        tasks_board.comment_dict(c, ctx["names"], ctx["people"]) for c in comments
    ]
    # TB-2: the card's own sub-tasks, so its page can list and add them.
    subtasks = await db_client.subtasks_of(task_id, organization_id=organization_id)
    out["subtasks"] = [
        tasks_board.as_dict(t, ctx["names"], people=ctx["people"], prefix=ctx["prefix"])
        for t in subtasks
    ]
    return out


@router.patch("/{task_id}")
async def edit_task(
    task_id: int, body: TaskEdit, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    await tasks_board.edit(
        organization_id=organization_id,
        task_id=task_id,
        changes=body.model_dump(exclude_unset=True),
        user_id=user.id,
    )
    task = await db_client.get_task(task_id, organization_id=organization_id)
    ctx = await _context(organization_id)
    return tasks_board.as_dict(
        task, ctx["names"], people=ctx["people"], prefix=ctx["prefix"]
    )


@router.post("/{task_id}/comments", status_code=201)
async def add_comment(
    task_id: int, body: CommentWrite, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    out = await tasks_board.comment(
        organization_id=organization_id,
        task_id=task_id,
        body=body.body,
        user_id=user.id,
    )
    ctx = await _context(organization_id)
    out["author_name"] = ctx["people"].get(user.id)
    return out


@router.post("/{task_id}/status")
async def set_task_status(
    task_id: int, body: TaskStatus, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    return await tasks_board.set_status(
        organization_id=organization_id,
        task_id=task_id,
        status=body.status,
        result=body.result,
        user_id=user.id,
        actor_name=_person_name(user),
    )


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    organization_id = _organization_id(user)
    if not await db_client.delete_task(task_id, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="That task is not here.")

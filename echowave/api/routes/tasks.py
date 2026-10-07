"""The task board over HTTP (KAN-140 P1; TB-1 makes it a board). Org-scoped
from the user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.workflow import task_ledger, tasks_board, visibility

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
    out = await tasks_board.set_status(
        organization_id=organization_id,
        task_id=task_id,
        status=body.status,
        result=body.result,
        user_id=user.id,
        actor_name=_person_name(user),
    )
    # The ledger reads the same move in its own words (no-op while off).
    await task_ledger.follow_board(
        organization_id=organization_id,
        task_id=task_id,
        status=body.status,
        user_id=user.id,
    )
    return out


# --- the task ledger (launch stream controls) -------------------------------

_ledger_flag = Depends(features.require("task_ledger", per_organization=True))


class LedgerTaskWrite(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=4_000)
    #: queued | scheduled | awaiting_approval | needs_input.
    state: str = Field(default=task_ledger.QUEUED, max_length=24)
    due: str | None = Field(default=None, max_length=40)

    model_config = ConfigDict(extra="forbid")


class LedgerMove(BaseModel):
    to: str = Field(min_length=1, max_length=24)
    #: The version the client read. A stale one is a 409, nothing changes.
    expected_version: int = Field(ge=0)
    reason_code: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9_]*$"
    )
    #: What proves the outcome, required to complete: a message id, a link.
    evidence: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


def _ledger_error(exc: task_ledger.LedgerError) -> HTTPException:
    if isinstance(exc, task_ledger.StaleState):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "current_version": exc.current,
                "current_state": exc.current_state,
            },
        )
    if isinstance(exc, task_ledger.NotAllowed):
        return HTTPException(status_code=409, detail=str(exc))
    if str(exc) == "That task is not here.":
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/ledger", status_code=201, dependencies=[_ledger_flag])
async def create_ledger_task(
    body: LedgerTaskWrite,
    user: Annotated[UserModel, Depends(get_user)],
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> dict[str, Any]:
    """File a task in the ledger. Send an ``Idempotency-Key`` header: a
    retry with the same key returns the task the first attempt made."""
    organization_id = _organization_id(user)
    try:
        task, created = await task_ledger.create(
            organization_id=organization_id,
            title=body.title,
            brief=body.brief,
            created_by=user.id,
            idempotency_key=idempotency_key,
            state=body.state,
        )
    except task_ledger.LedgerError as exc:
        raise _ledger_error(exc) from exc
    return {**task_ledger.as_dict(task), "created": created}


@router.get("/{task_id}/ledger", dependencies=[_ledger_flag])
async def ledger_of_task(
    task_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    """The task's state, version and every move it has made, in order."""
    organization_id = _organization_id(user)
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise HTTPException(status_code=404, detail="That task is not here.")
    return {
        **task_ledger.as_dict(task),
        "history": await task_ledger.history(organization_id, task_id),
    }


@router.post("/{task_id}/ledger/transition", dependencies=[_ledger_flag])
async def move_ledger_task(
    task_id: int, body: LedgerMove, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    try:
        task = await task_ledger.transition(
            organization_id=organization_id,
            task_id=task_id,
            to_state=body.to,
            expected_version=body.expected_version,
            actor_user_id=user.id,
            reason_code=body.reason_code,
            evidence=body.evidence,
        )
    except task_ledger.LedgerError as exc:
        raise _ledger_error(exc) from exc
    return task_ledger.as_dict(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    organization_id = _organization_id(user)
    if not await db_client.delete_task(task_id, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="That task is not here.")

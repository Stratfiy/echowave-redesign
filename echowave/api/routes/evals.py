"""Scripted callers an agent is rerun against after every edit.

Vapi and Retell both have this, and it is why people trust their prompt
edits: ten callers you wrote once, rerun in a minute, red or green. Each
run is a text session on the agent — billed like one, no phone minute —
and the judge is the agent's own model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.db import db_client
from api.db.models import EvalCaseModel, EvalResultModel, UserModel
from api.enums import PostHogEvent
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/workflow/{workflow_id}/evals", tags=["evals"])

MAX_CASES = 50


class EvalCaseRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    persona: str = Field(..., min_length=1, max_length=2000)
    goal: str = Field(..., min_length=1, max_length=2000)
    must_say: list[str] = Field(default_factory=list, max_length=20)
    must_not_say: list[str] = Field(default_factory=list, max_length=20)
    max_turns: int = Field(6, ge=1, le=12)


def _org(user: UserModel) -> int:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


async def _workflow_or_404(workflow_id: int, organization_id: int):
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return workflow


def _result_dict(row: EvalResultModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "verdict": row.verdict,
        "transcript": row.transcript or [],
        "workflow_run_id": row.workflow_run_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _case_dict(case: EvalCaseModel, latest: EvalResultModel | None) -> dict[str, Any]:
    return {
        "id": case.id,
        "name": case.name,
        "persona": case.persona,
        "goal": case.goal,
        "must_say": list(case.must_say or []),
        "must_not_say": list(case.must_not_say or []),
        "max_turns": case.max_turns,
        "latest": _result_dict(latest),
    }


@router.get("")
async def list_cases(
    workflow_id: int, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _org(user)
    await _workflow_or_404(workflow_id, organization_id)
    async with db_client.async_session() as session:
        cases = (
            (
                await session.execute(
                    select(EvalCaseModel)
                    .where(
                        EvalCaseModel.workflow_id == workflow_id,
                        EvalCaseModel.organization_id == organization_id,
                    )
                    .order_by(EvalCaseModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        latest: dict[int, EvalResultModel] = {}
        if cases:
            rows = (
                (
                    await session.execute(
                        select(EvalResultModel)
                        .where(EvalResultModel.case_id.in_([c.id for c in cases]))
                        .order_by(EvalResultModel.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                latest.setdefault(row.case_id, row)
    items = [_case_dict(c, latest.get(c.id)) for c in cases]
    return {
        "cases": items,
        "passed": sum(
            1 for i in items if (i["latest"] or {}).get("status") == "passed"
        ),
        "failed": sum(
            1 for i in items if (i["latest"] or {}).get("status") in ("failed", "error")
        ),
        "running": sum(
            1
            for i in items
            if (i["latest"] or {}).get("status") in ("queued", "running")
        ),
    }


@router.post("")
async def create_case(
    workflow_id: int, request: EvalCaseRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _org(user)
    await _workflow_or_404(workflow_id, organization_id)
    async with db_client.async_session() as session:
        count = len(
            (
                await session.execute(
                    select(EvalCaseModel.id).where(
                        EvalCaseModel.workflow_id == workflow_id
                    )
                )
            ).all()
        )
        if count >= MAX_CASES:
            raise HTTPException(
                status_code=400, detail=f"An agent can have {MAX_CASES} cases."
            )
        case = EvalCaseModel(
            organization_id=organization_id,
            workflow_id=workflow_id,
            name=request.name.strip(),
            persona=request.persona.strip(),
            goal=request.goal.strip(),
            must_say=[p.strip() for p in request.must_say if p.strip()],
            must_not_say=[p.strip() for p in request.must_not_say if p.strip()],
            max_turns=request.max_turns,
            created_by=user.id,
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        out = _case_dict(case, None)
    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.EVAL_CASE_CREATED,
        properties={"organization_id": organization_id, "workflow_id": workflow_id},
    )
    return out


@router.delete("/{case_id}")
async def delete_case(
    workflow_id: int, case_id: int, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _org(user)
    async with db_client.async_session() as session:
        case = await session.get(EvalCaseModel, case_id)
        if (
            case is None
            or case.workflow_id != workflow_id
            or case.organization_id != organization_id
        ):
            raise HTTPException(status_code=404, detail="Case not found")
        await session.delete(case)
        await session.commit()
    return {"deleted": True}


class RunRequest(BaseModel):
    #: Omit to run every case.
    case_ids: list[int] | None = None


@router.post("/run")
async def run_cases(
    workflow_id: int,
    request: RunRequest | None = None,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    """Queue a run per case. The worker drives each; the list shows them land."""
    organization_id = _org(user)
    await _workflow_or_404(workflow_id, organization_id)
    wanted = set(request.case_ids) if request and request.case_ids else None
    async with db_client.async_session() as session:
        cases = (
            (
                await session.execute(
                    select(EvalCaseModel).where(
                        EvalCaseModel.workflow_id == workflow_id,
                        EvalCaseModel.organization_id == organization_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        cases = [c for c in cases if wanted is None or c.id in wanted]
        if not cases:
            raise HTTPException(
                status_code=400, detail="No cases to run. Add one first."
            )
        results = [
            EvalResultModel(
                case_id=c.id,
                organization_id=organization_id,
                workflow_id=workflow_id,
                status="queued",
            )
            for c in cases
        ]
        session.add_all(results)
        await session.commit()
        ids = [r.id for r in results]
    for result_id in ids:
        await enqueue_job(FunctionNames.RUN_EVAL_CASE, result_id)
    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.EVAL_RUN_STARTED,
        properties={
            "organization_id": organization_id,
            "workflow_id": workflow_id,
            "cases": len(ids),
        },
    )
    return {"queued": len(ids), "result_ids": ids, "at": datetime.now(UTC).isoformat()}

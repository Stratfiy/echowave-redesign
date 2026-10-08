"""Activity -> Usage: what each agent ran, on which models, and what it cost.

Read from the run receipts (``call_cost_items``), the same lines a call's own
receipt shows, grouped twice: by agent, and by model within each agent. Every
kind of run is in it -- calls, chats, routines, triggers -- because a receipt
is written for each, and ``token_report.work_kind`` says which kind it was.

Scoped through the run's agent: a run has no organisation of its own.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallCostItemModel, WorkflowModel, WorkflowRunModel
from api.enums import CostComponent
from api.services.billing.token_report import TOKEN_COMPONENTS, work_kind

#: Components that are a model at work, and the slot each is shown under.
_SLOT = {
    **{component: "llm" for component in TOKEN_COMPONENTS},
    CostComponent.LLM.value: "llm",
    CostComponent.STT.value: "stt",
    CostComponent.TTS.value: "tts",
    CostComponent.EMBEDDING.value: "embeddings",
}
_TOKENS = set(TOKEN_COMPONENTS) | {CostComponent.LLM.value}


async def by_agent(
    session: AsyncSession,
    *,
    organization_id: int,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Runs, tokens, speech and cost per agent, with a per-model split."""
    runs_q = (
        select(
            WorkflowRunModel.workflow_id,
            WorkflowRunModel.mode,
            func.count(WorkflowRunModel.id),
        )
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.created_at >= start,
            WorkflowRunModel.created_at < end,
        )
        .group_by(WorkflowRunModel.workflow_id, WorkflowRunModel.mode)
    )
    items_q = (
        select(
            WorkflowRunModel.workflow_id,
            CallCostItemModel.component,
            CallCostItemModel.provider,
            CallCostItemModel.model,
            func.sum(CallCostItemModel.units),
            func.sum(CallCostItemModel.cost_paise),
        )
        .join(
            WorkflowRunModel, WorkflowRunModel.id == CallCostItemModel.workflow_run_id
        )
        .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
        .where(
            WorkflowModel.organization_id == organization_id,
            WorkflowRunModel.created_at >= start,
            WorkflowRunModel.created_at < end,
        )
        .group_by(
            WorkflowRunModel.workflow_id,
            CallCostItemModel.component,
            CallCostItemModel.provider,
            CallCostItemModel.model,
        )
    )
    names_q = select(WorkflowModel.id, WorkflowModel.name).where(
        WorkflowModel.organization_id == organization_id
    )

    names = dict((await session.execute(names_q)).all())
    agents: dict[int, dict[str, Any]] = {}

    def agent(workflow_id: int) -> dict[str, Any]:
        return agents.setdefault(
            workflow_id,
            {
                "workflow_id": workflow_id,
                "name": names.get(workflow_id) or f"Agent {workflow_id}",
                "runs": defaultdict(int),
                "tokens": 0,
                "cost_paise": 0,
                "models": {},
            },
        )

    for workflow_id, mode, count in (await session.execute(runs_q)).all():
        agent(workflow_id)["runs"][work_kind(mode)] += int(count or 0)

    for workflow_id, component, provider, model, units, cost in (
        await session.execute(items_q)
    ).all():
        row = agent(workflow_id)
        cost = int(cost or 0)
        row["cost_paise"] += cost
        slot = _SLOT.get(component)
        if slot is None:
            # Telephony, platform and the like: part of the cost, not a model.
            continue
        key = f"{slot}:{provider or ''}:{model or ''}"
        line = row["models"].setdefault(
            key,
            {
                "slot": slot,
                "provider": provider or "",
                "model": model or "",
                "tokens": 0,
                "units": 0,
                "cost_paise": 0,
            },
        )
        line["cost_paise"] += cost
        if component in _TOKENS:
            line["tokens"] += int(units or 0)
            row["tokens"] += int(units or 0)
        else:
            line["units"] += int(units or 0)

    out = []
    for row in agents.values():
        runs = dict(row["runs"])
        out.append(
            {
                **row,
                "runs": runs,
                "total_runs": sum(runs.values()),
                "models": sorted(
                    row["models"].values(), key=lambda m: m["cost_paise"], reverse=True
                ),
            }
        )
    out.sort(key=lambda a: (a["cost_paise"], a["total_runs"]), reverse=True)

    models: dict[str, dict[str, Any]] = {}
    for row in out:
        for line in row["models"]:
            key = f"{line['slot']}:{line['provider']}:{line['model']}"
            total = models.setdefault(
                key, {**line, "tokens": 0, "units": 0, "cost_paise": 0, "agents": 0}
            )
            total["tokens"] += line["tokens"]
            total["units"] += line["units"]
            total["cost_paise"] += line["cost_paise"]
            total["agents"] += 1

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "agents": out,
        "models": sorted(models.values(), key=lambda m: m["cost_paise"], reverse=True),
        "totals": {
            "runs": sum(a["total_runs"] for a in out),
            "tokens": sum(a["tokens"] for a in out),
            "cost_paise": sum(a["cost_paise"] for a in out),
        },
    }

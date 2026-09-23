"""What the agent graph shows beyond its steps (G-1).

Two read-only answers for one agent in one organization:

* **What starts it.** An agent's graph began at "Start Call" whether a phone
  rang, a routine fired, a webhook arrived or somebody opened the web chat --
  the one fact an owner most needs, and it lived on four other screens. Here
  it is gathered from the rows that actually start the agent: inbound numbers,
  routines, bot triggers, active web widgets, and campaigns still running.
  Nothing is inferred from the graph itself.
* **What the last run reached.** Read from the run's own record of node
  transitions (``logs.realtime_feedback_events``), falling back to the node
  names in ``gathered_context.nodes_visited`` for runs recorded before those
  events were kept. Both are returned, so the canvas can mark a step by id or,
  failing that, by name.

Every query filters by ``organization_id``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentRoutineModel,
    BotTriggerModel,
    CampaignModel,
    EmbedTokenModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services import features

NODE_TRANSITION = "rtf-node-transition"
#: Campaigns that can still start a call.
_LIVE_CAMPAIGN = ("created", "syncing", "running", "paused")


def enabled() -> bool:
    return features.is_on("agent_graph_extras")


async def _owned(session: AsyncSession, organization_id: int, workflow_id: int) -> bool:
    return (
        await session.scalar(
            select(WorkflowModel.id).where(
                WorkflowModel.id == workflow_id,
                WorkflowModel.organization_id == organization_id,
            )
        )
        is not None
    )


def _routine_words(routine: AgentRoutineModel) -> str:
    try:
        from api.services.workflow.routines import describe, spec_from_model

        return describe(spec_from_model(routine))
    except Exception:  # an unreadable schedule is still a routine
        return routine.cadence or "on a schedule"


async def starts(
    session: AsyncSession, *, organization_id: int, workflow_id: int
) -> list[dict[str, Any]] | None:
    """Everything that starts this agent, or None if it is not this
    organization's."""
    if not await _owned(session, organization_id, workflow_id):
        return None
    found: list[dict[str, Any]] = []

    numbers = await session.scalars(
        select(TelephonyPhoneNumberModel).where(
            TelephonyPhoneNumberModel.organization_id == organization_id,
            TelephonyPhoneNumberModel.inbound_workflow_id == workflow_id,
        )
    )
    for number in numbers:
        found.append(
            {
                "kind": "phone",
                "label": f"A call to {number.address}",
                "detail": number.label,
                "active": True,
            }
        )

    routines = await session.scalars(
        select(AgentRoutineModel).where(
            AgentRoutineModel.organization_id == organization_id,
            AgentRoutineModel.workflow_id == workflow_id,
        )
    )
    for routine in routines:
        found.append(
            {
                "kind": "routine",
                "label": routine.name,
                "detail": _routine_words(routine),
                "active": bool(routine.is_active),
            }
        )

    triggers = await session.scalars(
        select(BotTriggerModel).where(
            BotTriggerModel.organization_id == organization_id,
            BotTriggerModel.workflow_id == workflow_id,
        )
    )
    for trigger in triggers:
        found.append(
            {
                "kind": "trigger",
                "label": trigger.name,
                "detail": trigger.sentence or trigger.source,
                "active": bool(trigger.is_active),
            }
        )

    widgets = list(
        await session.scalars(
            select(EmbedTokenModel.id).where(
                EmbedTokenModel.organization_id == organization_id,
                EmbedTokenModel.workflow_id == workflow_id,
                EmbedTokenModel.is_active.is_(True),
            )
        )
    )
    if widgets:
        found.append(
            {
                "kind": "web",
                "label": "A visitor on your website or share link",
                "detail": None if len(widgets) == 1 else f"{len(widgets)} links",
                "active": True,
            }
        )

    campaigns = await session.scalars(
        select(CampaignModel).where(
            CampaignModel.organization_id == organization_id,
            CampaignModel.workflow_id == workflow_id,
            CampaignModel.state.in_(_LIVE_CAMPAIGN),
        )
    )
    for campaign in campaigns:
        found.append(
            {
                "kind": "campaign",
                "label": f"Campaign: {campaign.name}",
                "detail": campaign.state,
                "active": campaign.state != "paused",
            }
        )
    return found


def _visited(events, nodes_visited) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != NODE_TRANSITION:
            continue
        payload = event.get("payload") or {}
        for key_id, key_name in (
            ("previous_node_id", "previous_node_name"),
            ("node_id", "node_name"),
        ):
            node_id, name = payload.get(key_id), payload.get(key_name)
            if node_id and str(node_id) not in ids:
                ids.append(str(node_id))
            if name and str(name) not in names:
                names.append(str(name))
    for name in nodes_visited or []:
        if name and str(name) not in names:
            names.append(str(name))
    return ids, names


async def last_run(
    session: AsyncSession, *, organization_id: int, workflow_id: int
) -> dict[str, Any] | None:
    """The most recent finished run's path, or ``{"run": None}`` if there is
    none yet; None if the agent is not this organization's."""
    if not await _owned(session, organization_id, workflow_id):
        return None
    # Only the two JSON paths the canvas reads: a run's logs hold the whole
    # transcript and every event, which is far more than a path of steps.
    run = (
        await session.execute(
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.created_at,
                WorkflowRunModel.logs["realtime_feedback_events"].label("events"),
                WorkflowRunModel.gathered_context["nodes_visited"].label("visited"),
            )
            .where(
                WorkflowRunModel.workflow_id == workflow_id,
                WorkflowRunModel.is_completed.is_(True),
            )
            .order_by(WorkflowRunModel.created_at.desc(), WorkflowRunModel.id.desc())
            .limit(1)
        )
    ).first()
    if run is None:
        return {"run": None}
    ids, names = _visited(run.events, run.visited)
    return {
        "run": {
            "id": run.id,
            "at": run.created_at,
            "visited_ids": ids,
            "visited_names": names,
        }
    }


__all__ = ["enabled", "last_run", "starts"]

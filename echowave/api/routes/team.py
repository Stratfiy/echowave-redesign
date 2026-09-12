"""The team: every agent in the organisation and what it has just been doing.

The home screen reads like a list of people rather than a list of
configurations, so this is the one call behind it. One request for the whole
team rather than one per agent: an account with twenty agents would otherwise
open the home screen with twenty round trips, and the screen that decides
whether the product feels alive is the last place to put an N+1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import WorkflowStatus
from api.services.auth.depends import get_user
from api.services.workflow import home_suggestions, status_lines

router = APIRouter(prefix="/team", tags=["team"])


class LastAction(BaseModel):
    label: str
    app: Optional[str]
    status: Optional[str]
    at: Optional[datetime]


class TeamMember(BaseModel):
    workflow_id: int
    workflow_uuid: Optional[str]
    name: str
    is_live: bool
    #: The sentence under the name. Always present -- an agent that did nothing
    #: says so, because a blank line reads as a broken screen.
    status: str
    #: "working" | "idle" | "attention" | "paused", for the dot beside the name.
    tone: str
    at: Optional[datetime]
    calls: int
    answered: int
    outcomes: int
    failures: int
    last_action: Optional[LastAction]


class TeamResponse(BaseModel):
    hours: int
    members: list[TeamMember]


#: Agents needing attention first, then the ones working, then the quiet ones.
#: An owner opening the home screen should not have to scroll to find the agent
#: that has stopped filing bookings.
_TONE_ORDER = {
    status_lines.TONE_ATTENTION: 0,
    status_lines.TONE_WORKING: 1,
    status_lines.TONE_IDLE: 2,
    status_lines.TONE_PAUSED: 3,
}


async def _members(organization_id: int, hours: int) -> list[TeamMember]:
    """The team, sorted worst-first.

    Shared by the team list and the home screen so the two can never disagree
    about what an agent has been doing, and so the home screen costs one
    request rather than two.
    """
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id, status=WorkflowStatus.ACTIVE.value
    )
    activity = await db_client.agent_activity(
        organization_id=organization_id, hours=hours
    )

    members: list[TeamMember] = []
    for workflow in workflows:
        line = status_lines.status_line(
            activity.get(workflow.id),
            is_live=bool(workflow.is_live),
            hours=hours,
        )
        stats = activity.get(workflow.id) or {}
        members.append(
            TeamMember(
                workflow_id=workflow.id,
                workflow_uuid=workflow.workflow_uuid,
                name=workflow.name,
                is_live=bool(workflow.is_live),
                status=line["text"],
                tone=line["tone"],
                at=line["at"],
                calls=int(stats.get("calls") or 0),
                answered=int(stats.get("answered") or 0),
                outcomes=int(stats.get("outcomes") or 0),
                failures=int(stats.get("failures") or 0),
                last_action=(
                    LastAction(**line["last_action"]) if line["last_action"] else None
                ),
            )
        )

    members.sort(
        key=lambda m: (
            _TONE_ORDER.get(m.tone, 9),
            -(m.calls + m.outcomes),
            m.name.lower(),
        )
    )
    return members


@router.get("/status", response_model=TeamResponse)
async def team_status(
    hours: int = Query(default=24, ge=1, le=720),
    user: UserModel = Depends(get_user),
) -> TeamResponse:
    """Every active agent, with one line saying what it has been doing."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return TeamResponse(hours=hours, members=await _members(organization_id, hours))


class Headline(BaseModel):
    """The facts the greeting is built from.

    Facts rather than a finished sentence, because "Good morning" depends on
    the reader's clock and ours is in a data centre. Half the accounts would
    be greeted with the wrong time of day.
    """

    agents: int
    live: int
    calls: int
    answered: int
    outcomes: int
    needs_attention: int


class Suggestion(BaseModel):
    kind: str
    text: str
    #: "prompt" fills the composer and waits; "link" navigates. Never
    #: "execute" -- a chip that silently starts calling customers is how an
    #: account is lost.
    action: str
    prompt: Optional[str] = None
    href: Optional[str] = None


class HomeResponse(BaseModel):
    hours: int
    headline: Headline
    suggestions: list[Suggestion]
    members: list[TeamMember]


@router.get("/home", response_model=HomeResponse)
async def team_home(
    hours: int = Query(default=24, ge=1, le=720),
    user: UserModel = Depends(get_user),
) -> HomeResponse:
    """Everything above the fold on the home screen, in one request.

    The charts below the fold are deliberately not here. They are the
    expensive queries and nobody should pay for them before scrolling.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    members = await _members(organization_id, hours)

    summary = await db_client.app_interaction_summary(
        organization_id=organization_id, days=7
    )
    failures = {
        row["app"]: row["errors"]
        for row in summary
        if row.get("app") and row.get("errors")
    }
    missed = await db_client.unreturned_missed_call_count(organization_id, hours=48)

    suggestions = home_suggestions.build(
        members=[member.model_dump() for member in members],
        failures_by_app=failures,
        unreturned_missed_calls=missed,
    )

    return HomeResponse(
        hours=hours,
        headline=Headline(
            agents=len(members),
            live=sum(1 for m in members if m.is_live),
            calls=sum(m.calls for m in members),
            answered=sum(m.answered for m in members),
            outcomes=sum(m.outcomes for m in members),
            needs_attention=sum(
                1 for m in members if m.tone == status_lines.TONE_ATTENTION
            ),
        ),
        suggestions=[Suggestion(**chip) for chip in suggestions],
        members=members,
    )

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
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import WorkflowStatus
from api.schemas.agent_avatar import AgentAvatar, read_avatar
from api.services import reporting_window
from api.services.auth.depends import get_user
from api.services.organization_preferences import get_organization_preferences
from api.services.workflow import (
    home_openers,
    home_suggestions,
    status_lines,
    visibility,
)

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
    #: The most recent line on the bot's timeline -- what a chat list shows
    #: under a name -- and when, and who wrote it ("agent" or "human"). None
    #: for a bot that has done nothing yet. Read by the rail, which lights an
    #: unread dot when ``last_at`` is newer than the last time this person
    #: opened the bot.
    last_line: Optional[str] = None
    last_at: Optional[datetime] = None
    last_actor: Optional[str] = None
    #: The agent's face; None is the default one (schemas/agent_avatar.py).
    avatar: Optional[AgentAvatar] = None


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


async def _members(
    organization_id: int,
    hours: int,
    *,
    since: datetime | None = None,
    viewer_role: str | None = None,
    for_person: bool = False,
) -> list[TeamMember]:
    """The team, sorted worst-first.

    Shared by the team list, the home screen and Decibyl's context so the
    three can never disagree about what an agent has been doing, and so the
    home screen costs one request rather than two.

    ``since`` overrides ``hours`` for callers that have worked out where the
    operator's midnight falls. It exists because "today" and "the last 24
    hours" are different spans, and reporting the second under the name of the
    first is what had Decibyl give the same operator two different totals
    minutes apart.
    """
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id, status=WorkflowStatus.ACTIVE.value
    )
    if for_person:
        # A member's screen omits admins-only agents (KAN-158); Decibyl's
        # own reading of the team is not a person's and is not filtered.
        workflows = visibility.only_visible(workflows, viewer_role)
    activity = await db_client.agent_activity(
        organization_id=organization_id, hours=hours, since=since
    )
    # Built for the rail and never read until now: the helper existed, the
    # rail showed a status dot, and the line a chat list lives on was in
    # neither. Wired here so the rail and the home screen carry the same one.
    latest = await db_client.latest_event_per_workflow(
        organization_id=organization_id,
        workflow_ids=[workflow.id for workflow in workflows],
    )

    members: list[TeamMember] = []
    for workflow in workflows:
        last = latest.get(workflow.id) or {}
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
                last_line=last.get("summary") or None,
                last_at=last.get("at"),
                last_actor=last.get("actor"),
                avatar=read_avatar(workflow.avatar),
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
    return TeamResponse(
        hours=hours,
        members=await _members(
            organization_id,
            hours,
            viewer_role=await visibility.role_of(
                getattr(user, "id", None), organization_id
            ),
            for_person=True,
        ),
    )


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


class Opener(BaseModel):
    """A question card under the hello: pressed, it is sent to Decibyl."""

    kind: str
    text: str
    #: The helper the card is sent to, and its name for the chip; null is
    #: Automatic. Only set when that helper can answer here.
    helper: Optional[str] = None
    helper_name: Optional[str] = None
    #: The shelf role a life-stage starter is for (agent_templates id).
    template: Optional[str] = None


class HomeResponse(BaseModel):
    hours: int
    #: What the headline's counts are actually over, in the words a sentence
    #: about them may use: "today" (midnight where the account is) or "in the
    #: last N days". The screen used to write "today" over a rolling 24-hour
    #: window and so did Decibyl, which is how the same operator was given
    #: two different totals minutes apart.
    span: str = "today"
    headline: Headline
    suggestions: list[Suggestion]
    #: Built from this account's own life -- see home_openers.
    openers: list[Opener]
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

    # The default view is "today", which means midnight where the operator
    # is -- not this time yesterday. An explicit `hours` other than 24 is a
    # caller asking for a rolling span, and gets one, named as one.
    if hours == 24:
        try:
            preferences = await get_organization_preferences(organization_id)
            zone = preferences.timezone
        except Exception as exc:  # noqa: BLE001 - the screen still renders
            logger.warning("Could not read the account's timezone: {}", exc)
            zone = None
        window = reporting_window.day_so_far(zone)
    else:
        window = reporting_window.last_days(max(1, hours // 24))

    members = await _members(
        organization_id,
        hours,
        since=window.since,
        viewer_role=await visibility.role_of(
            getattr(user, "id", None), organization_id
        ),
        for_person=True,
    )

    summary = await db_client.app_interaction_summary(
        organization_id=organization_id, days=7
    )
    failures = {
        row["app"]: row["errors"]
        for row in summary
        if row.get("app") and row.get("errors")
    }
    missed = await db_client.unreturned_missed_call_count(organization_id, hours=48)

    member_rows = [member.model_dump() for member in members]
    suggestions = home_suggestions.build(
        members=member_rows,
        failures_by_app=failures,
        unreturned_missed_calls=missed,
    )
    openers = await home_openers.gather(
        organization_id,
        members=member_rows,
        unreturned_missed_calls=missed,
        viewer_id=user.id,
    )

    return HomeResponse(
        hours=hours,
        span=window.label,
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
        openers=[Opener(**card) for card in openers],
        members=members,
    )

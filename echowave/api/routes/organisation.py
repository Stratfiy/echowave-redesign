"""The organisation itself: what it knows, who taught it, and what to improve.

The screen that survives its staff. A front desk hire in this market lasts six
to twelve months and takes the fee schedule, the doctor who runs late and the
three questions everybody asks out of the door with them. An agent that was
switched off in March leaves all of it behind, attributed to the agent that
learned it, and the next agent hired starts from there.

So archived agents appear here, marked. Their calls happened, their actions are
recorded, and the facts they learned are as true as they were. Hiding them
would be the product quietly agreeing that knowledge belongs to whoever was
holding it at the time, which is the problem this is supposed to solve.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.organisation import improvements
from api.services.workflow.organisation_learning import (
    KIND_FACT,
    KIND_GAP,
    STATUS_REJECTED,
)

router = APIRouter(prefix="/organisation", tags=["organisation"])


class MemoryEntry(BaseModel):
    id: int
    kind: str
    subject: str
    key: str
    value: str
    status: str
    times_seen: int
    last_seen_at: Optional[datetime]
    source_run_id: Optional[int]


class Contributor(BaseModel):
    """An agent that taught this organisation something.

    ``archived`` is marked rather than filtered, so nobody mistakes a record of
    past work for something still answering the phone.
    """

    workflow_id: int
    name: str
    archived: bool
    calls: int
    outcomes: int


class Improvement(BaseModel):
    """One thing this business could do better, with the count behind it.

    Named Improvement rather than Suggestion because the home screen already
    has a Suggestion -- the chips under the composer -- and two schemas with one
    name collide in the generated client, where the loser silently disappears.
    """

    key: str
    title: str
    detail: str
    #: The count this rests on. Always shown: a suggestion whose evidence is
    #: hidden is indistinguishable from a guess.
    evidence: str
    severity: str
    #: "answer" fills the chat box; "open" goes to the screen that fixes it.
    #: Nothing here applies anything by itself -- the person decides, every
    #: time.
    action: str
    href: Optional[str] = None
    prompt: Optional[str] = None


class OrganisationResponse(BaseModel):
    facts: list[MemoryEntry]
    gaps: list[MemoryEntry]
    contributors: list[Contributor]
    suggestions: list[Improvement]


def _entry(row: Any) -> MemoryEntry:
    return MemoryEntry(
        id=row.id,
        kind=row.kind,
        subject=row.subject_key,
        key=row.key,
        value=row.value,
        status=row.status,
        times_seen=row.times_seen,
        last_seen_at=row.last_seen_at,
        source_run_id=row.source_run_id,
    )


@router.get("", response_model=OrganisationResponse)
async def organisation(
    user: UserModel = Depends(get_user),
) -> OrganisationResponse:
    """Everything on the organisation screen, in one request."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    rows = await db_client.organisation_memory(organization_id=organization_id)
    live = [row for row in rows if row.status != STATUS_REJECTED]
    facts = [row for row in live if row.kind == KIND_FACT]
    gaps = [row for row in live if row.kind == KIND_GAP]

    # Every agent, archived included. The whole argument of this screen.
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    activity = await db_client.agent_activity(
        organization_id=organization_id, hours=24 * 30
    )
    contributors = [
        Contributor(
            workflow_id=workflow.id,
            name=workflow.name,
            archived=str(workflow.status) != "active",
            calls=int((activity.get(workflow.id) or {}).get("calls") or 0),
            outcomes=int((activity.get(workflow.id) or {}).get("outcomes") or 0),
        )
        for workflow in workflows
    ]
    # Busiest first, but an agent that did nothing in the window still appears:
    # it may be the one holding a fact nobody else knows.
    contributors.sort(key=lambda row: (-(row.calls + row.outcomes), row.name.lower()))

    summary = await db_client.app_interaction_summary(
        organization_id=organization_id, days=7
    )
    failures = {
        row["app"]: row["errors"]
        for row in summary
        if row.get("app") and row.get("errors")
    }

    found: list[dict[str, Any]] = []
    found += improvements.from_gaps(gaps)
    found += improvements.from_failures(failures)

    # Did the last change to any agent make it worse? Asked only of agents with
    # enough traffic to answer it, so a quiet account is not told its agent
    # regressed on the strength of four calls.
    for contributor in contributors[:5]:
        if contributor.calls < improvements.MIN_CALLS_FOR_VERSION_COMPARISON:
            continue
        versions = await db_client.outcomes_by_version(
            organization_id=organization_id,
            workflow_id=contributor.workflow_id,
            days=90,
        )
        found += improvements.from_versions(
            workflow_id=contributor.workflow_id,
            name=contributor.name,
            versions=versions,
        )

    return OrganisationResponse(
        facts=[_entry(row) for row in facts],
        gaps=[_entry(row) for row in gaps],
        contributors=contributors,
        suggestions=[Improvement(**item) for item in improvements.rank(found)],
    )

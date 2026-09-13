"""Reading back the timeline every screen was supposed to be built on.

``services/workflow/agent_timeline.py`` has been writing these rows since it
shipped. Nothing has ever read them. A write-only table is the same silent
absence this codebase keeps relearning: the data was there the whole time, the
screens said "no activity", and nobody could tell the difference between an
agent that did nothing and an agent whose work was never surfaced.

Three things this route is careful about, all of them consequences of what the
rows contain rather than of REST taste:

**The organisation is not a filter, it is the query.** Every read is scoped to
``user.selected_organization_id`` before any request-supplied id is looked at.
An id in a query string proves nothing.

**An id that is not yours is 404, not empty.** Passing ``workflow_id`` for
another tenant's bot returns "not found" rather than an empty list, because an
empty list is indistinguishable from "that bot has been quiet" and would let
somebody enumerate which ids exist.

**``ON_REQUEST`` stays off unless asked for, per call.** Recordings,
transcripts and caller words sit behind ``include_transcripts``. It is a
deliberate act by the reader, never a default, and ``OFF`` is returned by
nothing at all -- the db layer drops it whatever this route passes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/timeline", tags=["agent-timeline"])


class TimelineEvent(BaseModel):
    id: int
    at: datetime
    #: Plain strings rather than enums, matching how the column is stored. A
    #: kind written by a newer deploy must render as itself on an older
    #: screen rather than fail validation and blank the whole feed.
    kind: str
    actor: str
    summary: str
    payload: dict[str, Any]
    is_deliverable: bool
    workflow_id: Optional[int]
    workflow_run_id: Optional[int]
    folder_id: Optional[int]


class TimelineResponse(BaseModel):
    events: list[TimelineEvent]
    #: The cursor for the next page, or null when this page is the end. Null
    #: rather than omitted so a client can branch on it without guessing.
    #:
    #: A PAIR, because the rows are ordered by (at, id) and a cursor that
    #: compares less than the sort does silently drops rows -- see the note in
    #: db/agent_event_client.py. Both halves are passed back together or
    #: neither is.
    next_before_at: Optional[datetime]
    next_before_id: Optional[int]
    #: True when this page hit the limit and more rows exist that this
    #: response gives no way to ask for. Only reachable on the single-call
    #: path, which is deliberately uncursored; everywhere else `next_before_id`
    #: is the answer. Said out loud rather than left as a short list, because
    #: a truncated history that looks complete is the failure this whole
    #: module exists to stop.
    truncated: bool = False


def _as_event(row: Any) -> TimelineEvent:
    return TimelineEvent(
        id=row.id,
        at=row.at,
        kind=row.kind,
        actor=row.actor,
        summary=row.summary,
        payload=row.payload or {},
        is_deliverable=bool(row.is_deliverable),
        workflow_id=row.workflow_id,
        workflow_run_id=row.workflow_run_id,
        folder_id=row.folder_id,
    )


@router.get("", response_model=TimelineResponse)
async def timeline(
    workflow_id: Annotated[Optional[int], Query()] = None,
    workflow_run_id: Annotated[Optional[int], Query()] = None,
    folder_id: Annotated[Optional[int], Query()] = None,
    kinds: Annotated[Optional[list[str]], Query()] = None,
    deliverables_only: Annotated[bool, Query()] = False,
    include_transcripts: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_at: Annotated[Optional[datetime], Query()] = None,
    before_id: Annotated[Optional[int], Query()] = None,
    user: UserModel = Depends(get_user),
) -> TimelineResponse:
    """One feed, four screens.

    No filter gives the account's whole history; ``workflow_id`` gives one
    bot's; ``workflow_run_id`` gives one call, and that one reads *forwards*
    because a call is a story. ``deliverables_only`` gives the Deliverables
    list. The db layer owns that ordering rule -- see ``agent_events``.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Ownership before reading, for each id the caller supplied. Checked here
    # rather than trusted to the org filter on agent_events because a row that
    # was never written is indistinguishable from a row that was filtered out,
    # and "your colleague's bot has no history" is a different sentence from
    # "that bot is not yours".
    if workflow_id is not None:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow_run_id is not None:
        run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

    if folder_id is not None:
        folder = await db_client.get_folder(folder_id, organization_id=organization_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        folder_id=folder_id,
        kinds=kinds or None,
        deliverables_only=deliverables_only,
        include_on_request=include_transcripts,
        limit=limit,
        before_at=before_at,
        before_id=before_id,
    )

    events = [_as_event(row) for row in rows]

    # A cursor only where paging exists. One call is returned whole and in
    # ascending order, so handing back its last id would page a client
    # backwards through a story it is reading forwards.
    next_before_at = None
    next_before_id = None
    truncated = False
    full_page = bool(events) and len(events) == limit
    if workflow_run_id is None:
        if full_page:
            next_before_at = events[-1].at
            next_before_id = events[-1].id
    elif full_page:
        # One call is returned whole and ascending, so there is no cursor to
        # hand back -- but a call long enough to fill the page would otherwise
        # end mid-story with nothing saying so.
        truncated = True

    return TimelineResponse(
        events=events,
        next_before_at=next_before_at,
        next_before_id=next_before_id,
        truncated=truncated,
    )

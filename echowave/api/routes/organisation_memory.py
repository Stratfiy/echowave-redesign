"""What the business knows, and what it still cannot answer.

The organisation's own memory, as opposed to what it remembers about each
customer. Two halves of one screen: facts it has been told or has had
confirmed, and gaps its agents ran into -- questions nobody could answer, calls
that had to be handed to a person, systems that would not respond.

Gaps sit beside facts deliberately. "Twelve callers asked about Saturday hours"
is the business finding out something about itself, and putting the answer on
one page and the question on another is how a list like this stops being read.

Nothing here is applied automatically. Everything learned from a call arrives
unbelieved and stays out of every agent's prompt until somebody confirms it.
That rule is enforced in the write path, not here, but this is the screen where
somebody exercises it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.workflow.organisation_learning import (
    KIND_FACT,
    KIND_GAP,
    STATUS_CONFIRMED,
    STATUS_LEARNED,
    STATUS_REJECTED,
)

router = APIRouter(prefix="/organisation/memory", tags=["organisation-memory"])


class MemoryItem(BaseModel):
    id: int
    #: "fact" or "gap".
    kind: str
    #: For a fact, what it is about ("opening_hours"). For a gap, which kind of
    #: gap it is ("not_understood", "escalated", "app_failed").
    subject: str
    key: str
    value: str
    #: "learned" | "confirmed" | "rejected". A learned item has never reached
    #: an agent's prompt and will not until it is confirmed.
    status: str
    #: How many calls have run into this. The number that decides what is
    #: worth acting on.
    times_seen: int
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    #: Which call taught us, so a wrong entry can be traced back and heard.
    source_run_id: Optional[int]


class MemoryResponse(BaseModel):
    facts: list[MemoryItem]
    gaps: list[MemoryItem]


def _item(row) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        kind=row.kind,
        subject=row.subject_key,
        key=row.key,
        value=row.value,
        status=row.status,
        times_seen=row.times_seen,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        source_run_id=row.source_run_id,
    )


@router.get("", response_model=MemoryResponse)
async def read_memory(user: UserModel = Depends(get_user)) -> MemoryResponse:
    """Everything this business knows about itself, most-seen first.

    Rejected entries are left out. Dismissing something has to mean it goes
    away, or the list will not stay dismissed and people stop reading it.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    rows = await db_client.organisation_memory(organization_id=organization_id)
    live = [row for row in rows if row.status != STATUS_REJECTED]
    return MemoryResponse(
        facts=[_item(row) for row in live if row.kind == KIND_FACT],
        gaps=[_item(row) for row in live if row.kind == KIND_GAP],
    )


class FactsRequest(BaseModel):
    #: Answers from the onboarding step, or a correction typed into the chat.
    facts: dict[str, str] = Field(default_factory=dict)


@router.post("/facts", response_model=MemoryResponse)
async def write_facts(
    request: FactsRequest,
    user: UserModel = Depends(get_user),
) -> MemoryResponse:
    """What the business tells us about itself.

    Confirmed on arrival: the person filling this in is the same person who
    would confirm it afterwards, and asking twice is ceremony. This is the
    endpoint the hiring flow's onboarding step writes to, which is why hiring a
    second agent asks fewer questions than the first.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    await db_client.remember_organisation_facts(
        organization_id=organization_id,
        facts={
            key: value for key, value in request.facts.items() if str(value).strip()
        },
        status=STATUS_CONFIRMED,
    )
    return await read_memory(user=user)


class StatusRequest(BaseModel):
    #: "confirmed" believes it and lets it reach agents. "rejected" dismisses
    #: it for good, including on recurrence.
    status: str


@router.post("/{fact_id}/status", response_model=MemoryItem)
async def set_status(
    fact_id: int,
    request: StatusRequest,
    user: UserModel = Depends(get_user),
) -> MemoryItem:
    """Believe something the agents learned, or dismiss it."""
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    if request.status not in {STATUS_CONFIRMED, STATUS_REJECTED, STATUS_LEARNED}:
        raise HTTPException(status_code=400, detail="Unknown status")

    changed = await db_client.set_organisation_fact_status(
        organization_id=organization_id,
        fact_id=fact_id,
        status=request.status,
    )
    # Scoped in the UPDATE itself, so a miss is either a deleted row or
    # somebody else's. Both are 404 -- saying which would confirm that another
    # account holds that id.
    if not changed:
        raise HTTPException(status_code=404, detail="Not found")

    rows = await db_client.organisation_memory(organization_id=organization_id)
    for row in rows:
        if row.id == fact_id:
            return _item(row)
    raise HTTPException(status_code=404, detail="Not found")

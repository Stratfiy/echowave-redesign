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

from fastapi import APIRouter, Depends, HTTPException, Query
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
    #: Whose it is: None is the organisation's, every bot reads it; a bot's
    #: id is that bot's own standing instruction.
    workflow_id: Optional[int] = None


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
        workflow_id=getattr(row, "workflow_id", None),
    )


@router.get("", response_model=MemoryResponse)
async def read_memory(
    workflow_id: Optional[int] = Query(default=None),
    user: UserModel = Depends(get_user),
) -> MemoryResponse:
    """Everything this business knows about itself, most-seen first.

    With a ``workflow_id`` it is the organisation's memory plus that bot's
    own, which is what the bot's About panel shows; each row says whose it
    is. Rejected entries are left out. Dismissing something has to mean it
    goes away, or the list will not stay dismissed and people stop reading it.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    rows = await db_client.organisation_memory(
        organization_id=organization_id, workflow_id=workflow_id
    )
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
    return await read_memory(workflow_id=None, user=user)


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


# --- The graph, the export, the delete (B7, B8) ----------------------------


class MemoryGraphNode(BaseModel):
    id: str
    label: str
    summary: str = ""
    #: "confirmed" when any fact on it is held by the account's own record;
    #: "inferred" otherwise, and drawn faint.
    status: str
    connections: int = 0
    labels: list[str] = Field(default_factory=list)


class MemoryGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    fact: str
    status: str
    valid_at: Optional[str] = None
    sources: int = 0


class MemoryGraphResponse(BaseModel):
    nodes: list[MemoryGraphNode]
    edges: list[MemoryGraphEdge]
    #: False when the knowledge graph could not be read; the screen says so
    #: rather than showing an empty picture as if nothing were known.
    graph_available: bool = True
    #: Remembered facts and gaps in the account's own table, for the count.
    records: int = 0


class MemoryConnection(BaseModel):
    id: str
    other_id: str
    other: str
    relation: str
    fact: str
    status: str
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    current: bool = True


class MemorySource(BaseModel):
    id: str
    name: str
    source: str
    when: Optional[str] = None
    excerpt: str
    run_id: Optional[int] = None


class MemoryNodeDetail(BaseModel):
    id: str
    label: str
    summary: str = ""
    labels: list[str] = Field(default_factory=list)
    status: str
    connections: list[MemoryConnection]
    sources: list[MemorySource]


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(organization_id)


@router.get("/graph", response_model=MemoryGraphResponse)
async def memory_graph(user: UserModel = Depends(get_user)) -> MemoryGraphResponse:
    """The knowledge graph as nodes and edges, confirmed and inferred told
    apart, for the memory screen."""
    from api.services.knowledge_graph import export

    snap = await export.snapshot(_organization_id(user))
    return MemoryGraphResponse(**export.graph_view(snap))


@router.get("/graph/{node_id}", response_model=MemoryNodeDetail)
async def memory_node(
    node_id: str, user: UserModel = Depends(get_user)
) -> MemoryNodeDetail:
    """One node opened: its connections and the conversations behind them."""
    from api.services.knowledge_graph import export

    snap = await export.snapshot(_organization_id(user))
    detail = export.node_detail(snap, node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such node in this memory")
    return MemoryNodeDetail(**detail)


class ExportRequested(BaseModel):
    sent_to: str
    note: str


@router.post("/export", response_model=ExportRequested)
async def request_export(user: UserModel = Depends(get_user)) -> ExportRequested:
    """Email the whole memory as an Obsidian vault to the person asking."""
    from api.services.messaging.email import email_is_configured
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    organization_id = _organization_id(user)
    email = str(getattr(user, "email", "") or "").strip()
    if not email:
        raise HTTPException(
            status_code=400, detail="Your account has no email to send to"
        )
    if not email_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Email is not configured on this deployment; download the zip instead",
        )
    await enqueue_job(FunctionNames.EXPORT_MEMORY, organization_id, int(user.id))
    return ExportRequested(
        sent_to=email,
        note="On its way. Unzip it and open the folder in Obsidian; every link works.",
    )


@router.get("/export.zip")
async def download_export(user: UserModel = Depends(get_user)):
    """The same vault, straight down, for a deployment with no email or a
    person who wants it now."""
    from fastapi.responses import Response

    from api.services.knowledge_graph import export

    organization_id = _organization_id(user)
    organization = await db_client.get_organization_by_id(organization_id)
    business = str(getattr(organization, "name", "") or "This business")
    snap = await export.snapshot(organization_id)
    data = export.obsidian_zip(snap, business_name=business)
    filename = f"{export._file_stem(business)} memory.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


#: What a person types to delete everything. A phrase rather than a click,
#: because this is the one action on the screen that cannot be undone.
FORGET_PHRASE = "delete everything"


class ForgetEverythingRequest(BaseModel):
    confirm: str = Field(description=f"Must be exactly '{FORGET_PHRASE}'.")


class ForgetEverythingResponse(BaseModel):
    entities: int
    episodes: int
    records: int
    note: str


@router.post("/forget-everything", response_model=ForgetEverythingResponse)
async def forget_everything(
    body: ForgetEverythingRequest, user: UserModel = Depends(get_user)
) -> ForgetEverythingResponse:
    """Delete the organisation's memory: graph partition, remembered facts
    and gaps, day-slot marks. Not reversible; the phrase is the confirm."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.knowledge_graph import export
    from api.services.workflow import agent_timeline

    organization_id = _organization_id(user)
    if body.confirm.strip().lower() != FORGET_PHRASE:
        raise HTTPException(
            status_code=422, detail=f"Type '{FORGET_PHRASE}' to confirm"
        )
    try:
        counts = await export.forget_everything(organization_id)
    except Exception as exc:  # noqa: BLE001 - say so, never half-delete quietly
        raise HTTPException(
            status_code=503, detail=f"Could not delete the memory: {exc}"
        ) from exc
    line = (
        f"Deleted everything the business had taught its workers: "
        f"{counts['records']} remembered, {counts['entities']} people and "
        f"things, {counts['episodes']} conversations"
    )
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.AGENT_ACTED.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        payload={"by": int(user.id), "counts": counts},
        in_channel=False,
    )
    return ForgetEverythingResponse(**counts, note=line + ".")

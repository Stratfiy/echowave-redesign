"""People over HTTP: a person's own contacts (PEOPLE.md).

Thin: each route resolves the signed-in person and their workspace and
hands over to ``services/people``. Nothing here takes an owner id: every
route is about the caller's own contacts, and another person's contact id
is answered as not found, the way a wrong tenant is. The one exception is
``/people/shared``-style reads of cards a colleague chose to show the caller,
which return the card fields only.

All a 404 while the ``people`` flag is off for the workspace.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.db.people_models import PersonModel
from api.services.auth.depends import get_user
from api.services.people import (
    briefs,
    enabled,
    imports,
    normalise,
    providers,
    store,
    sync,
)

router = APIRouter(prefix="/people", tags=["people"])


def _require(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if not enabled(organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return organization_id


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No such contact.")


# --- shapes --------------------------------------------------------------------


class PersonInteraction(BaseModel):
    channel: str
    direction: str
    at: str | None = None
    line: str


class PersonSummary(BaseModel):
    id: str
    name: str
    company: str | None = None
    relation: str | None = None
    phones: list[str] = []
    emails: list[str] = []
    sources: list[str] = []
    brief: str | None = None
    last_interaction_at: str | None = None


class PersonDetail(PersonSummary):
    brief_by: str | None = None
    brief_at: str | None = None
    #: A rewrite is waiting for its debounce window.
    brief_pending: bool = False
    interactions: list[PersonInteraction] = []
    shared_with: list[int] = []


class SharedCard(BaseModel):
    """A colleague's contact they chose to show you. Never their brief or
    their interactions."""

    id: str
    name: str
    company: str | None = None
    phones: list[str] = []
    emails: list[str] = []
    shared_by: str | None = None


class PeopleList(BaseModel):
    people: list[PersonSummary]
    total: int
    shared: list[SharedCard] = []


class ProviderStatus(BaseModel):
    provider: str
    name: str
    toolkit: str
    #: not_connected | needs_setup | idle | syncing | ok | error
    state: str
    detail: str | None = None
    last_synced_at: str | None = None
    counts: dict[str, int] = {}


class PeopleStatus(BaseModel):
    providers: list[ProviderStatus]
    total: int
    open_merges: int
    agents_may_read: bool


class MergeSuggestion(BaseModel):
    id: str
    reason: str
    value: str
    keep: PersonSummary
    other: PersonSummary


class MergeList(BaseModel):
    merges: list[MergeSuggestion]


class MergeDecision(BaseModel):
    action: Literal["merge", "keep_both"]


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phones: list[str] = Field(default_factory=list, max_length=10)
    emails: list[str] = Field(default_factory=list, max_length=10)
    company: str | None = Field(default=None, max_length=200)
    relation: str | None = Field(default=None, max_length=200)


class PersonEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phones: list[str] | None = Field(default=None, max_length=10)
    emails: list[str] | None = Field(default=None, max_length=10)
    company: str | None = Field(default=None, max_length=200)
    relation: str | None = Field(default=None, max_length=200)
    brief: str | None = Field(default=None, max_length=briefs.MAX_BRIEF)


class ImportResult(BaseModel):
    source: str
    added: int
    updated: int
    unchanged: int
    skipped: int
    open_merges: int


class PickerContact(BaseModel):
    name: list[str] = Field(default_factory=list, max_length=5)
    tel: list[str] = Field(default_factory=list, max_length=10)
    email: list[str] = Field(default_factory=list, max_length=10)


class PickerImport(BaseModel):
    contacts: list[PickerContact] = Field(max_length=imports.MAX_CONTACTS)


class SyncStarted(BaseModel):
    provider: str
    state: str


class ConnectLink(BaseModel):
    provider: str
    connect_url: str


class PeopleSettings(BaseModel):
    agents_may_read: bool


class PersonShareRequest(BaseModel):
    user_id: int


class Colleague(BaseModel):
    user_id: int
    label: str


class ColleagueList(BaseModel):
    colleagues: list[Colleague]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _summary(person: PersonModel) -> PersonSummary:
    return PersonSummary(
        id=person.uuid,
        name=person.name,
        company=person.company,
        relation=person.relation,
        phones=list(person.phones or []),
        emails=list(person.emails or []),
        sources=list(person.sources or []),
        brief=person.brief,
        last_interaction_at=_iso(person.last_interaction_at),
    )


async def _detail(person: PersonModel) -> PersonDetail:
    items = await store.interactions(person, limit=20)
    return PersonDetail(
        **_summary(person).model_dump(),
        brief_by=person.brief_by,
        brief_at=_iso(person.brief_at),
        brief_pending=person.brief_due_at is not None,
        interactions=[
            PersonInteraction(
                channel=i.channel, direction=i.direction, at=_iso(i.at), line=i.line
            )
            for i in items
        ],
        shared_with=await store.shared_to(person),
    )


async def _label(user_id: int) -> str | None:
    user = await db_client.get_user_by_id(user_id)
    return (getattr(user, "email", None) or None) if user else None


async def _shared_cards(organization_id: int, user_id: int) -> list[SharedCard]:
    cards = []
    labels: dict[int, str | None] = {}
    for person in await store.shared_with(organization_id, user_id):
        if person.owner_user_id not in labels:
            labels[person.owner_user_id] = await _label(person.owner_user_id)
        cards.append(
            SharedCard(
                id=person.uuid,
                name=person.name,
                company=person.company,
                phones=list(person.phones or []),
                emails=list(person.emails or []),
                shared_by=labels[person.owner_user_id],
            )
        )
    return cards


def _incoming(body: PersonCreate | PersonEdit) -> normalise.Incoming | None:
    return normalise.Incoming(
        name=body.name,
        phones=list(body.phones or []),
        emails=list(body.emails or []),
        company=body.company,
        relation=body.relation,
    ).clean()


# --- reading -------------------------------------------------------------------


@router.get("", response_model=PeopleList)
async def my_people(
    user: Annotated[UserModel, Depends(get_user)],
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PeopleList:
    """The caller's own contacts, and cards colleagues chose to show them."""
    organization_id = _require(user)
    rows, total = await store.listing(
        organization_id, user.id, query=q, limit=limit, offset=offset
    )
    return PeopleList(
        people=[_summary(r) for r in rows],
        total=total,
        shared=await _shared_cards(organization_id, user.id)
        if offset == 0 and not q
        else [],
    )


@router.get("/status", response_model=PeopleStatus)
async def people_status(user: Annotated[UserModel, Depends(get_user)]) -> PeopleStatus:
    """Each source's state for the caller, and what waits on them."""
    organization_id = _require(user)
    return PeopleStatus(
        providers=[
            ProviderStatus(**row) for row in await sync.status(organization_id, user.id)
        ],
        total=await store.count(organization_id, user.id),
        open_merges=await store.open_merge_count(organization_id, user.id),
        agents_may_read=await store.agents_may_read(organization_id, user.id),
    )


@router.get("/merges", response_model=MergeList)
async def my_merges(user: Annotated[UserModel, Depends(get_user)]) -> MergeList:
    """Possible duplicates, both sides shown, waiting for the caller."""
    organization_id = _require(user)
    return MergeList(
        merges=[
            MergeSuggestion(
                id=row["merge"].uuid,
                reason=row["merge"].reason,
                value=row["merge"].value,
                keep=_summary(row["keep"]),
                other=_summary(row["other"]),
            )
            for row in await store.open_merges(organization_id, user.id)
        ]
    )


@router.get("/settings", response_model=PeopleSettings)
async def my_people_settings(
    user: Annotated[UserModel, Depends(get_user)],
) -> PeopleSettings:
    organization_id = _require(user)
    return PeopleSettings(
        agents_may_read=await store.agents_may_read(organization_id, user.id)
    )


@router.put("/settings", response_model=PeopleSettings)
async def save_people_settings(
    body: PeopleSettings, user: Annotated[UserModel, Depends(get_user)]
) -> PeopleSettings:
    """Whether Decibyl's outreach and voice agents may read a contact's
    brief when they call or write to that contact for the caller."""
    organization_id = _require(user)
    await store.set_agents_may_read(organization_id, user.id, body.agents_may_read)
    return body


@router.get("/colleagues", response_model=ColleagueList)
async def people_colleagues(
    user: Annotated[UserModel, Depends(get_user)],
) -> ColleagueList:
    """Who in this workspace a card could be shown to."""
    organization_id = _require(user)
    members = await db_client.list_organization_members(organization_id)
    return ColleagueList(
        colleagues=[
            Colleague(user_id=m.user.id, label=m.user.email or f"Member {m.user.id}")
            for m in members
            if m.user.id != user.id
        ]
    )


@router.get("/shared/{person_id}", response_model=SharedCard)
async def shared_card(
    person_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> SharedCard:
    organization_id = _require(user)
    rows = await store.shared_with(organization_id, user.id, uuid=person_id)
    if not rows:
        raise _not_found()
    person = rows[0]
    return SharedCard(
        id=person.uuid,
        name=person.name,
        company=person.company,
        phones=list(person.phones or []),
        emails=list(person.emails or []),
        shared_by=await _label(person.owner_user_id),
    )


@router.get("/{person_id}", response_model=PersonDetail)
async def my_person(
    person_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail:
    organization_id = _require(user)
    try:
        person = await store.get(organization_id, user.id, person_id)
    except store.NotFound:
        raise _not_found() from None
    return await _detail(person)


# --- writing -------------------------------------------------------------------


@router.post("", response_model=PersonDetail)
async def add_person(
    body: PersonCreate, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail:
    organization_id = _require(user)
    contact = _incoming(body)
    if contact is None:
        raise HTTPException(status_code=422, detail="Give the contact a name.")
    result = await store.create_manual(organization_id, user.id, contact)
    return await _detail(result.person)


@router.patch("/{person_id}", response_model=PersonDetail)
async def edit_person(
    person_id: str, body: PersonEdit, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail:
    """The caller's own edits, the brief included (it becomes theirs)."""
    organization_id = _require(user)
    changes: dict[str, Any] = {}
    sent = body.model_fields_set
    for key in ("company", "relation", "brief"):
        if key in sent:
            changes[key] = normalise.text(
                getattr(body, key), briefs.MAX_BRIEF if key == "brief" else 200
            )
    if "name" in sent and body.name:
        changes["name"] = normalise.text(body.name, normalise.MAX_NAME)
    if "phones" in sent and body.phones is not None:
        changes["phones"] = [p for p in map(normalise.phone, body.phones) if p]
        if len(changes["phones"]) != len([p for p in body.phones if p.strip()]):
            raise HTTPException(
                status_code=422, detail="One of those numbers is not a phone number."
            )
    if "emails" in sent and body.emails is not None:
        changes["emails"] = [e for e in map(normalise.email, body.emails) if e]
        if len(changes["emails"]) != len([e for e in body.emails if e.strip()]):
            raise HTTPException(
                status_code=422, detail="One of those is not an email address."
            )
    try:
        person = await store.edit(organization_id, user.id, person_id, changes)
    except store.NotFound:
        raise _not_found() from None
    return await _detail(person)


@router.delete("/{person_id}")
async def delete_person(
    person_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, bool]:
    organization_id = _require(user)
    try:
        await store.remove(organization_id, user.id, person_id)
    except store.NotFound:
        raise _not_found() from None
    return {"deleted": True}


@router.post("/{person_id}/brief", response_model=PersonDetail)
async def rewrite_brief(
    person_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail:
    """Rewrite the brief now from the latest interactions."""
    organization_id = _require(user)
    try:
        person = await store.get(organization_id, user.id, person_id)
    except store.NotFound:
        raise _not_found() from None
    try:
        person = await briefs.write(person)
    except briefs.BriefUnavailable:
        raise HTTPException(
            status_code=503,
            detail="No model is set up to write briefs in this workspace yet.",
        ) from None
    return await _detail(person)


@router.post("/{person_id}/share", response_model=PersonDetail)
async def share_person(
    person_id: str,
    body: PersonShareRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> PersonDetail:
    """Show this contact's card (not the brief, not the history) to one
    colleague in this workspace."""
    organization_id = _require(user)
    try:
        await store.share(organization_id, user.id, person_id, body.user_id)
        person = await store.get(organization_id, user.id, person_id)
    except store.NotFound:
        raise _not_found() from None
    return await _detail(person)


@router.delete("/{person_id}/share/{user_id}", response_model=PersonDetail)
async def unshare_person(
    person_id: str, user_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail:
    organization_id = _require(user)
    try:
        await store.unshare(organization_id, user.id, person_id, user_id)
        person = await store.get(organization_id, user.id, person_id)
    except store.NotFound:
        raise _not_found() from None
    return await _detail(person)


@router.post("/merges/{merge_id}", response_model=PersonDetail | None)
async def decide_merge(
    merge_id: str, body: MergeDecision, user: Annotated[UserModel, Depends(get_user)]
) -> PersonDetail | None:
    """Merge the two into one, or keep both. Nothing merges otherwise."""
    organization_id = _require(user)
    try:
        kept = await store.decide_merge(
            organization_id, user.id, merge_id, merge=body.action == "merge"
        )
    except store.NotFound:
        raise HTTPException(status_code=404, detail="No such suggestion.") from None
    return await _detail(kept) if kept else None


async def _store_all(
    organization_id: int, user_id: int, contacts: list, source: str, skipped: int
) -> ImportResult:
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    for contact in contacts:
        result = await store.upsert(organization_id, user_id, contact, source=source)
        key = (
            "added"
            if result.created
            else ("updated" if result.changed else "unchanged")
        )
        counts[key] += 1
    return ImportResult(
        source=source,
        skipped=skipped,
        open_merges=await store.open_merge_count(organization_id, user_id),
        **counts,
    )


@router.post("/import", response_model=ImportResult)
async def import_people(
    user: Annotated[UserModel, Depends(get_user)],
    file: UploadFile = File(...),
) -> ImportResult:
    """A vCard (.vcf) or CSV file of contacts, added as the caller's own."""
    organization_id = _require(user)
    data = await file.read(imports.MAX_FILE_BYTES + 1)
    try:
        contacts, skipped, source = imports.parse_file(file.filename or "", data)
    except imports.ImportRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return await _store_all(organization_id, user.id, contacts, source, skipped)


@router.post("/import/picker", response_model=ImportResult)
async def import_picked(
    body: PickerImport, user: Annotated[UserModel, Depends(get_user)]
) -> ImportResult:
    """What the phone's contact picker returned (Android Chrome)."""
    organization_id = _require(user)
    contacts, skipped = imports.parse_picker([c.model_dump() for c in body.contacts])
    return await _store_all(organization_id, user.id, contacts, "picker", skipped)


@router.post("/sync/{provider}", response_model=SyncStarted)
async def start_sync(
    provider: Literal["google", "microsoft"],
    user: Annotated[UserModel, Depends(get_user)],
) -> SyncStarted:
    organization_id = _require(user)
    try:
        state = await sync.start(organization_id, user.id, provider)
    except sync.SyncRefused as exc:
        code = 409 if exc.state in (sync.NOT_CONNECTED, sync.NEEDS_SETUP) else 503
        raise HTTPException(status_code=code, detail=str(exc)) from None
    return SyncStarted(provider=provider, state=state)


@router.post("/connect/{provider}", response_model=ConnectLink)
async def connect_provider(
    provider: Literal["google", "microsoft"],
    user: Annotated[UserModel, Depends(get_user)],
) -> ConnectLink:
    """A sign-in link for the caller's own Google or Microsoft account,
    opened from the chip on the People screen -- nobody is sent elsewhere."""
    organization_id = _require(user)
    from api.services.integrations.composio import client, members

    if not client.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Connecting apps is not set up on this deployment yet.",
        )
    if members.member_scope(user.id) is None:
        raise HTTPException(
            status_code=409,
            detail="Contacts sync from your own connection, and connections here "
            "are the workspace's. An admin can turn on per-person connections.",
        )
    link = await client.connect_link(
        toolkit=providers.toolkit(provider),
        organization_id=organization_id,
        user_id=user.id,
    )
    if "error" in link:
        raise HTTPException(status_code=502, detail=link["error"])
    try:
        await db_client.record_member_connection(
            organization_id=organization_id,
            user_id=user.id,
            toolkit=providers.toolkit(provider),
        )
    except Exception as exc:  # noqa: BLE001 - the link is still good
        logger.warning("Could not record a member connection: {}", exc)
    return ConnectLink(provider=provider, connect_url=link["url"])

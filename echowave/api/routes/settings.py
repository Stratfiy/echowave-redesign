"""Settings for one person, over HTTP (launch stream `settings`, SETTINGS.md).

Thin: each route resolves the signed-in person and the workspace they are
in, and hands over to ``services/settings``. Every group is behind its own
flag and is a 404 while that flag is off; none takes another person's id.

* ``/me/settings/profile`` -- Account, Personalization, Voice and language,
  on controls' ``member_preferences`` (``settings_shell``).
* ``/me/memory`` -- the memory manager (``memory_manager``).
* ``/me/temporary-conversations`` -- temporary chats (``memory_manager``).
* ``/me/saved``, ``/me/search`` -- saved items and scoped search
  (``saved_items``).
* ``/me/privacy`` -- personal export and deletion (``privacy_center``).
* ``/me/settings/cards`` -- the cards those screens raise, settled by
  their owner only.
* ``/settings/models/inheritance`` -- model defaults with inheritance
  (``model_inheritance``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services import features, member_preferences
from api.services.auth.depends import get_user, require_organization_role
from api.services.settings import (
    MEMORY_MANAGER,
    MODEL_INHERITANCE,
    PRIVACY_CENTER,
    SAVED_ITEMS,
    SETTINGS_SHELL,
    cards,
    memory,
    privacy,
    profile,
    saved,
    temporary,
)
from api.services.settings import models as model_defaults
from api.services.workflow.actions import ActionError

router = APIRouter(tags=["settings"])

User = Annotated[UserModel, Depends(get_user)]


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(organization_id)


def _flag(name: str):
    return Depends(features.require(name, per_organization=True))


# --- profile: account, personalization, voice and language --------------------


class SettingsLanguage(BaseModel):
    tag: str
    native: str
    english: str
    voice: bool


class Profile(BaseModel):
    preferred_name: str | None = None
    email: str | None = None
    email_verified: bool = False
    language: str | None = None
    explanation_language: str | None = None
    timezone: str | None = None
    voice: str | None = None
    summary_time: str | None = None
    response_length: str | None = None
    custom_instructions: str | None = None
    memory_enabled: bool | None = None
    speaking_speed: float | None = None
    captions: bool | None = None
    auto_detect_language: bool | None = None
    revision: int
    updated_at: str | None = None
    #: When the answers given at the door were read in, if they were.
    onboarding_absorbed_at: str | None = None
    languages: list[SettingsLanguage] = Field(default_factory=list)
    max_instructions: int = member_preferences.MAX_INSTRUCTIONS
    workspace_name: str | None = None


class ProfileWrite(BaseModel):
    """Only the fields sent change; null clears one. ``revision`` is the one
    the screen read; an older one is a 409 carrying what is stored."""

    preferred_name: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=16)
    explanation_language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    voice: str | None = Field(default=None, max_length=64)
    summary_time: str | None = Field(default=None, max_length=5)
    response_length: str | None = Field(default=None, max_length=16)
    custom_instructions: str | None = Field(default=None, max_length=20_000)
    # Strict: "yes" is not on, and true is not a speed.
    memory_enabled: StrictBool | None = None
    speaking_speed: StrictFloat | StrictInt | None = None
    captions: StrictBool | None = None
    auto_detect_language: StrictBool | None = None
    revision: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


def _require_preferences() -> None:
    if not member_preferences.enabled():
        # Settings keeps a person's choices in controls' store; without it
        # there is nowhere honest to save them.
        raise HTTPException(
            status_code=503,
            detail="Personal settings are not switched on here yet.",
        )


async def _profile(user: UserModel, stored: dict[str, Any]) -> Profile:
    organization = (
        await db_client.get_organization_by_id(user.selected_organization_id)
        if user.selected_organization_id
        else None
    )
    fields = {k: stored.get(k) for k in Profile.model_fields if k in stored}
    return Profile(
        **fields,
        email=getattr(user, "email", None),
        email_verified=getattr(user, "email_verified_at", None) is not None,
        languages=[SettingsLanguage(**language) for language in profile.languages()],
        workspace_name=getattr(organization, "name", None),
    )


@router.get(
    "/me/settings/profile", response_model=Profile, dependencies=[_flag(SETTINGS_SHELL)]
)
async def my_profile(user: User) -> Profile:
    _require_preferences()
    return await _profile(user, await profile.absorb_onboarding(user.id))


@router.put(
    "/me/settings/profile", response_model=Profile, dependencies=[_flag(SETTINGS_SHELL)]
)
async def save_my_profile(body: ProfileWrite, user: User) -> Profile:
    _require_preferences()
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    try:
        row = await member_preferences.save(user.id, changes, revision=revision)
    except member_preferences.PreferenceInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except member_preferences.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "These were changed somewhere else. Here is what is saved now.",
                "stored": (await _profile(user, exc.stored)).model_dump(),
            },
        ) from exc
    return await _profile(user, row)


class VoiceOption(BaseModel):
    voice_id: str
    name: str
    gender: str | None = None
    sample_url: str | None = None


class VoiceCatalogue(BaseModel):
    provider: str
    provider_label: str
    model: str | None = None
    language: str | None = None
    #: None when the provider does not say which languages it speaks.
    language_supported: bool | None = None
    readiness: Literal["ready", "needs_setup"]
    readiness_reason: str | None = None
    unavailable_reason: str | None = None
    voices: list[VoiceOption]
    #: False when the samples could not be looked up in time; the list is
    #: still right, only previews are missing.
    previews_available: bool = True


@router.get(
    "/me/settings/voices",
    response_model=VoiceCatalogue,
    dependencies=[_flag(SETTINGS_SHELL)],
)
async def my_voices(
    user: User, language: Annotated[str | None, Query(max_length=16)] = None
) -> VoiceCatalogue:
    """Voices that can speak ``language`` for this workspace."""
    return VoiceCatalogue(**await profile.voices(_organization_id(user), language))


# --- memory manager -----------------------------------------------------------


class MemorySource(BaseModel):
    kind: str
    line: str
    run_id: int | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    confirmed_at: str | None = None
    times_seen: int = 0


class MemoryChange(BaseModel):
    change: str
    before: str | None = None
    after: str | None = None
    note: str | None = None
    at: str
    by_you: bool = False


class MemoryFact(BaseModel):
    id: int
    key: str
    value: str
    subject: dict[str, str] | None = None
    kind: str
    status: str
    scope: Literal["mine", "workspace"]
    source: MemorySource
    saved_at: str
    revisions: int = 0
    history: list[MemoryChange] | None = None


class MemoryOverview(BaseModel):
    memory_enabled: bool
    memory_chosen: bool
    revision: int
    personal_memory: bool
    mine: list[MemoryFact]
    workspace: list[MemoryFact]
    temporary_retention: str


@router.get(
    "/me/memory", response_model=MemoryOverview, dependencies=[_flag(MEMORY_MANAGER)]
)
async def my_memory(user: User) -> MemoryOverview:
    data = await memory.overview(
        organization_id=_organization_id(user), user_id=user.id
    )
    return MemoryOverview(**data, temporary_retention=temporary.retention_line())


class MemorySwitch(BaseModel):
    memory_enabled: StrictBool
    revision: int = Field(ge=0)


@router.put(
    "/me/memory/switch",
    response_model=MemoryOverview,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def set_memory_switch(body: MemorySwitch, user: User) -> MemoryOverview:
    """Remember things from my conversations: on or off, for this person
    only. Saved at once; a stale revision is a 409 with what is stored."""
    _require_preferences()
    try:
        await member_preferences.save(
            user.id, {"memory_enabled": body.memory_enabled}, revision=body.revision
        )
    except member_preferences.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This was changed somewhere else.",
                "memory_enabled": exc.stored.get("memory_enabled") is True,
                "revision": exc.stored.get("revision"),
            },
        ) from exc
    return await my_memory(user)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


@router.get(
    "/me/memory/destinations",
    response_model=list[dict[str, Any]],
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def memory_destinations(user: User) -> list[dict[str, Any]]:
    return await memory.destinations(user_id=user.id)


@router.get(
    "/me/memory/{fact_id}",
    response_model=MemoryFact,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def memory_fact(fact_id: int, user: User) -> MemoryFact:
    try:
        return MemoryFact(
            **await memory.detail(
                organization_id=_organization_id(user), user_id=user.id, fact_id=fact_id
            )
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc


class MemoryEdit(BaseModel):
    value: str = Field(max_length=memory.MAX_VALUE * 2)
    #: The value the screen showed; a different stored value is a 409.
    expected_value: str


@router.patch(
    "/me/memory/{fact_id}",
    response_model=MemoryFact,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def edit_memory_fact(fact_id: int, body: MemoryEdit, user: User) -> MemoryFact:
    try:
        return MemoryFact(
            **await memory.edit(
                organization_id=_organization_id(user),
                user_id=user.id,
                fact_id=fact_id,
                value=body.value,
                expected_value=body.expected_value,
            )
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc
    except memory.MemoryInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except memory.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This changed since you opened it. Here is what is saved now.",
                "stored": exc.stored,
            },
        ) from exc


@router.post(
    "/me/memory/{fact_id}/confirm",
    response_model=MemoryFact,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def confirm_memory_fact(fact_id: int, user: User) -> MemoryFact:
    try:
        return MemoryFact(
            **await memory.confirm(
                organization_id=_organization_id(user), user_id=user.id, fact_id=fact_id
            )
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc


class SettingsCard(BaseModel):
    event_id: int
    organization_id: int
    action: str | None = None
    label: str
    effect: str
    state: str
    ledger_state: str | None = None
    version: str | None = None
    reversible: bool
    fires_at: str | None = None
    note: str | None = None
    error: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/me/memory/{fact_id}/forget",
    response_model=SettingsCard,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def forget_memory_fact(fact_id: int, user: User) -> SettingsCard:
    """Ask to forget a fact: a card the person confirms. Nothing changes
    until they do."""
    try:
        return SettingsCard(
            **await memory.propose_forget(
                organization_id=_organization_id(user), user_id=user.id, fact_id=fact_id
            )
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc
    except (memory.MemoryInvalid, ActionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class SharePreview(BaseModel):
    fact_id: int
    key: str
    value: str
    destination: dict[str, Any]
    members: int
    moves: bool
    lines: list[str]


@router.get(
    "/me/memory/{fact_id}/share-preview",
    response_model=SharePreview,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def share_memory_preview(
    fact_id: int, user: User, destination: Annotated[int, Query()]
) -> SharePreview:
    try:
        return SharePreview(
            **await memory.share_preview(
                organization_id=_organization_id(user),
                user_id=user.id,
                fact_id=fact_id,
                destination_id=destination,
            )
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc
    except memory.MemoryInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ShareRequest(BaseModel):
    destination_organization_id: int
    #: The value the preview showed.
    expected_value: str


@router.post(
    "/me/memory/{fact_id}/share",
    response_model=dict[str, Any],
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def share_memory_fact(
    fact_id: int, body: ShareRequest, user: User
) -> dict[str, Any]:
    try:
        return await memory.share(
            organization_id=_organization_id(user),
            user_id=user.id,
            fact_id=fact_id,
            destination_id=body.destination_organization_id,
            expected_value=body.expected_value,
        )
    except memory.FactNotFound as exc:
        raise _not_found() from exc
    except memory.MemoryInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except memory.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "This changed since the preview.", "stored": exc.stored},
        ) from exc


class TemporaryConversation(BaseModel):
    thread_id: str
    href: str | None = None
    expires_at: str
    retention: str
    purged: bool = False


@router.post(
    "/me/temporary-conversations",
    response_model=TemporaryConversation,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def start_temporary_conversation(user: User) -> TemporaryConversation:
    return TemporaryConversation(
        **await temporary.start(organization_id=_organization_id(user), user_id=user.id)
    )


@router.get(
    "/me/temporary-conversations/{thread_id}",
    response_model=TemporaryConversation,
    dependencies=[_flag(MEMORY_MANAGER)],
)
async def temporary_conversation(thread_id: str, user: User) -> TemporaryConversation:
    found = await temporary.describe(
        organization_id=_organization_id(user), user_id=user.id, thread_id=thread_id
    )
    if found is None:
        raise _not_found()
    return TemporaryConversation(**found)


# --- the cards these screens raise ------------------------------------------------

_CARD_FLAGS = {
    cards.FORGET_MEMORY: MEMORY_MANAGER,
    cards.DELETE_SAVED_ITEM: SAVED_ITEMS,
    cards.DELETE_PERSONAL_DATA: PRIVACY_CENTER,
}


async def _card_scope(user: UserModel, organization_id: int) -> None:
    """The card's workspace must be one the person is in."""
    if await db_client.get_membership(user.id, organization_id) is None:
        raise _not_found()


@router.get("/me/settings/cards/{event_id}", response_model=SettingsCard)
async def settings_card(
    event_id: int, user: User, organization_id: Annotated[int, Query()]
) -> SettingsCard:
    await _card_scope(user, organization_id)
    try:
        card = await cards.get(
            organization_id=organization_id, user_id=user.id, event_id=event_id
        )
    except cards.CardNotFound as exc:
        raise _not_found() from exc
    if not features.is_on(
        _CARD_FLAGS.get(card["action"], SETTINGS_SHELL), organization_id
    ):
        raise _not_found()
    return SettingsCard(**card)


class SettleCard(BaseModel):
    organization_id: int
    verb: Literal["confirm", "decline", "undo"]
    #: The version the screen showed; Confirm approves exactly that one.
    version: str | None = Field(default=None, max_length=32)


@router.post("/me/settings/cards/{event_id}/settle", response_model=SettingsCard)
async def settle_settings_card(
    event_id: int, body: SettleCard, user: User
) -> SettingsCard:
    """Do it / Don't / Undo on a Settings card, by its owner only. The same
    run-once machinery as every card (services/workflow/actions.py)."""
    await _card_scope(user, body.organization_id)
    try:
        card = await cards.get(
            organization_id=body.organization_id, user_id=user.id, event_id=event_id
        )
        if not features.is_on(
            _CARD_FLAGS.get(card["action"], SETTINGS_SHELL), body.organization_id
        ):
            raise _not_found()
        return SettingsCard(
            **await cards.settle(
                organization_id=body.organization_id,
                user_id=user.id,
                event_id=event_id,
                verb=body.verb,
                version=body.version,
            )
        )
    except cards.CardNotFound as exc:
        raise _not_found() from exc
    except ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- saved items and search ---------------------------------------------------------


class SavedItem(BaseModel):
    id: int
    organization_id: int
    kind: str
    title: str
    body: str | None = None
    visibility: str
    status: str
    mine: bool
    source_event_id: int | None = None
    thread_id: str | None = None
    conversation_href: str | None = None
    file: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    deletion_effects: list[str] = Field(default_factory=list)


def _saved(item: dict[str, Any]) -> SavedItem:
    return SavedItem(**item, deletion_effects=saved.deletion_effects(item))


class SavedList(BaseModel):
    scope: str
    items: list[SavedItem]


@router.get("/me/saved", response_model=SavedList, dependencies=[_flag(SAVED_ITEMS)])
async def my_saved(
    user: User, scope: Annotated[Literal["personal", "workspace"], Query()] = "personal"
) -> SavedList:
    items = await saved.list_items(
        organization_id=_organization_id(user), user_id=user.id, scope=scope
    )
    return SavedList(scope=scope, items=[_saved(i) for i in items])


class SaveRequest(BaseModel):
    title: str = Field(max_length=400)
    kind: Literal["reply", "note", "file", "link"] = "reply"
    body: str | None = Field(default=None, max_length=saved.MAX_BODY)
    visibility: Literal["private", "workspace"] = "private"
    source_event_id: int | None = None
    thread_id: str | None = Field(default=None, max_length=36)


@router.post("/me/saved", response_model=SavedItem, dependencies=[_flag(SAVED_ITEMS)])
async def save_item(body: SaveRequest, user: User) -> SavedItem:
    try:
        return _saved(
            await saved.save(
                organization_id=_organization_id(user),
                user_id=user.id,
                **body.model_dump(),
            )
        )
    except saved.SavedNotFound as exc:
        raise _not_found() from exc
    except saved.SavedInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/me/saved/{item_id}", response_model=SavedItem, dependencies=[_flag(SAVED_ITEMS)]
)
async def saved_item(item_id: int, user: User) -> SavedItem:
    try:
        return _saved(
            await saved.get(
                organization_id=_organization_id(user), user_id=user.id, item_id=item_id
            )
        )
    except saved.SavedNotFound as exc:
        raise _not_found() from exc


class RenameRequest(BaseModel):
    title: str = Field(max_length=400)


@router.patch(
    "/me/saved/{item_id}", response_model=SavedItem, dependencies=[_flag(SAVED_ITEMS)]
)
async def rename_saved_item(item_id: int, body: RenameRequest, user: User) -> SavedItem:
    try:
        return _saved(
            await saved.rename(
                organization_id=_organization_id(user),
                user_id=user.id,
                item_id=item_id,
                title=body.title,
            )
        )
    except saved.SavedNotFound as exc:
        raise _not_found() from exc
    except saved.SavedInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/me/saved/{item_id}/delete",
    response_model=SettingsCard,
    dependencies=[_flag(SAVED_ITEMS)],
)
async def delete_saved_item(item_id: int, user: User) -> SettingsCard:
    """Ask to delete a saved item: a card the person confirms."""
    try:
        return SettingsCard(
            **await saved.propose_delete(
                organization_id=_organization_id(user), user_id=user.id, item_id=item_id
            )
        )
    except saved.SavedNotFound as exc:
        raise _not_found() from exc
    except (saved.SavedInvalid, ActionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class SearchResult(BaseModel):
    type: Literal["saved", "memory"]
    id: int
    title: str
    snippet: str
    kind: str
    href: str


class SearchResponse(BaseModel):
    scope: str
    query: str
    results: list[SearchResult]


@router.get(
    "/me/search", response_model=SearchResponse, dependencies=[_flag(SAVED_ITEMS)]
)
async def search_mine(
    user: User,
    q: Annotated[str, Query(max_length=200)] = "",
    scope: Annotated[Literal["personal", "workspace"], Query()] = "personal",
) -> SearchResponse:
    """Saved items and memories in one scope. Never another person's."""
    return SearchResponse(
        **await saved.search(
            organization_id=_organization_id(user),
            user_id=user.id,
            scope=scope,
            query=q,
        )
    )


# --- privacy and security --------------------------------------------------------------


class DataRequest(BaseModel):
    id: int
    kind: str
    status: str
    stores: list[dict[str, Any]]
    card_event_id: int | None = None
    card_organization_id: int | None = None
    error: str | None = None
    expires_at: str | None = None
    completed_at: str | None = None
    created_at: str
    card: SettingsCard | None = None


class PrivacyOverview(BaseModel):
    retention: dict[str, Any]
    security: dict[str, Any]
    deletion_available: bool
    workspace_owner: bool
    delete_phrase: str
    requests: list[DataRequest]


@router.get(
    "/me/privacy", response_model=PrivacyOverview, dependencies=[_flag(PRIVACY_CENTER)]
)
async def my_privacy(user: User) -> PrivacyOverview:
    return PrivacyOverview(**await privacy.overview(user, _organization_id(user)))


class DataPreview(BaseModel):
    kind: str
    stores: list[dict[str, Any]]
    exceptions: list[dict[str, Any]]
    scope: str
    lines: list[str]


@router.get(
    "/me/privacy/preview",
    response_model=DataPreview,
    dependencies=[_flag(PRIVACY_CENTER)],
)
async def privacy_preview(
    user: User, kind: Annotated[Literal["export", "deletion"], Query()]
) -> DataPreview:
    return DataPreview(**await privacy.preview(user.id, kind))


@router.post(
    "/me/privacy/export",
    response_model=DataRequest,
    dependencies=[_flag(PRIVACY_CENTER)],
)
async def request_my_export(user: User) -> DataRequest:
    return DataRequest(**await privacy.request_export(user.id))


@router.get(
    "/me/privacy/requests/{request_id}",
    response_model=DataRequest,
    dependencies=[_flag(PRIVACY_CENTER)],
)
async def my_data_request(request_id: int, user: User) -> DataRequest:
    try:
        return DataRequest(**await privacy.get(user.id, request_id))
    except privacy.RequestNotFound as exc:
        raise _not_found() from exc


@router.get(
    "/me/privacy/requests/{request_id}/download", dependencies=[_flag(PRIVACY_CENTER)]
)
async def download_my_export(request_id: int, user: User) -> JSONResponse:
    try:
        payload = await privacy.download(user.id, request_id)
    except privacy.RequestNotFound as exc:
        raise _not_found() from exc
    except privacy.PrivacyInvalid as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="my-decibyl-data.json"',
            "Cache-Control": "no-store",
        },
    )


class DeletionRequest(BaseModel):
    #: The typed phrase, when the person has no second factor.
    phrase: str | None = Field(default=None, max_length=64)
    #: A current authenticator code, when they do.
    code: str | None = Field(default=None, max_length=16)


@router.post(
    "/me/privacy/deletion",
    response_model=DataRequest,
    dependencies=[_flag(PRIVACY_CENTER)],
)
async def request_my_deletion(body: DeletionRequest, user: User) -> DataRequest:
    """Ask to delete the person's own data. Raises a card in their personal
    space; nothing is deleted until they press Do it on it."""
    try:
        result = await privacy.request_deletion(
            user, phrase=body.phrase, code=body.code
        )
    except privacy.NeedsSetup as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except privacy.PrivacyInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataRequest(**result)


# --- model defaults with inheritance ------------------------------------------------------


class ModelChoice(BaseModel):
    slot: str
    value: str
    #: The slot's revision the screen read; a different one is a 409.
    revision: str | None = Field(default=None, max_length=32)


@router.get(
    "/settings/models/inheritance",
    response_model=dict[str, Any],
    dependencies=[_flag(MODEL_INHERITANCE)],
)
async def model_inheritance(user: User) -> dict[str, Any]:
    return await model_defaults.view(_organization_id(user))


@router.put(
    "/settings/models/inheritance",
    response_model=dict[str, Any],
    dependencies=[_flag(MODEL_INHERITANCE)],
)
async def choose_model_default(
    body: ModelChoice,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> dict[str, Any]:
    """Set one slot for the workspace, if the screen saw what is stored now.
    Applies to new sessions; a call in progress keeps what it started with."""
    from api.services.configuration import workspace_models

    try:
        return await model_defaults.choose(
            _organization_id(user),
            slot=body.slot,
            value=body.value,
            revision=body.revision,
        )
    except model_defaults.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This was changed somewhere else. Here is what runs now.",
                "stored": exc.stored,
            },
        ) from exc
    except workspace_models.LockedStack as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except workspace_models.UnknownChoice as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

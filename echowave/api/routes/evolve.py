"""Evolving skills over HTTP (services/evolve).

Thin: each route resolves the signed-in person and their workspace and hands
over to the service. The whole group is a 404 while ``evolve_skills`` is off
for the person's workspace.

* ``GET /evolve/skills`` -- the Skills tab: every kept skill as a full card.
* ``GET /evolve/skills/{slug}/versions`` -- one skill's version history.
* ``POST /evolve/skills/{slug}/rollback`` -- back to the version before.
* ``POST /evolve/skills/{slug}/agents`` -- put the skill on one agent.
* ``PUT /evolve/versions/{version_id}`` -- edit a draft before saving it.
* ``POST /evolve/versions/{version_id}/publish`` | ``/discard``.
* ``POST /evolve/cards/settle`` -- a press on a learning card in a thread.
* ``POST /evolve/remember`` -- "remember this as my way" from a thread.
* ``GET /evolve/experience`` -- the ledger, as the viewer may see it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import evolve, features
from api.services.auth.depends import get_user
from api.services.evolve import experience, remember, tab, versions

router = APIRouter(
    prefix="/evolve",
    tags=["evolve"],
    dependencies=[Depends(features.require(evolve.FLAG, per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class AgentRef(BaseModel):
    id: int
    name: str


class ExternalActions(BaseModel):
    can_act: bool
    sentence: str
    mentions: list[str] = []


class SkillExplain(BaseModel):
    """The five things a skill card says (skills-and-context.md)."""

    accomplish: str
    example: str
    needs: list[str]
    produces: list[str]
    external_actions: ExternalActions
    enabled_on: list[str]
    wont_do: list[str] = []


class SkillVersionOut(BaseModel):
    id: int
    slug: str
    version: int
    status: str
    origin: str
    base_version: int | None = None
    lessons: list[str] = []
    content: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    evaluation: dict[str, Any] | None = None
    cost: dict[str, Any] = {}
    author_user_id: int | None = None
    decided_by: int | None = None
    published_at: str | None = None
    rolled_back_at: str | None = None
    rolled_back_by: int | None = None
    reason: str | None = None
    created_at: str | None = None
    card_event_id: int | None = None


class SkillTabCard(BaseModel):
    slug: str
    title: str
    emoji: str = ""
    division: str = ""
    own: bool = False
    installed: bool = False
    explain: SkillExplain
    on_agents: list[AgentRef] = []
    active_version: int | None = None
    versions: list[SkillVersionOut] = []
    improvement: dict[str, Any] | None = None
    experience: dict[str, int] = {}


class SkillsTabResponse(BaseModel):
    skills: list[SkillTabCard]
    max_per_bot: int


@router.get("/skills", response_model=SkillsTabResponse)
async def skills_tab(user: UserModel = Depends(get_user)) -> SkillsTabResponse:
    return SkillsTabResponse(**await tab.cards(_organization_id(user), user.id))


@router.get("/skills/{slug}/versions", response_model=list[SkillVersionOut])
async def skill_versions(
    slug: str, user: UserModel = Depends(get_user)
) -> list[SkillVersionOut]:
    rows = await versions.history(_organization_id(user), slug[:64], user.id)
    return [SkillVersionOut(**r) for r in rows]


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=300)


class RollbackResponse(BaseModel):
    slug: str
    rolled_back: int
    active: int | None = None


@router.post("/skills/{slug}/rollback", response_model=RollbackResponse)
async def rollback_skill(
    slug: str, body: RollbackRequest, user: UserModel = Depends(get_user)
) -> RollbackResponse:
    try:
        done = await versions.rollback(
            _organization_id(user), slug[:64], user.id, reason=body.reason
        )
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RollbackResponse(**done)


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: int


@router.post("/skills/{slug}/agents")
async def attach_skill(
    slug: str, body: AttachRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    try:
        await versions.attach(
            _organization_id(user), slug[:64], body.workflow_id, user.id
        )
    except versions.VersionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slug": slug, "workflow_id": body.workflow_id}


class EditDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]


@router.put("/versions/{version_id}", response_model=SkillVersionOut)
async def edit_version(
    version_id: int, body: EditDraftRequest, user: UserModel = Depends(get_user)
) -> SkillVersionOut:
    try:
        row = await versions.edit_draft(
            _organization_id(user), version_id, user.id, body.content
        )
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SkillVersionOut(**versions.describe(row))


@router.post("/versions/{version_id}/publish", response_model=SkillVersionOut)
async def publish_version(
    version_id: int, user: UserModel = Depends(get_user)
) -> SkillVersionOut:
    try:
        row = await versions.publish(_organization_id(user), version_id, user.id)
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SkillVersionOut(**versions.describe(row))


@router.post("/versions/{version_id}/discard", response_model=SkillVersionOut)
async def discard_version(
    version_id: int, user: UserModel = Depends(get_user)
) -> SkillVersionOut:
    try:
        row = await versions.discard(_organization_id(user), version_id, user.id)
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SkillVersionOut(**versions.describe(row))


class SettleCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    #: publish | discard (offer, remembered), rollback | keep (disable),
    #: add | discard (attach).
    action: str = Field(max_length=16)


class SettleCardResponse(BaseModel):
    event_id: int
    payload: dict[str, Any]


@router.post("/cards/settle", response_model=SettleCardResponse)
async def settle_card(
    body: SettleCardRequest, user: UserModel = Depends(get_user)
) -> SettleCardResponse:
    """A press on a learning card. Stamped into the card's own row."""
    try:
        payload = await versions.settle(
            organization_id=_organization_id(user),
            event_id=body.event_id,
            action=body.action.strip().lower(),
            user_id=user.id,
        )
    except versions.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SettleCardResponse(event_id=body.event_id, payload=payload)


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: int | None = None
    thread_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="", max_length=120)


@router.post("/remember")
async def remember_my_way(
    body: RememberRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """'Remember this as my way of doing it', from the thread's own button."""
    return await remember.draft(
        organization_id=_organization_id(user),
        user_id=user.id,
        workflow_id=body.workflow_id,
        thread_id=body.thread_id,
        title_hint=body.title,
    )


class ExperienceOut(BaseModel):
    id: int
    kind: str
    task_family: str
    skill_slug: str | None = None
    skill_version: int | None = None
    outcome: str
    split: str
    scope: str
    summary: str
    evidence: list[dict[str, Any]] = []
    occurred_at: str | None = None


@router.get("/experience", response_model=list[ExperienceOut])
async def list_experience(
    family: str | None = Query(default=None, max_length=96),
    user: UserModel = Depends(get_user),
) -> list[ExperienceOut]:
    """The ledger for this workspace: its own records and the viewer's own
    personal ones, never a colleague's."""
    rows = await db_client.list_experience(
        organization_id=_organization_id(user),
        task_family=family,
        viewer_user_id=user.id,
        include_personal=True,
        limit=100,
    )
    return [
        ExperienceOut(
            id=r.id,
            kind=r.kind,
            task_family=r.task_family,
            skill_slug=r.skill_slug,
            skill_version=r.skill_version,
            outcome=r.outcome,
            split=r.split,
            scope=r.scope,
            summary=experience.summary(r),
            evidence=list(r.evidence or []),
            occurred_at=r.occurred_at.isoformat() if r.occurred_at else None,
        )
        for r in rows
    ]

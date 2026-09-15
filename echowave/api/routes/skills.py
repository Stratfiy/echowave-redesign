"""The Skills shelf, and what an account does with it.

A skill is a procedure a bot is taught -- how to chase an unpaid invoice,
what to check before promising a delivery date. It is not a capability: the
tools a bot can call are attached separately, and a skill naming an action
does not receive it. See ``services/skills/__init__.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.skills import catalogue, shelf

router = APIRouter(prefix="/skills", tags=["skills"])


def _organization_id(user: UserModel) -> int:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


class SkillOnBot(BaseModel):
    """A bot carrying this skill. Named so the picker can tick it."""

    id: int
    name: str


class SkillCard(BaseModel):
    slug: str
    title: str
    description: str
    division: str
    emoji: str
    source: str
    license: str
    #: How long the procedure is, so somebody can tell a checklist from an essay.
    lines: int
    #: Only on an installed skill: the bots carrying it.
    on_bots: list[SkillOnBot] = []


class Attribution(BaseModel):
    source: str
    license: str


class ShelfResponse(BaseModel):
    #: What this account keeps. First, because it is what they came back for.
    installed: list[SkillCard]
    #: Everything else on the shelf.
    skills: list[SkillCard]
    divisions: list[str]
    attributions: list[Attribution]
    max_per_bot: int


@router.get("", response_model=ShelfResponse)
async def list_skills(user: UserModel = Depends(get_user)) -> ShelfResponse:
    return ShelfResponse(**await shelf.shelf(_organization_id(user)))


class SkillDetail(SkillCard):
    #: The procedure itself. Asked for one at a time: a hundred bodies is a
    #: megabyte nobody is reading.
    body: str


@router.get("/{slug}", response_model=SkillDetail)
async def read_skill(
    slug: str = Path(description="The catalogue slug, e.g. sales-coach."),
    user: UserModel = Depends(get_user),
) -> SkillDetail:
    organization_id = _organization_id(user)
    found = catalogue.get(slug)
    if found is None:
        raise HTTPException(status_code=404, detail="No such skill")
    card = found.as_card()
    have = await shelf.installed(organization_id)
    names = await shelf._bot_names(organization_id)
    if found.slug in have:
        card["on_bots"] = [
            {"id": wid, "name": names.get(wid, "a bot")}
            for wid in have[found.slug].on_bots
        ]
    return SkillDetail(**card, body=found.skill.body)


class InstallRequest(BaseModel):
    slug: str = Field(max_length=64)


@router.post("/install")
async def install_skill(
    body: InstallRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    try:
        await shelf.install(organization_id, body.slug, user_id=user.id)
    except shelf.SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"installed": True, "slug": body.slug}


@router.post("/uninstall")
async def uninstall_skill(
    body: InstallRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    await shelf.uninstall(_organization_id(user), body.slug)
    return {"installed": False, "slug": body.slug}


class BotsRequest(BaseModel):
    slug: str = Field(max_length=64)
    #: Exactly the bots this skill should be on. An empty list takes it off
    #: every one of them, which is what unticking the last box means.
    workflow_ids: list[int] = Field(default_factory=list, max_length=200)


@router.post("/bots")
async def set_skill_bots(
    body: BotsRequest, user: UserModel = Depends(get_user)
) -> dict[str, Any]:
    """Put one skill on a set of bots, and take it off the rest."""
    organization_id = _organization_id(user)
    try:
        on = await shelf.set_bots(
            organization_id, body.slug, body.workflow_ids, user_id=user.id
        )
    except shelf.SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slug": body.slug, "workflow_ids": on}


class WorkflowSkillsResponse(BaseModel):
    slugs: list[str]


@router.get("/on/{workflow_id}", response_model=WorkflowSkillsResponse)
async def skills_on_workflow(
    workflow_id: int, user: UserModel = Depends(get_user)
) -> WorkflowSkillsResponse:
    organization_id = _organization_id(user)
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="No such bot here")
    return WorkflowSkillsResponse(
        slugs=await shelf.for_workflow(organization_id, workflow_id)
    )

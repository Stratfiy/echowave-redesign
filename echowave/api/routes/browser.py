"""Decibyl's private browser: the panel's calls, saved logins, the site list.

Thin, like every router here (api/AGENTS.md): resolve the person, read the
row as theirs, hand the press to ``services/browser/session.py``.

- ``/browser/...`` -- the person's own. Behind ``decibyl_browser`` for the
  caller's organisation (a 404 while it is off), and every session read is
  by organisation **and** person: a colleague on the same thread gets the
  same 404 as a stranger, because what a browser saw on somebody's account
  is theirs.
- ``/admin/browser/sites`` -- the staff's allow and deny list. Superadmin
  only, every change written to ``admin_action_log``, and out of the public
  OpenAPI (``/admin/`` path, ``admin-`` tag).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_superuser, get_user
from api.services.browser import channel, cookies, session, sites

router = APIRouter(
    prefix="/browser",
    tags=["browser"],
    dependencies=[Depends(features.require("decibyl_browser", per_organization=True))],
)
admin_router = APIRouter(
    prefix="/admin/browser",
    tags=["admin-browser"],
    dependencies=[Depends(get_superuser)],
)


class BrowserStep(BaseModel):
    at: str
    kind: str
    text: str
    n: int | None = None
    url: str | None = None


class BrowserSessionView(BaseModel):
    session_uuid: str
    task: str
    sites: list[str]
    allowed_verbs: list[str]
    state: str
    state_note: str | None = None
    limits: dict[str, int]
    used: dict[str, int]
    steps: list[BrowserStep]
    pending_label: str | None = None
    pending_event_id: int | None = None
    keep_login_sites: list[str]
    receipt: dict[str, Any] | None = None
    can_keep_logins: bool
    created_at: datetime
    ended_at: datetime | None = None


class BrowserScreen(BaseModel):
    jpeg: str | None = None
    w: int | None = None
    h: int | None = None
    url: str | None = None
    title: str | None = None
    at: str | None = None


class BrowserInput(BaseModel):
    kind: Literal["click", "type", "key", "scroll"]
    x: float | None = Field(default=None, ge=0, le=4096)
    y: float | None = Field(default=None, ge=0, le=4096)
    text: str | None = Field(default=None, max_length=500)
    key: str | None = Field(default=None, max_length=20)
    dy: float | None = Field(default=None, ge=-5000, le=5000)


class BrowserHandback(BaseModel):
    keep_login: bool = False


class BrowserLogin(BaseModel):
    id: int
    site: str
    cookie_count: int
    updated_at: datetime
    last_used_at: datetime | None = None


class BrowserLogins(BaseModel):
    logins: list[BrowserLogin]
    can_keep_logins: bool


class BrowserSiteRule(BaseModel):
    site: str
    rule: Literal["allow", "deny"]
    reason: str = ""
    source: str = "staff"


class BrowserSiteRuleIn(BaseModel):
    site: str = Field(min_length=3, max_length=255)
    rule: Literal["allow", "deny"]
    reason: str = Field(default="", max_length=500)


def _person(user: UserModel) -> tuple[int, int]:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(user.selected_organization_id), int(user.id)


async def _own(session_uuid: str, user: UserModel):
    organization_id, user_id = _person(user)
    row = await db_client.get_browser_session(
        session_uuid, organization_id=organization_id, user_id=user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Browser session not found")
    return row


def _view(row) -> BrowserSessionView:
    pending = row.pending or {}
    return BrowserSessionView(
        session_uuid=row.session_uuid,
        task=row.task,
        sites=list(row.sites or []),
        allowed_verbs=list(row.allowed_verbs or []),
        state=row.state,
        state_note=row.state_note,
        limits=dict(row.limits or {}),
        used=dict(row.used or {}),
        steps=[BrowserStep(**s) for s in (row.steps or []) if isinstance(s, dict)],
        pending_label=pending.get("label"),
        pending_event_id=pending.get("event_id"),
        keep_login_sites=list(row.keep_login_sites or []),
        receipt=row.receipt,
        can_keep_logins=cookies.can_keep(),
        created_at=row.created_at,
        ended_at=row.ended_at,
    )


async def _press(row, command: dict[str, Any]) -> dict[str, bool]:
    try:
        await session.person_command(row, command)
    except session.CommandRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/sessions/{session_uuid}")
async def get_browser_session(
    session_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> BrowserSessionView:
    """The panel: state, steps, limits, what is waiting, the receipt."""
    return _view(await _own(session_uuid, user))


@router.get("/sessions/{session_uuid}/screen")
async def get_browser_screen(
    session_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> BrowserScreen:
    """The latest screenshot while the browser is open; empty after."""
    row = await _own(session_uuid, user)
    if row.state in session.TERMINAL:
        return BrowserScreen()
    screen = await channel.get_screen(row.session_uuid)
    return BrowserScreen(**(screen or {}))


@router.post("/sessions/{session_uuid}/takeover")
async def take_over_browser(
    session_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, bool]:
    return await _press(await _own(session_uuid, user), {"cmd": "takeover"})


@router.post("/sessions/{session_uuid}/input")
async def send_browser_input(
    session_uuid: str,
    body: BrowserInput,
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, bool]:
    """A click, keys or a scroll while the person has the browser. Passed
    on and not kept anywhere."""
    command = {"cmd": "input", **body.model_dump(exclude_none=True)}
    return await _press(await _own(session_uuid, user), command)


@router.post("/sessions/{session_uuid}/handback")
async def hand_back_browser(
    session_uuid: str,
    body: BrowserHandback,
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, bool]:
    return await _press(
        await _own(session_uuid, user),
        {"cmd": "handback", "keep_login": body.keep_login},
    )


@router.post("/sessions/{session_uuid}/stop")
async def stop_browser(
    session_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, bool]:
    return await _press(await _own(session_uuid, user), {"cmd": "stop"})


@router.get("/logins")
async def list_browser_logins(
    user: Annotated[UserModel, Depends(get_user)],
) -> BrowserLogins:
    """The sites this person stays signed in to. Theirs alone."""
    organization_id, user_id = _person(user)
    rows = await db_client.list_browser_logins(
        organization_id=organization_id, user_id=user_id
    )
    return BrowserLogins(
        logins=[
            BrowserLogin(
                id=r.id,
                site=r.site,
                cookie_count=r.cookie_count,
                updated_at=r.updated_at,
                last_used_at=r.last_used_at,
            )
            for r in rows
        ],
        can_keep_logins=cookies.can_keep(),
    )


@router.delete("/logins/{login_id}")
async def delete_browser_login(
    login_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, bool]:
    """Forget one saved login: the cookies are deleted, not hidden."""
    organization_id, user_id = _person(user)
    if not await db_client.delete_browser_login(
        login_id, organization_id=organization_id, user_id=user_id
    ):
        raise HTTPException(status_code=404, detail="Saved login not found")
    return {"ok": True}


# --- staff -------------------------------------------------------------------------


@admin_router.get("/sites")
async def list_browser_site_rules() -> list[BrowserSiteRule]:
    """Every rule in force: the defaults and the staff's, each with its source."""
    return [
        BrowserSiteRule(**r) for r in sites.effective_list(await sites.staff_rules())
    ]


@admin_router.put("/sites")
async def set_browser_site_rule(
    body: BrowserSiteRuleIn, user: Annotated[UserModel, Depends(get_superuser)]
) -> BrowserSiteRule:
    site = sites.normalise_site(body.site)
    if not site or "." not in site:
        raise HTTPException(status_code=400, detail="Give a site such as example.com")
    row = await db_client.set_browser_site_rule(
        site=site, rule=body.rule, reason=body.reason.strip(), set_by_user_id=user.id
    )
    return BrowserSiteRule(site=row.site, rule=row.rule, reason=row.reason)


@admin_router.delete("/sites/{site}")
async def delete_browser_site_rule(
    site: str, user: Annotated[UserModel, Depends(get_superuser)]
) -> dict[str, bool]:
    """Remove a staff rule; a default deny for the site applies again."""
    if not await db_client.delete_browser_site_rule(
        sites.normalise_site(site), actor_user_id=user.id
    ):
        raise HTTPException(status_code=404, detail="No staff rule for that site")
    return {"ok": True}

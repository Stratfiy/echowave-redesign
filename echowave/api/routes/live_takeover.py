"""Joining a live call: barge, take over, let the agent answer, hand back.

Thin, as routes are (``services/live_takeover``). Under the same prefix and
the same rules as listening (``routes/live_calls``): the workspace comes from
the session, a run in another workspace is a 404, an ended call a 409.
Everything here is a 404 unless both ``live_takeover`` and
``live_supervision`` are on for the workspace -- the talking socket too.

The talking socket (``/ws/live-calls/{run_id}/talk``) is the other half of
the listen socket: the browser sends the supervisor's microphone up it, one
binary packet per slice in the listen socket's audio format with side
``s``; the server sends back only status. It opens only for the person who
has joined the call, and while it is open it tells the call so once a
second -- so a supervisor whose browser closes, crashes or loses its
connection is noticed within ``LIVE_TAKEOVER_RECOVERY_SECONDS`` and the agent
takes the call back.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services import features
from api.services.auth.depends import (
    get_user,
    get_user_ws,
    require_organization_role,
    ws_accept_subprotocol,
)
from api.services.live_supervision import access as listen_access
from api.services.live_supervision import channels as live_channels
from api.services.live_takeover import access, registry

FEATURE = "live_takeover"
NEEDS = "live_supervision"

router = APIRouter(
    prefix="/live-calls",
    tags=["live-calls"],
    dependencies=[
        Depends(features.require(NEEDS, per_organization=True)),
        Depends(features.require(FEATURE, per_organization=True)),
    ],
)
#: The socket checks both flags itself (see ``routes/live_calls``).
ws_router = APIRouter(tags=["live-calls"])

#: How often the talking socket tells the call its supervisor is still there.
PING_SECONDS = 1.0

REFUSALS = {
    listen_access.NOT_PERMITTED: (
        "Only the agent's owner or a workspace admin can join its calls."
    ),
    listen_access.SETTING_OFF: (
        "Live listening is off for this workspace. An admin can turn it on here."
    ),
    access.JOINING_OFF: (
        "Joining calls is off for this workspace. An admin can turn it on here."
    ),
}


class TakeoverState(BaseModel):
    run_id: int
    #: ``ai`` while the agent has the call; ``barge`` or ``takeover``.
    mode: str
    by: str | None = None
    by_user_id: int | None = None
    since: str | None = None
    #: In a barge, the supervisor let the agent answer.
    agent_answering: bool = False
    #: The person asking is the one on the call.
    mine: bool = False
    can_join: bool
    #: Why not: ``joining_off``, ``taken``.
    blocked: str | None = None
    allow_joining: bool
    can_change_setting: bool
    #: ``pipeline``: speak from this browser. ``plivo_mpc``: by phone.
    bridge: str
    needs_phone: bool = False
    #: How long the agent waits for a supervisor who drops off.
    recovery_seconds: float


class JoinRequest(BaseModel):
    #: ``barge``: the agent is paused and stays on the line. ``takeover``:
    #: the agent says nothing until the call is handed back.
    mode: str = Field(pattern="^(barge|takeover)$")
    #: Only when joining by phone (``needs_phone``).
    phone: str | None = Field(default=None, max_length=32)


class JoinResponse(BaseModel):
    mode: str
    by: str | None = None
    by_user_id: int | None = None


class HandBackResponse(BaseModel):
    mode: str


class JoinSettingsRequest(BaseModel):
    allow_joining: bool


class JoinSettingsResponse(BaseModel):
    allow_joining: bool


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(user.selected_organization_id)


async def _guarded(coro):
    try:
        return await coro
    except registry.NotFound as exc:
        raise HTTPException(status_code=404, detail="Call not found") from exc
    except registry.NotLive as exc:
        raise HTTPException(status_code=409, detail="This call has ended.") from exc
    except registry.Refused as exc:
        raise HTTPException(
            status_code=403, detail=REFUSALS.get(exc.reason, exc.reason)
        ) from exc
    except registry.Conflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc


@router.put("/join-settings", response_model=JoinSettingsResponse)
async def set_join_settings(
    body: JoinSettingsRequest,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
):
    """Switch "Allow supervisors to join calls" (admins and owners)."""
    _organization_id(user)
    viewer = await listen_access.viewer_for(user)
    allowed = await access.set_allow_joining(viewer, body.allow_joining)
    return JoinSettingsResponse(allow_joining=allowed)


@router.get("/{run_id}/takeover", response_model=TakeoverState)
async def takeover_state(run_id: int, user: Annotated[UserModel, Depends(get_user)]):
    """Who has the call, and whether this person could join it."""
    _organization_id(user)
    viewer = await listen_access.viewer_for(user)
    described = await _guarded(registry.describe(viewer, run_id))
    return TakeoverState(**described.as_dict())


@router.post("/{run_id}/takeover", response_model=JoinResponse)
async def join_call(
    run_id: int,
    body: JoinRequest,
    user: Annotated[UserModel, Depends(get_user)],
):
    """Join the call (or switch between barge and take-over). The agent
    stops speaking at once; the caller hears you from when you start
    talking on the talking socket (or answer your phone)."""
    _organization_id(user)
    viewer = await listen_access.viewer_for(user)
    claim = await _guarded(
        registry.join(viewer, run_id, mode=body.mode, phone=body.phone)
    )
    return JoinResponse(
        mode=claim["mode"], by=claim.get("by"), by_user_id=claim.get("by_user_id")
    )


@router.post("/{run_id}/let-agent-answer", status_code=204)
async def let_agent_answer(run_id: int, user: Annotated[UserModel, Depends(get_user)]):
    """In a barge: the agent answers the caller now, until you next speak."""
    _organization_id(user)
    viewer = await listen_access.viewer_for(user)
    await _guarded(registry.let_agent_answer(viewer, run_id))


@router.post("/{run_id}/hand-back", response_model=HandBackResponse)
async def hand_back(run_id: int, user: Annotated[UserModel, Depends(get_user)]):
    """Give the call back to the agent, which picks it up with a line."""
    _organization_id(user)
    viewer = await listen_access.viewer_for(user)
    result = await _guarded(registry.hand_back(viewer, run_id))
    return HandBackResponse(**result)


@ws_router.websocket("/ws/live-calls/{run_id}/talk")
async def talk_on_call(
    websocket: WebSocket,
    run_id: int,
    user: UserModel = Depends(get_user_ws),
):
    """The supervisor's microphone, to a call they have joined."""
    await websocket.accept(subprotocol=ws_accept_subprotocol(websocket))

    async def refuse(code: int, detail: str) -> None:
        await websocket.send_text(json.dumps({"type": "error", "detail": detail}))
        await websocket.close(code=code, reason=detail[:120])

    organization_id = user.selected_organization_id
    if (
        not organization_id
        or not features.is_on(FEATURE, organization_id)
        or not features.is_on(NEEDS, organization_id)
    ):
        await refuse(4404, "Not Found")
        return
    viewer = await listen_access.viewer_for(user)
    try:
        await registry.require_talker(viewer, run_id)
    except registry.NotFound:
        await refuse(4404, "Call not found")
        return
    except registry.NotLive:
        await refuse(4409, "This call has ended.")
        return
    except registry.Refused as exc:
        await refuse(4403, REFUSALS.get(exc.reason, exc.reason))
        return
    except registry.Conflict as exc:
        await refuse(4409, exc.detail)
        return

    await websocket.send_text(json.dumps({"type": "live"}))
    await registry.ping(viewer, run_id)

    async def presence() -> str:
        """Once a second: still the one on the call, and the call still on."""
        while True:
            await asyncio.sleep(PING_SECONDS)
            if not await live_channels.redis().exists(live_channels.meta_key(run_id)):
                return "ended"
            if not await registry.still_holds(viewer, run_id):
                return "released"
            if not await registry.ping(viewer, run_id):
                return "ended"

    async def receive() -> str:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return "gone"
            data = message.get("bytes")
            if data:
                await registry.forward_mic(run_id, data)

    tasks = [asyncio.create_task(presence()), asyncio.create_task(receive())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        outcome = next(iter(done)).result() if done else "gone"
        if outcome in ("ended", "released"):
            await websocket.send_text(json.dumps({"type": outcome}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("Talking socket for run {} stopped: {}", run_id, exc)
    finally:
        for task in tasks:
            task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed
            pass

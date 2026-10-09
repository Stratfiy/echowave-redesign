"""Live calls: the list, the listen panel's data, whispers, the setting.

Thin, as routes are: the workspace comes from the session, never from the
request, and every run id is read back through it -- another workspace's
call is a 404. Everything here is a 404 while ``live_supervision`` is off
for the workspace, including the listening socket.

The socket (``/ws/live-calls/{run_id}``) is receive-only: JSON text
messages for the transcript, whispers, the step and the end; binary
messages for audio when ``?audio=1`` (see ``live_supervision.channels``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from loguru import logger
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.routes.agent_timeline import TimelineEvent, _as_event
from api.services import features
from api.services.auth.depends import (
    get_user,
    get_user_ws,
    require_organization_role,
    ws_accept_subprotocol,
)
from api.services.live_supervision import access, consent, registry, whisper
from api.services.live_supervision.listener import ListenerPump

FEATURE = "live_supervision"

router = APIRouter(
    prefix="/live-calls",
    tags=["live-calls"],
    dependencies=[Depends(features.require(FEATURE, per_organization=True))],
)
#: The socket cannot carry the router's session dependency (it reads a
#: header a WebSocket does not have); it checks the flag itself.
ws_router = APIRouter(tags=["live-calls"])

REFUSALS = {
    access.NOT_PERMITTED: (
        "Only the agent's owner or a workspace admin can listen to its calls."
    ),
    access.SETTING_OFF: (
        "Live listening is off for this workspace. An admin can turn it on here."
    ),
    "empty": "Type an instruction first.",
}


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(user.selected_organization_id)


class LiveCallItem(BaseModel):
    run_id: int
    workflow_id: int
    agent_name: str
    #: ``inbound``, ``outbound`` or ``web``.
    direction: str
    started_at: str
    duration_seconds: int
    step: str | None = None
    #: The other party's number: in full for somebody who may listen to
    #: the call, the last four digits otherwise; null on a web call.
    caller: str | None = None
    caller_masked: bool = False
    can_listen: bool
    #: Why not, when ``can_listen`` is false: ``not_permitted`` or
    #: ``setting_off``.
    blocked: str | None = None


class LiveCallsResponse(BaseModel):
    calls: list[LiveCallItem]
    allow_listening: bool
    #: Whether this person may switch "Allow live listening".
    can_change_setting: bool
    #: More calls are live than the list shows.
    more: bool = False


class LiveSettingsRequest(BaseModel):
    allow_listening: bool


class LiveSettingsResponse(BaseModel):
    allow_listening: bool


class ConsentNotice(BaseModel):
    mentions_monitoring: bool
    warning: str | None = None


class LiveCallDetail(BaseModel):
    call: LiveCallItem
    #: The final lines and whispers so far, in order (``seq``).
    transcript: list[dict[str, Any]]
    consent: ConsentNotice


class WhisperRequest(BaseModel):
    text: str = Field(min_length=1, max_length=whisper.MAX_CHARS)
    #: Cut the agent off and have it answer now, rather than on its next turn.
    urgent: bool = False


class WhisperResponse(BaseModel):
    id: str
    at: str
    urgent: bool
    text: str


def _refuse(reason: str) -> HTTPException:
    return HTTPException(status_code=403, detail=REFUSALS.get(reason, reason))


async def _listener_call(user: UserModel, run_id: int):
    viewer = await access.viewer_for(user)
    try:
        call = await registry.require_listener(viewer, run_id)
    except registry.NotFound as exc:
        raise HTTPException(status_code=404, detail="Call not found") from exc
    except registry.NotLive as exc:
        raise HTTPException(status_code=409, detail="This call has ended.") from exc
    except registry.Refused as exc:
        raise _refuse(exc.reason) from exc
    return viewer, call


@router.get("", response_model=LiveCallsResponse)
async def list_live_calls(
    user: Annotated[UserModel, Depends(get_user)],
    workflow_id: int | None = Query(None),
):
    """The workspace's calls in progress, oldest first; one agent's with
    ``workflow_id``."""
    _organization_id(user)
    viewer = await access.viewer_for(user)
    calls, allowed, more = await registry.live_calls(viewer, workflow_id=workflow_id)
    return LiveCallsResponse(
        calls=[LiveCallItem(**c.as_dict()) for c in calls],
        allow_listening=allowed,
        can_change_setting=viewer.is_admin,
        more=more,
    )


@router.put("/settings", response_model=LiveSettingsResponse)
async def set_live_settings(
    body: LiveSettingsRequest,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
):
    """Switch "Allow live listening" for the workspace (admins and owners)."""
    _organization_id(user)
    viewer = await access.viewer_for(user)
    allowed = await access.set_allow_listening(viewer, body.allow_listening)
    return LiveSettingsResponse(allow_listening=allowed)


@router.get("/{run_id}", response_model=LiveCallDetail)
async def live_call_detail(run_id: int, user: Annotated[UserModel, Depends(get_user)]):
    """What the listen panel opens with: the call, its transcript so far,
    and whether its opening tells callers about monitoring."""
    _organization_id(user)
    _viewer, call = await _listener_call(user, run_id)
    return LiveCallDetail(
        call=LiveCallItem(**call.as_dict()),
        transcript=await registry.backlog(run_id),
        consent=ConsentNotice(**consent.notice(call.definition)),
    )


@router.post("/{run_id}/whisper", response_model=WhisperResponse)
async def whisper_to_call(
    run_id: int,
    body: WhisperRequest,
    user: Annotated[UserModel, Depends(get_user)],
):
    """Give the agent a private instruction. The caller never hears it."""
    _organization_id(user)
    viewer = await access.viewer_for(user)
    try:
        sent = await registry.send_whisper(
            viewer, run_id, text=body.text, urgent=body.urgent
        )
    except registry.NotFound as exc:
        raise HTTPException(status_code=404, detail="Call not found") from exc
    except registry.NotLive as exc:
        raise HTTPException(status_code=409, detail="This call has ended.") from exc
    except registry.Refused as exc:
        raise _refuse(exc.reason) from exc
    return WhisperResponse(**sent)


@router.post("/{run_id}/consent-fix", response_model=TimelineEvent)
async def propose_consent_fix(
    run_id: int, user: Annotated[UserModel, Depends(get_user)]
):
    """Draft the greeting with a monitoring sentence and return the card to
    publish or discard. Changes nothing a caller hears until published."""
    organization_id = _organization_id(user)
    _viewer, call = await _listener_call(user, run_id)
    result = await consent.propose_fix(
        organization_id=organization_id, workflow_id=call.workflow_id
    )
    if result.get("status") != "proposed":
        raise HTTPException(
            status_code=409, detail=result.get("reason") or "Could not propose that."
        )
    row = (
        await db_client.get_agent_event(
            int(result["event_id"]), organization_id=organization_id
        )
        if result.get("event_id")
        else None
    )
    if row is None:
        raise HTTPException(
            status_code=409, detail="The change was drafted but its card is missing."
        )
    return _as_event(row)


@ws_router.websocket("/ws/live-calls/{run_id}")
async def listen_to_call(
    websocket: WebSocket,
    run_id: int,
    audio: bool = Query(False),
    user: UserModel = Depends(get_user_ws),
):
    """Receive-only: the call's words, whispers and (with ``audio``) sound."""
    await websocket.accept(subprotocol=ws_accept_subprotocol(websocket))

    async def refuse(code: int, detail: str) -> None:
        await websocket.send_text(json.dumps({"type": "error", "detail": detail}))
        await websocket.close(code=code, reason=detail[:120])

    organization_id = user.selected_organization_id
    if not organization_id or not features.is_on(FEATURE, organization_id):
        await refuse(4404, "Not Found")
        return
    viewer = await access.viewer_for(user)
    try:
        call = await registry.require_listener(viewer, run_id)
    except registry.NotFound:
        await refuse(4404, "Call not found")
        return
    except registry.NotLive:
        await refuse(4409, "This call has ended.")
        return
    except registry.Refused as exc:
        await refuse(4403, REFUSALS.get(exc.reason, exc.reason))
        return

    pump = ListenerPump(
        run_id,
        send_text=websocket.send_text,
        send_bytes=websocket.send_bytes,
        audio=audio,
    )
    await pump.subscribe()
    try:
        # Subscribed before the backlog is read, so nothing falls between
        # them; the panel merges the two by ``seq``.
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "call": call.as_dict(),
                    "transcript": await registry.backlog(run_id),
                    "tag": whisper.tag(viewer.name),
                }
            )
        )
        await registry.note_listening(viewer, call, audio=audio)

        async def watch_close() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    pump.finish()
                    return

        tasks = [
            asyncio.create_task(pump.read()),
            asyncio.create_task(pump.write()),
            asyncio.create_task(watch_close()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # The writer finishes what is queued (the "ended" line) unless the
        # browser is gone.
        writer = tasks[1]
        if writer in pending and not tasks[2].done():
            try:
                await asyncio.wait_for(writer, timeout=2.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                pass
        for task in tasks:
            task.cancel()
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                exc = task.exception()
                if not isinstance(exc, WebSocketDisconnect):
                    logger.debug("Live listener for run {} stopped: {}", run_id, exc)
    except WebSocketDisconnect:
        pass
    finally:
        await pump.close()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed
            pass

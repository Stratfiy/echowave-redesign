"""The huddle: a voice conversation with an agent as a teammate.

Every route is a 404 while ``huddle`` is off. A session is the caller's own
(organisation *and* person) and a huddle's -- a Talk session id here is not
found, as another person's is. The agent must be this workspace's and
visible to the caller. Handlers stay thin: the rules live in
``services/huddle/`` and, for the session record, ``services/voice/``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features, member_preferences, quotas
from api.services.auth.depends import get_user
from api.services.huddle import FLAG, live_call, record
from api.services.huddle import session as huddle_session
from api.services.voice import sessions

router = APIRouter(
    prefix="/huddle",
    tags=["huddle"],
    dependencies=[Depends(features.require(FLAG, per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = getattr(user, "selected_organization_id", None)
    if not organization_id:
        raise HTTPException(status_code=400, detail="No workspace selected")
    return organization_id


class HuddleSession(BaseModel):
    id: int
    workflow_id: int
    agent_name: str | None = None
    state: str
    phase: str | None
    state_version: int
    muted: bool
    language: str | None
    reconnects: int
    lost_ms: int
    end_reason: str | None
    created_at: str | None
    connected_at: str | None
    ended_at: str | None
    #: What the agent kept from earlier huddles with this person; on start.
    notes: list[str] = []


class MoveHuddle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    to: Literal["live", "reconnecting", "ended", "failed"]
    phase: Literal["listening", "processing", "speaking"] | None = None
    muted: bool | None = None
    end_reason: str | None = Field(default=None, max_length=48)


class EndHuddle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=48)


class HuddleNotes(BaseModel):
    notes: list[str]


def _shape(session: dict[str, Any], **extra: Any) -> HuddleSession:
    huddle = (session.get("config") or {}).get("huddle") or {}
    return HuddleSession(
        **{
            key: session.get(key)
            for key in HuddleSession.model_fields
            if key not in ("workflow_id", "agent_name", "notes")
        },
        workflow_id=huddle.get("workflow_id"),
        agent_name=huddle.get("agent_name"),
        **extra,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No such huddle")


async def _own(user: UserModel, session_id: int) -> dict[str, Any]:
    session = await huddle_session.get(
        organization_id=_organization_id(user), user_id=user.id, session_id=session_id
    )
    if session is None:
        raise _not_found()
    return session


async def _agent(user: UserModel, workflow_id: int) -> None:
    try:
        await huddle_session.agent_for(
            organization_id=_organization_id(user),
            user_id=user.id,
            workflow_id=workflow_id,
        )
    except huddle_session.NotFound as exc:
        raise HTTPException(status_code=404, detail="No such agent") from exc


@router.post("/{workflow_id}/sessions", response_model=HuddleSession, status_code=201)
async def start_huddle(
    workflow_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleSession:
    """Open a huddle with this agent in ``connecting``; the audio connects
    over ``/ws/huddle/{id}``. Refused before anything connects when the agent
    is not this person's to see, voice is not set up, the day's voice minutes
    are spent, or they already have a live voice session."""
    organization_id = _organization_id(user)
    try:
        started = await huddle_session.start(
            organization_id=organization_id, user_id=user.id, workflow_id=workflow_id
        )
    except huddle_session.NotFound as exc:
        raise HTTPException(status_code=404, detail="No such agent") from exc
    except huddle_session.NotReady as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.state.state,
                "message": exc.state.reason,
                "next_step": exc.state.next_step,
            },
        ) from exc
    except quotas.QuotaExceeded as exc:
        prefs = await member_preferences.get(user.id)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "voice_limit_reached",
                "message": quotas.message(exc.usage, prefs.get("timezone")),
            },
        ) from exc
    except sessions.AlreadyLive as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "already_live",
                "message": "You already have a live voice session open.",
                "session_id": exc.session_id,
            },
        ) from exc
    return _shape(started, notes=started.get("notes") or [])


@router.get("/sessions/{session_id}", response_model=HuddleSession)
async def get_huddle(
    session_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleSession:
    return _shape(await _own(user, session_id))


@router.post("/sessions/{session_id}/move", response_model=HuddleSession)
async def move_huddle(
    session_id: int, body: MoveHuddle, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleSession:
    """Mute, phase and reconnect moves; a stale version is 409 with the
    current session."""
    await _own(user, session_id)
    try:
        moved = await sessions.move(
            organization_id=_organization_id(user),
            user_id=user.id,
            session_id=session_id,
            expected_version=body.expected_version,
            to=body.to,
            phase=body.phase,
            muted=body.muted,
            end_reason=body.end_reason,
        )
    except sessions.NotFound as exc:
        raise _not_found() from exc
    except sessions.Stale as exc:
        raise HTTPException(
            status_code=409, detail={"code": "stale", "current": exc.current}
        ) from exc
    except sessions.NotAllowed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _shape(moved)


@router.post("/sessions/{session_id}/heartbeat", response_model=HuddleSession)
async def heartbeat_huddle(
    session_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleSession:
    await _own(user, session_id)
    try:
        return _shape(
            await sessions.heartbeat(
                organization_id=_organization_id(user),
                user_id=user.id,
                session_id=session_id,
            )
        )
    except sessions.NotFound as exc:
        raise _not_found() from exc
    except sessions.NotAllowed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/end", response_model=HuddleSession)
async def end_huddle(
    session_id: int, body: EndHuddle, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleSession:
    """End always works, whatever the version, and twice is once."""
    await _own(user, session_id)
    try:
        return _shape(
            await sessions.end(
                organization_id=_organization_id(user),
                user_id=user.id,
                session_id=session_id,
                reason=body.reason,
            )
        )
    except sessions.NotFound as exc:
        raise _not_found() from exc


@router.get("/{workflow_id}/notes", response_model=HuddleNotes)
async def huddle_notes(
    workflow_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleNotes:
    """What this agent keeps from huddles with the caller: theirs alone."""
    await _agent(user, workflow_id)
    return HuddleNotes(
        notes=await record.notes_for(
            organization_id=_organization_id(user),
            user_id=user.id,
            workflow_id=workflow_id,
        )
    )


@router.delete("/{workflow_id}/notes", response_model=HuddleNotes)
async def forget_huddle_notes(
    workflow_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleNotes:
    """Forget them. The transcripts stay on the thread; only the notes go."""
    await _agent(user, workflow_id)
    await record.forget_notes(
        organization_id=_organization_id(user),
        user_id=user.id,
        workflow_id=workflow_id,
    )
    return HuddleNotes(notes=[])


# --- the huddle as a live call's whisper channel (with live_supervision) ----

_LIVE = [Depends(features.require(live_call.FLAG, per_organization=True))]


class HuddleLiveCall(BaseModel):
    #: The agent is on one live call this person may whisper to: what they
    #: say in the huddle goes to it.
    live: bool
    #: "This call only", while ``live``.
    label: str | None = None
    run_id: int | None = None
    direction: str | None = None
    caller: str | None = None
    started_at: str | None = None
    #: How many live calls the agent is on (several: none is chosen).
    calls: int = 0


class HuddleWhisper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)


class HuddleWhisperSent(BaseModel):
    id: str
    at: str
    text: str
    run_id: int
    label: str


@router.get(
    "/{workflow_id}/live-call", response_model=HuddleLiveCall, dependencies=_LIVE
)
async def huddle_live_call(
    workflow_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HuddleLiveCall:
    """Whether the huddle is this agent's live call's whisper channel now."""
    await _agent(user, workflow_id)
    target, count = await live_call.find(
        organization_id=_organization_id(user),
        user_id=user.id,
        workflow_id=workflow_id,
    )
    if target is None:
        return HuddleLiveCall(live=False, calls=count)
    return HuddleLiveCall(live=True, calls=count, **target.as_dict())


@router.post(
    "/{workflow_id}/whisper", response_model=HuddleWhisperSent, dependencies=_LIVE
)
async def huddle_whisper(
    workflow_id: int,
    body: HuddleWhisper,
    user: Annotated[UserModel, Depends(get_user)],
) -> HuddleWhisperSent:
    """A typed line from the huddle, to the agent's live call as a whisper.
    409 when there is no single live call to send it to."""
    from api.services.live_supervision import registry as live_registry

    await _agent(user, workflow_id)
    try:
        sent = await live_call.whisper(
            organization_id=_organization_id(user),
            user_id=user.id,
            workflow_id=workflow_id,
            text=body.text,
        )
    except live_call.NoLiveCall as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except live_registry.NotLive as exc:
        raise HTTPException(status_code=409, detail="This call has ended.") from exc
    except (live_registry.NotFound, live_registry.Refused) as exc:
        raise HTTPException(
            status_code=409, detail="The agent isn't on a call you can whisper to."
        ) from exc
    return HuddleWhisperSent(
        id=str(sent["id"]),
        at=str(sent["at"]),
        text=str(sent["text"]),
        run_id=int(sent["run_id"]),
        label=live_call.THIS_CALL_ONLY,
    )

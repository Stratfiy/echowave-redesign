"""Live voice with Decibyl, voice settings, and the Call and Appointment
runtime's settings (launch stream `voice`).

Every route is a 404 while its flag is off. Sessions and latency are the
caller's own (organisation *and* person); appointment settings belong to the
workspace and only its admins change them. Handlers stay thin: the rules
live in ``services/voice/``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features, member_preferences, quotas
from api.services.auth.depends import get_staff, get_user
from api.services.voice import appointments, catalogue, latency, readiness, sessions

router = APIRouter(prefix="/voice", tags=["voice"])
admin_router = APIRouter(
    prefix="/admin/voice", tags=["admin-voice"], dependencies=[Depends(get_staff)]
)

_voice = Depends(features.require("decibyl_voice", per_organization=True))
_latency = Depends(features.require("voice_latency", per_organization=True))
_appointments = Depends(features.require("call_appointment", per_organization=True))


def _organization_id(user: UserModel) -> int:
    organization_id = getattr(user, "selected_organization_id", None)
    if not organization_id:
        raise HTTPException(status_code=400, detail="No workspace selected")
    return organization_id


# --- readiness ----------------------------------------------------------------


class ReadinessState(BaseModel):
    state: Literal["available", "needs_setup", "disabled_by_policy", "unavailable"]
    reason: str | None = None
    next_step: str | None = None
    notes: list[str] = []


class VoiceReadiness(BaseModel):
    live_voice: ReadinessState
    calls: ReadinessState | None = None
    language: str | None = None
    voice: str | None = None
    captions: bool = True


def _state(value: readiness.Readiness) -> ReadinessState:
    return ReadinessState(
        state=value.state,
        reason=value.reason,
        next_step=value.next_step,
        notes=value.notes,
    )


@router.get("/readiness", response_model=VoiceReadiness, dependencies=[_voice])
async def voice_readiness(
    user: Annotated[UserModel, Depends(get_user)],
) -> VoiceReadiness:
    """Whether Talk can start for this person, and why not if not."""
    organization_id = _organization_id(user)
    prefs = await member_preferences.get(user.id)
    live = await readiness.live_voice(
        organization_id=organization_id, user_id=user.id, language=prefs.get("language")
    )
    calls = None
    if features.is_on("call_for_me", organization_id):
        calls = _state(await readiness.calls(organization_id=organization_id))
    return VoiceReadiness(
        live_voice=_state(live),
        calls=calls,
        language=prefs.get("language"),
        voice=prefs.get("voice"),
        captions=prefs.get("captions") is not False,
    )


# --- sessions -------------------------------------------------------------------


class VoiceSession(BaseModel):
    id: int
    thread_id: str | None
    state: str
    phase: str | None
    state_version: int
    muted: bool
    language: str | None
    voice: str | None
    config: dict[str, Any]
    reconnects: int
    gaps: list[dict[str, Any]]
    lost_ms: int
    end_reason: str | None
    created_at: str | None
    connected_at: str | None
    ended_at: str | None


class StartSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str | None = Field(default=None, max_length=64)


class MoveSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    to: Literal["live", "reconnecting", "ended", "failed"]
    phase: Literal["listening", "processing", "speaking"] | None = None
    muted: bool | None = None
    end_reason: str | None = Field(default=None, max_length=48)


class EndSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=48)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No such voice session")


@router.post(
    "/sessions", response_model=VoiceSession, status_code=201, dependencies=[_voice]
)
async def start_session(
    body: StartSession, user: Annotated[UserModel, Depends(get_user)]
) -> VoiceSession:
    """Open a session in ``connecting``; the audio connects over
    ``/ws/voice/{id}``. Refused before anything connects when voice is not
    set up, the person is over their daily voice minutes, or they already
    have a live session."""
    from api.routes.agent_timeline import _assert_thread_is_theirs

    organization_id = _organization_id(user)
    await _assert_thread_is_theirs(user, organization_id, body.thread_id)
    prefs = await member_preferences.get(user.id)
    state = await readiness.live_voice(
        organization_id=organization_id, user_id=user.id, language=prefs.get("language")
    )
    if state.state != readiness.AVAILABLE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": state.state,
                "message": state.reason,
                "next_step": state.next_step,
            },
        )
    try:
        await quotas.check(user.id, quotas.VOICE_MINUTES)
    except quotas.QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "voice_limit_reached",
                "message": quotas.message(exc.usage, prefs.get("timezone")),
            },
        ) from exc
    voice = prefs.get("voice")
    language = state.config.get("language")
    config = {
        **state.config,
        "speed": prefs.get("speaking_speed"),
        "captions": prefs.get("captions") is not False,
        # The person's voice (Settings -> Voice and language) applies only if
        # the workspace's voice model has that speaker and speaks the
        # session's language; it is never silently swapped for another.
        "voice_compatible": (
            catalogue.compatible(
                voice, language, (state.config.get("tts") or {}).get("model")
            )
            if voice
            else None
        ),
    }
    try:
        session = await sessions.start(
            organization_id=organization_id,
            user_id=user.id,
            thread_id=body.thread_id,
            language=language,
            voice=voice if config["voice_compatible"] else None,
            config=config,
        )
    except sessions.AlreadyLive as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "already_live",
                "message": "You already have a live voice session open.",
                "session_id": exc.session_id,
            },
        ) from exc
    return VoiceSession(**session)


@router.get(
    "/sessions/{session_id}", response_model=VoiceSession, dependencies=[_voice]
)
async def get_session(
    session_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> VoiceSession:
    session = await sessions.get(
        organization_id=_organization_id(user), user_id=user.id, session_id=session_id
    )
    if session is None:
        raise _not_found()
    return VoiceSession(**session)


@router.post(
    "/sessions/{session_id}/move", response_model=VoiceSession, dependencies=[_voice]
)
async def move_session(
    session_id: int, body: MoveSession, user: Annotated[UserModel, Depends(get_user)]
) -> VoiceSession:
    """Mute, phase and reconnect moves. A stale version is 409 with the
    current session, so the screen shows the truth rather than its guess."""
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
    return VoiceSession(**moved)


@router.post(
    "/sessions/{session_id}/heartbeat",
    response_model=VoiceSession,
    dependencies=[_voice],
)
async def heartbeat(
    session_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> VoiceSession:
    try:
        return VoiceSession(
            **await sessions.heartbeat(
                organization_id=_organization_id(user),
                user_id=user.id,
                session_id=session_id,
            )
        )
    except sessions.NotFound as exc:
        raise _not_found() from exc
    except sessions.NotAllowed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/end", response_model=VoiceSession, dependencies=[_voice]
)
async def end_session(
    session_id: int, body: EndSession, user: Annotated[UserModel, Depends(get_user)]
) -> VoiceSession:
    """End always works, whatever the version, and twice is once."""
    try:
        return VoiceSession(
            **await sessions.end(
                organization_id=_organization_id(user),
                user_id=user.id,
                session_id=session_id,
                reason=body.reason,
            )
        )
    except sessions.NotFound as exc:
        raise _not_found() from exc


# --- latency -------------------------------------------------------------------


class TurnTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_index: int = Field(ge=0, le=10_000)
    response_ms: float | None = None
    interruption_ms: float | None = None
    interrupted: bool = False


class TurnTimingRecorded(BaseModel):
    session_id: int
    turn_index: int
    response_ms: int | None
    interruption_ms: int | None
    interrupted: bool


@router.post(
    "/sessions/{session_id}/turns",
    response_model=TurnTimingRecorded,
    dependencies=[_latency],
)
async def record_turn(
    session_id: int, body: TurnTiming, user: Annotated[UserModel, Depends(get_user)]
) -> TurnTimingRecorded:
    """What the person's device measured for one turn (handoff 12)."""
    try:
        recorded = await latency.record_client(
            organization_id=_organization_id(user),
            user_id=user.id,
            session_id=session_id,
            turn_index=body.turn_index,
            response_ms=body.response_ms,
            interruption_ms=body.interruption_ms,
            interrupted=body.interrupted,
        )
    except LookupError as exc:
        raise _not_found() from exc
    except latency.LatencyInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if recorded is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return TurnTimingRecorded(**recorded)


@admin_router.get("/latency", dependencies=[Depends(features.require("voice_latency"))])
async def latency_summary(
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Staff: response and interruption p50/p95 with sample sizes, by
    language, channel and voice provider (design screen 40)."""
    return await latency.summary(days=days, organization_id=organization_id)


# --- Call and Appointment ----------------------------------------------------


class AppointmentPolicy(BaseModel):
    booking: Literal["off", "suggest", "book"]
    duration_minutes: int
    lead_minutes: int
    horizon_days: int
    services: list[str]
    verification: Literal["details", "known_caller"]
    escalate_to: str | None
    call_workflow_id: int | None
    revision: int
    updated_at: str | None


class AppointmentPolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    booking: Literal["off", "suggest", "book"] | None = None
    duration_minutes: int | None = None
    lead_minutes: int | None = None
    horizon_days: int | None = None
    services: list[str] | None = None
    verification: Literal["details", "known_caller"] | None = None
    escalate_to: str | None = None
    call_workflow_id: int | None = None


class Appointment(BaseModel):
    id: int
    starts_at: str
    ends_at: str
    status: str
    service: str | None
    caller_name: str | None
    caller_number: str | None
    reason: str | None
    source: str
    workflow_run_id: int | None


class Slots(BaseModel):
    status: str
    slots: list[str] = []
    date: str | None = None
    timezone: str | None = None
    duration_minutes: int | None = None
    may_book: bool = False
    say: str | None = None


def _policy(row: dict[str, Any]) -> AppointmentPolicy:
    return AppointmentPolicy(**{k: row.get(k) for k in AppointmentPolicy.model_fields})


async def _assert_admin(user: UserModel, organization_id: int) -> None:
    membership = await db_client.get_membership(user.id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    if rank < ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]:
        raise HTTPException(
            status_code=403, detail="Only a workspace admin can change this."
        )


@router.get(
    "/appointments/policy",
    response_model=AppointmentPolicy,
    dependencies=[_appointments],
)
async def appointment_policy(
    user: Annotated[UserModel, Depends(get_user)],
) -> AppointmentPolicy:
    return _policy(await appointments.get_policy(_organization_id(user)))


@router.put(
    "/appointments/policy",
    response_model=AppointmentPolicy,
    dependencies=[_appointments],
)
async def save_appointment_policy(
    body: AppointmentPolicyWrite, user: Annotated[UserModel, Depends(get_user)]
) -> AppointmentPolicy:
    organization_id = _organization_id(user)
    await _assert_admin(user, organization_id)
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    try:
        saved = await appointments.save_policy(
            organization_id, changes, revision=revision, user_id=user.id
        )
    except appointments.PolicyInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except appointments.PolicyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "stored": _policy(exc.stored).model_dump()},
        ) from exc
    if saved.get("call_workflow_id"):
        # The helper gets the booking tool; attaching it to the agent's steps
        # is the agent owner's choice in the editor.
        await appointments.ensure_tool(organization_id=organization_id, user_id=user.id)
    return _policy(saved)


@router.get(
    "/appointments", response_model=list[Appointment], dependencies=[_appointments]
)
async def upcoming_appointments(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[Appointment]:
    return [
        Appointment(**a) for a in await appointments.upcoming(_organization_id(user))
    ]


@router.get("/appointments/slots", response_model=Slots, dependencies=[_appointments])
async def appointment_slots(
    day: Annotated[date, Query(alias="date")],
    user: Annotated[UserModel, Depends(get_user)],
) -> Slots:
    """The times a call would offer on a day, for checking the policy."""
    result = await appointments.open_slots(
        organization_id=_organization_id(user), day=day
    )
    return Slots(**result)

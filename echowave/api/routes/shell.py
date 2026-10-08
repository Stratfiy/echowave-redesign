"""Launch stream `shell`: the door, the landing and Stop.

Thin by design (api/AGENTS.md): each handler checks its flag, resolves the
person and delegates to ``services/shell`` or ``services/workflow``.

- ``/public/early-access/*`` takes no session (screen 01). Rate limited per
  address and per client, because a public form is a form anyone can loop.
- ``/shell/*`` is the signed-in half: where to land after sign-in, the
  onboarding answers (screen 02) and Stop for a forming reply (screen 04).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.rate_limit import rate_limiter
from api.services.shell import early_access, languages, onboarding
from api.services.workflow import reply_stop

public_router = APIRouter(
    prefix="/public/early-access",
    tags=["public-early-access"],
    dependencies=[Depends(features.require(early_access.FLAG))],
)
router = APIRouter(prefix="/shell", tags=["shell"])


# ---------------------------------------------------------------------------
# Screen 01: waitlist and invitation
# ---------------------------------------------------------------------------


class WaitlistJoinRequest(BaseModel):
    email: EmailStr
    language: str = Field(default="en", max_length=16)
    first_task: Optional[str] = Field(default=None, max_length=2000)
    phone: Optional[str] = Field(default=None, max_length=32)
    occupation: Optional[str] = Field(default=None, max_length=120)
    #: True from "Request a new invitation" on an expired or revoked link.
    renewal: bool = False


class WaitlistJoinResponse(BaseModel):
    #: ``waitlisted``, ``already_registered`` or ``invited``.
    state: str
    #: False when this address was already on the list: the second submit
    #: of a double click, said honestly rather than as a new request.
    created: bool


class InviteStatusResponse(BaseModel):
    #: ``valid``, ``expired``, ``revoked``, ``used`` or ``invalid``.
    state: str
    email_hint: Optional[str] = None
    expires_at: Optional[str] = None
    #: The code as printed, only while it can still be used.
    code: Optional[str] = None


class LanguageOption(BaseModel):
    code: str
    native: str
    english: str
    voice: bool


async def _limit(request: Request, bucket: str, identity: str, limit: int) -> None:
    allowed, retry_after = await rate_limiter.check(
        bucket=bucket, identity=identity, limit=limit, window_secs=600
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many tries from here. Wait a few minutes and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else "unknown"
    )


@public_router.get("/languages", response_model=list[LanguageOption])
async def early_access_languages() -> list[dict]:
    """The languages the forms offer, native name first."""
    return languages.as_dicts()


@public_router.post("/waitlist", response_model=WaitlistJoinResponse)
async def join_waitlist(
    body: WaitlistJoinRequest, request: Request
) -> WaitlistJoinResponse:
    """Put an address on the list, once. A repeat returns the same state."""
    await _limit(request, "early_access_ip", _client_ip(request), 30)
    await _limit(request, "early_access_email", body.email.lower(), 10)
    try:
        result = await early_access.join(
            email=body.email,
            language=body.language,
            first_task=body.first_task,
            phone=body.phone,
            occupation=body.occupation,
            renewal=body.renewal,
        )
    except early_access.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return WaitlistJoinResponse(state=result.state, created=result.created)


@public_router.get("/invites/{code}", response_model=InviteStatusResponse)
async def invite_status(code: str, request: Request) -> InviteStatusResponse:
    """What an invitation link says, without using it up."""
    await _limit(request, "early_access_invite", _client_ip(request), 60)
    return InviteStatusResponse(**await early_access.invite_status(code))


# ---------------------------------------------------------------------------
# After sign-in: landing and screen 02
# ---------------------------------------------------------------------------


class LandingResponse(BaseModel):
    #: Where to go, or null to keep the door's own rule (flag off).
    path: Optional[str] = None


class OnboardingResponse(BaseModel):
    enabled: bool
    language: Optional[str] = None
    timezone: Optional[str] = None
    timezone_confirmed: bool = False
    preferred_name: Optional[str] = None
    completed: bool = False
    languages: list[LanguageOption]


class OnboardingSaveRequest(BaseModel):
    language: str = Field(max_length=16)
    timezone: str = Field(max_length=64)
    #: The person pressed Confirm on the detected zone, or chose another.
    timezone_confirmed: bool
    preferred_name: Optional[str] = Field(default=None, max_length=80)
    #: True when they start their first task: onboarding is then done.
    complete: bool = False


def _onboarding_flag(user: UserModel) -> None:
    if not features.is_on(onboarding.FLAG, user.selected_organization_id):
        raise HTTPException(status_code=404, detail="Not Found")


def _onboarding_response(state: onboarding.State) -> OnboardingResponse:
    return OnboardingResponse(
        enabled=True, **state.as_dict(), languages=languages.as_dicts()
    )


@router.get("/landing", response_model=LandingResponse)
async def landing(user: UserModel = Depends(get_user)) -> LandingResponse:
    """Where this person goes after sign-in. Null keeps the old rule, so the
    UI asks this first and falls back to its own logic while the flag is
    off."""
    return LandingResponse(
        path=await onboarding.landing(user.id, user.selected_organization_id)
    )


@router.get("/onboarding", response_model=OnboardingResponse)
async def get_onboarding(user: UserModel = Depends(get_user)) -> OnboardingResponse:
    _onboarding_flag(user)
    return _onboarding_response(await onboarding.get(user.id))


@router.put("/onboarding", response_model=OnboardingResponse)
async def save_onboarding(
    body: OnboardingSaveRequest, user: UserModel = Depends(get_user)
) -> OnboardingResponse:
    """This person's answers. Never written to the workspace."""
    _onboarding_flag(user)
    try:
        state = await onboarding.save(
            user.id,
            language=body.language,
            timezone=body.timezone,
            timezone_confirmed=body.timezone_confirmed,
            preferred_name=body.preferred_name,
            complete=body.complete,
        )
    except onboarding.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The answers are the person's own preferences too: with the settings
    # shell on they reach Settings now (services/settings/profile.py).
    from api.services.settings import profile as settings_profile

    await settings_profile.onboarding_saved(user.id, user.selected_organization_id)
    return _onboarding_response(state)


@router.post("/onboarding/skip", response_model=OnboardingResponse)
async def skip_onboarding(user: UserModel = Depends(get_user)) -> OnboardingResponse:
    _onboarding_flag(user)
    return _onboarding_response(await onboarding.skip(user.id))


# ---------------------------------------------------------------------------
# Screen 04: Stop
# ---------------------------------------------------------------------------


class StopRequest(BaseModel):
    #: Which of Decibyl's conversations; null is the original one.
    thread_id: Optional[str] = Field(default=None, max_length=64)


class StopResponse(BaseModel):
    #: The note was left; the reply stops at its next chunk. False when the
    #: note could not be written, so the screen says Stop did not land.
    requested: bool


@router.post("/chat/stop", response_model=StopResponse)
async def stop_reply(
    body: StopRequest, user: UserModel = Depends(get_user)
) -> StopResponse:
    """Ask Decibyl to stop the reply forming in this thread. What it has
    said so far stays on the thread, marked as stopped."""
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="Select an organization first")
    if not features.is_on("chat_shell", organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    # The same ownership rule as reading the thread: nobody stops a reply
    # in a conversation that is not theirs.
    from api.routes.agent_timeline import _assert_thread_is_theirs

    await _assert_thread_is_theirs(user, organization_id, body.thread_id)
    return StopResponse(
        requested=await reply_stop.request(organization_id, body.thread_id)
    )

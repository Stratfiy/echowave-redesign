"""A person's own controls over HTTP (launch stream `controls`).

Thin: each route resolves the signed-in person (and their workspace where
one applies) and hands over to the service that owns the rule. Every group
is behind its own flag and is a 404 while that flag is off.

* ``/me/quotas`` -- today's allowances (services/quotas.py).
* ``/me/preferences`` -- the person's own preferences
  (services/member_preferences.py). Takes no user id: the row is always the
  caller's.
* ``/me/personal-space`` -- the person's personal space
  (services/personal_space.py).
* ``/feedback`` -- Yes / Not quite on a reply or a task (services/feedback.py).
* ``/events/client`` -- the browser's intent events; only catalogue entries
  owned by the client are accepted, so a client can never record an outcome.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import events, features, feedback, member_preferences, quotas
from api.services import personal_space as personal_space_service
from api.services.auth.depends import get_user
from api.services.events import catalogue

router = APIRouter(tags=["controls"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


# --- quotas -----------------------------------------------------------------


class AllowanceRow(BaseModel):
    kind: str
    unit: str
    used: int
    limit: int
    remaining: int
    resets_at: str


class QuotasResponse(BaseModel):
    allowances: list[AllowanceRow]


@router.get(
    "/me/quotas",
    response_model=QuotasResponse,
    dependencies=[Depends(features.require("operational_quotas"))],
)
async def my_quotas(user: Annotated[UserModel, Depends(get_user)]) -> QuotasResponse:
    """What is left of today's allowances, for the person asking."""
    return QuotasResponse(
        allowances=[AllowanceRow(**row) for row in await quotas.status(user.id)]
    )


# --- preferences ------------------------------------------------------------


class MemberPreferences(BaseModel):
    language: str | None = None
    timezone: str | None = None
    voice: str | None = None
    summary_time: str | None = None
    revision: int
    updated_at: str | None = None
    #: The languages a person can choose from.
    languages: list[str] = Field(default_factory=list)


class MemberPreferencesWrite(BaseModel):
    """Only the fields sent change; null clears one. ``revision`` is the one
    the screen read; an older one is a 409 carrying the stored value."""

    language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    voice: str | None = Field(default=None, max_length=64)
    summary_time: str | None = Field(default=None, max_length=5)
    revision: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


def _prefs(row: dict[str, Any]) -> MemberPreferences:
    return MemberPreferences(
        **{k: row.get(k) for k in ("language", "timezone", "voice", "summary_time")},
        revision=row["revision"],
        updated_at=row.get("updated_at"),
        languages=list(member_preferences.LANGUAGES),
    )


_prefs_flag = Depends(features.require("member_preferences", per_organization=True))


@router.get(
    "/me/preferences", response_model=MemberPreferences, dependencies=[_prefs_flag]
)
async def my_preferences(
    user: Annotated[UserModel, Depends(get_user)],
) -> MemberPreferences:
    return _prefs(await member_preferences.get(user.id))


@router.put(
    "/me/preferences", response_model=MemberPreferences, dependencies=[_prefs_flag]
)
async def save_my_preferences(
    body: MemberPreferencesWrite, user: Annotated[UserModel, Depends(get_user)]
) -> MemberPreferences:
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
                "stored": _prefs(exc.stored).model_dump(),
            },
        ) from exc
    return _prefs(row)


# --- personal space ---------------------------------------------------------


class PersonalSpace(BaseModel):
    organization_id: int
    name: str
    kind: str
    owner_user_id: int
    #: Whether the person is in it right now.
    selected: bool


_space_flag = Depends(features.require("personal_space"))


@router.get(
    "/me/personal-space", response_model=PersonalSpace, dependencies=[_space_flag]
)
async def my_personal_space(
    user: Annotated[UserModel, Depends(get_user)],
) -> PersonalSpace:
    """The person's personal space, made the first time it is asked for.
    Switching into it is the ordinary workspace switch, which checks
    membership like any other."""
    space = await personal_space_service.ensure(user.id)
    return PersonalSpace(
        **personal_space_service.describe(space),
        selected=user.selected_organization_id == space.id,
    )


# --- feedback ---------------------------------------------------------------


class FeedbackWrite(BaseModel):
    subject_kind: Literal["reply", "task", "lesson"]
    subject_id: int
    verdict: Literal["yes", "not_quite"]
    reasons: list[str] = Field(default_factory=list, max_length=5)

    model_config = ConfigDict(extra="forbid")


class FeedbackSaved(BaseModel):
    id: int
    subject_kind: str
    subject_id: int
    verdict: str
    reasons: list[str]
    output_version: str


class MyFeedback(BaseModel):
    #: subject id -> what this person said about it.
    answers: dict[int, dict[str, Any]]
    reasons: list[str]


_feedback_flag = Depends(features.require("reply_feedback", per_organization=True))


@router.post("/feedback", response_model=FeedbackSaved, dependencies=[_feedback_flag])
async def give_feedback(
    body: FeedbackWrite, user: Annotated[UserModel, Depends(get_user)]
) -> FeedbackSaved:
    organization_id = _organization_id(user)
    try:
        saved = await feedback.submit(
            organization_id=organization_id,
            user_id=user.id,
            subject_kind=body.subject_kind,
            subject_id=body.subject_id,
            verdict=body.verdict,
            reasons=body.reasons,
        )
    except feedback.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except feedback.FeedbackRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FeedbackSaved(**saved)


@router.get("/feedback/mine", response_model=MyFeedback, dependencies=[_feedback_flag])
async def my_feedback(
    user: Annotated[UserModel, Depends(get_user)],
    subject_kind: Literal["reply", "task", "lesson"] = "reply",
    ids: Annotated[list[int], Query(max_length=200)] = [],  # noqa: B006
) -> MyFeedback:
    organization_id = _organization_id(user)
    answers = await feedback.mine(
        organization_id=organization_id,
        user_id=user.id,
        subject_kind=subject_kind,
        subject_ids=list(ids),
    )
    return MyFeedback(answers=answers, reasons=list(feedback.REASONS))


# --- client events ----------------------------------------------------------


class ClientEvent(BaseModel):
    name: str = Field(max_length=64)
    properties: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ClientEventAccepted(BaseModel):
    #: Null when the catalogue is switched off: nothing was recorded.
    event_id: str | None


@router.post(
    "/events/client",
    response_model=ClientEventAccepted,
    dependencies=[Depends(features.require("event_catalogue"))],
)
async def client_event(
    body: ClientEvent, user: Annotated[UserModel, Depends(get_user)]
) -> ClientEventAccepted:
    """Record one interaction event from the browser. Server-owned names --
    anything that states an outcome -- are refused: those come from the
    change itself, never from a client's say-so."""
    spec = catalogue.CATALOGUE.get(body.name)
    if spec is None or spec.owner != catalogue.CLIENT:
        raise HTTPException(status_code=422, detail="Not a client event.")
    try:
        events.envelope.clean_properties(body.name, body.properties)
    except events.envelope.EventRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event_id = await events.emit(
        body.name,
        user_id=user.id,
        organization_id=user.selected_organization_id,
        properties=body.properties,
    )
    return ClientEventAccepted(event_id=event_id)

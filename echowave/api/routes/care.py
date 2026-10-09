"""Care for older people and their families over HTTP (launch stream `care`).

Thin: each route resolves the signed-in person and their workspace and hands
over to services/care. Every group sits behind its own flag and is a 404
while that flag is off for the person's workspace:

* ``/care/status`` -- which care parts are on here, and which need setup.
* ``/care/circle`` and ``/care/family`` -- the family circle
  (``care_family_circle``): the older person's side, and the family's.
* ``/care/medicines`` -- reminder calls (``care_medicine_calls``).
* ``/care/scam-check`` -- is this a scam? (``care_scam_check``).
* ``/care/help`` -- step-by-step tech help (``care_tech_help``).

Everything that rings, shares or sends answers with the consent card it
proposed (``card``), which the screen renders in place and the person
answers there: nothing is done until they confirm it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.routes.agent_timeline import TimelineEvent, _as_event
from api.services import care, features
from api.services.auth.depends import get_user
from api.services.care import (
    CareError,
    NeedsSetup,
    NotFound,
    calls,
    circle,
    medicines,
    scam,
    tech_help,
)

router = APIRouter(prefix="/care", tags=["care"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _refusal(exc: CareError) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, NeedsSetup):
        return HTTPException(
            status_code=409, detail={"state": "needs_setup", "message": str(exc)}
        )
    if isinstance(exc, tech_help.StaleAnswer):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def _card(organization_id: int, event_id: int | None) -> TimelineEvent | None:
    if not event_id:
        return None
    row = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return _as_event(row) if row is not None else None


# --- status -----------------------------------------------------------------


class CarePart(BaseModel):
    key: str
    #: available | needs_setup | test_mode | disabled
    state: str
    reason: str | None = None


class ShareOption(BaseModel):
    key: str
    label: str


class CareStatus(BaseModel):
    parts: list[CarePart]
    #: What a family member may be shown, for the consent screens.
    shares: list[ShareOption]


def _any_care(user: Annotated[UserModel, Depends(get_user)]) -> None:
    organization_id = user.selected_organization_id
    if not any(features.is_on(flag, organization_id) for flag in care.FLAGS):
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/status", response_model=CareStatus, dependencies=[Depends(_any_care)])
async def care_status(user: Annotated[UserModel, Depends(get_user)]) -> CareStatus:
    organization_id = user.selected_organization_id
    parts = []
    for flag in care.FLAGS:
        if not features.is_on(flag, organization_id):
            parts.append(CarePart(key=flag, state="disabled"))
            continue
        if flag == care.MEDICINE_CALLS and organization_id:
            ready = await calls.readiness(organization_id)
            if ready["state"] == "needs_setup":
                # Reminders in Decibyl need no number, so the part works
                # here; it is phone calls that wait for a line, said so.
                parts.append(
                    CarePart(
                        key=flag,
                        state="available",
                        reason=f"Phone calls need setting up: {ready['reason']} "
                        "Reminders in Decibyl work now.",
                    )
                )
                continue
            state = "available" if ready["state"] == "ready" else ready["state"]
            parts.append(CarePart(key=flag, state=state, reason=ready["reason"]))
            continue
        if flag == care.SIMPLE_MODE and not features.is_on(
            "member_preferences", organization_id
        ):
            parts.append(
                CarePart(
                    key=flag,
                    state="needs_setup",
                    reason="Simple mode is saved with personal preferences, which are not switched on.",
                )
            )
            continue
        parts.append(CarePart(key=flag, state="available"))
    return CareStatus(
        parts=parts,
        shares=[{"key": k, "label": v} for k, v in circle.SHARES.items()],
    )


@router.get(
    "/cards/{event_id}", response_model=TimelineEvent, dependencies=[Depends(_any_care)]
)
async def care_card(
    event_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> TimelineEvent:
    """One care consent card as it stands now, for the screen that showed
    it. Only care cards, only in this workspace, only the person's own."""
    from api.enums import AgentEventKind
    from api.services.workflow import actions

    organization_id = _organization_id(user)
    row = await db_client.get_agent_event(event_id, organization_id=organization_id)
    payload = (row.payload or {}) if row is not None else {}
    if (
        row is None
        or row.kind != AgentEventKind.ACTION_PROPOSED.value
        or payload.get("action") not in actions.CARE_ACTIONS
        or payload.get("only_user_id") != user.id
    ):
        raise HTTPException(status_code=404, detail="That card is not here.")
    return _as_event(row)


# --- family circle: the older person's side ---------------------------------

_circle_flag = Depends(features.require(care.FAMILY_CIRCLE, per_organization=True))


class CircleMember(BaseModel):
    id: int
    name: str
    email: str
    shares: list[str]
    pending_shares: list[str]
    status: str
    consent_event_id: int | None = None
    invite_expires_at: str | None = None
    accepted_at: str | None = None


class Circle(BaseModel):
    id: int
    display_name: str | None
    members: list[CircleMember]
    shares: list[ShareOption]


class CircleName(BaseModel):
    display_name: str = Field(max_length=60)

    model_config = ConfigDict(extra="forbid")


class MemberWrite(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3, max_length=254)
    shares: list[str] = Field(min_length=1, max_length=len(circle.SHARES))

    model_config = ConfigDict(extra="forbid")


class SharesWrite(BaseModel):
    shares: list[str] = Field(max_length=len(circle.SHARES))

    model_config = ConfigDict(extra="forbid")


class MemberProposed(BaseModel):
    member: CircleMember
    #: The consent card, to answer in place. Null when nothing needed asking
    #: (sharing less takes effect at once).
    card: TimelineEvent | None = None


@router.get("/circle", response_model=Circle, dependencies=[_circle_flag])
async def my_circle(user: Annotated[UserModel, Depends(get_user)]) -> Circle:
    return Circle(**await circle.my_circle(_organization_id(user), user.id))


@router.put("/circle", response_model=Circle, dependencies=[_circle_flag])
async def name_my_circle(
    body: CircleName, user: Annotated[UserModel, Depends(get_user)]
) -> Circle:
    organization_id = _organization_id(user)
    await circle.set_display_name(organization_id, user.id, body.display_name)
    return Circle(**await circle.my_circle(organization_id, user.id))


@router.post(
    "/circle/members", response_model=MemberProposed, dependencies=[_circle_flag]
)
async def add_member(
    body: MemberWrite, user: Annotated[UserModel, Depends(get_user)]
) -> MemberProposed:
    organization_id = _organization_id(user)
    try:
        made = await circle.propose_member(
            organization_id,
            user.id,
            name=body.name,
            email=body.email,
            shares=body.shares,
        )
    except CareError as exc:
        raise _refusal(exc) from exc
    return MemberProposed(
        member=CircleMember(**made["member"]),
        card=await _card(organization_id, made["event_id"]),
    )


@router.put(
    "/circle/members/{member_id}/shares",
    response_model=MemberProposed,
    dependencies=[_circle_flag],
)
async def change_member_shares(
    member_id: int, body: SharesWrite, user: Annotated[UserModel, Depends(get_user)]
) -> MemberProposed:
    organization_id = _organization_id(user)
    try:
        made = await circle.change_shares(
            organization_id, user.id, member_id, body.shares
        )
    except CareError as exc:
        raise _refusal(exc) from exc
    return MemberProposed(
        member=CircleMember(**made["member"]),
        card=await _card(organization_id, made["event_id"]),
    )


@router.delete(
    "/circle/members/{member_id}",
    response_model=CircleMember,
    dependencies=[_circle_flag],
)
async def remove_member(
    member_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> CircleMember:
    """Stop sharing with someone. Immediate: stopping must never wait."""
    try:
        return CircleMember(
            **await circle.revoke(_organization_id(user), user.id, member_id)
        )
    except CareError as exc:
        raise _refusal(exc) from exc


# --- family circle: the family's side ----------------------------------------


class AcceptInvite(BaseModel):
    code: str = Field(min_length=6, max_length=16)

    model_config = ConfigDict(extra="forbid")


class Accepted(BaseModel):
    person: str
    shares: list[str]


class FamilyAlert(BaseModel):
    id: int
    kind: str
    title: str
    at: str
    read: bool


class FamilyDose(BaseModel):
    due_at: str
    state: str


class FamilyMedicine(BaseModel):
    id: int
    label: str
    times: list[str]
    #: The times above are in this zone.
    timezone: str
    state: str
    doses: list[FamilyDose]


class FamilyScamCheck(BaseModel):
    kind: str
    verdict: str
    signals: list[str]
    at: str


class CaredFor(BaseModel):
    member_id: int
    person: str
    shares: list[str]
    alerts: list[FamilyAlert]
    #: Null when the person has not shared their medicines with you.
    medicines: list[FamilyMedicine] | None = None
    #: Null when the person has not shared their scam checks with you.
    scam_checks: list[FamilyScamCheck] | None = None


class FamilyView(BaseModel):
    people: list[CaredFor]


@router.post("/family/accept", response_model=Accepted, dependencies=[_circle_flag])
async def accept_invite(
    body: AcceptInvite, user: Annotated[UserModel, Depends(get_user)]
) -> Accepted:
    try:
        return Accepted(**await circle.accept(user.id, body.code))
    except CareError as exc:
        raise _refusal(exc) from exc


@router.get("/family", response_model=FamilyView, dependencies=[_circle_flag])
async def family(user: Annotated[UserModel, Depends(get_user)]) -> FamilyView:
    """The people this person is in the family circle of, and only what each
    one shared with them."""
    return FamilyView(people=[CaredFor(**p) for p in await circle.family_view(user.id)])


@router.post(
    "/family/alerts/{alert_id}/read", status_code=204, dependencies=[_circle_flag]
)
async def read_alert(
    alert_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    try:
        await circle.mark_alert_read(user.id, alert_id)
    except CareError as exc:
        raise _refusal(exc) from exc


# --- medicine reminder calls -------------------------------------------------

_medicine_flag = Depends(features.require(care.MEDICINE_CALLS, per_organization=True))


class Dose(BaseModel):
    id: int
    due_at: str
    #: calling | reminded | taken | not_taken | not_answered | unclear | failed
    #: | cancelled | unknown (may have rung; not confirmed either way)
    state: str
    reason: str | None = None
    alerted: bool


class Medicine(BaseModel):
    id: int
    label: str
    times: list[str]
    timezone: str
    language: str
    language_name: str
    #: call (rings a phone) | app (shown in Decibyl and sent as a notification)
    channel: str
    #: Null for a reminder in Decibyl, which has no phone.
    phone_masked: str | None = None
    alert_member_ids: list[int]
    #: awaiting_approval | active | paused | declined
    state: str
    card_event_id: int | None = None
    doses: list[Dose] = Field(default_factory=list)


class MedicineList(BaseModel):
    medicines: list[Medicine]
    #: Phone calls here: ready | test_mode | needs_setup, and why.
    calls: dict[str, str]
    #: Reminders in Decibyl: always ready (no number needed), and how they
    #: reach the person.
    app: dict[str, str]
    languages: dict[str, str]


class MedicineWrite(BaseModel):
    #: The medicine as the person calls it. Reminders only: never a dose
    #: Decibyl chose.
    label: str = Field(min_length=1, max_length=80)
    times: list[str] = Field(min_length=1, max_length=medicines.MAX_TIMES)
    #: Needed for a phone call; not for a reminder in Decibyl.
    phone: str | None = Field(default=None, min_length=6, max_length=24)
    #: call | app. Omitted: a call when a phone is given, else app.
    channel: Literal["call", "app"] | None = None
    language: str | None = Field(default=None, max_length=16)
    alert_member_ids: list[int] = Field(default_factory=list, max_length=8)

    model_config = ConfigDict(extra="forbid")


class MedicineEdit(BaseModel):
    #: Only what is sent changes. The phone is not here: a different number
    #: is a new reminder, with its own card.
    label: str | None = Field(default=None, min_length=1, max_length=80)
    times: list[str] | None = Field(
        default=None, min_length=1, max_length=medicines.MAX_TIMES
    )
    language: str | None = Field(default=None, max_length=16)

    model_config = ConfigDict(extra="forbid")


class MedicineProposed(BaseModel):
    medicine: Medicine
    card: TimelineEvent | None = None


class MarkTaken(BaseModel):
    due_at: str

    model_config = ConfigDict(extra="forbid")


@router.get("/medicines", response_model=MedicineList, dependencies=[_medicine_flag])
async def my_medicines(user: Annotated[UserModel, Depends(get_user)]) -> MedicineList:
    organization_id = _organization_id(user)
    return MedicineList(
        medicines=[
            Medicine(**m) for m in await medicines.list_mine(organization_id, user.id)
        ],
        calls=await calls.readiness(organization_id),
        app=calls.app_readiness(organization_id),
        languages=dict(medicines.LANGUAGE_NAMES),
    )


@router.post(
    "/medicines", response_model=MedicineProposed, dependencies=[_medicine_flag]
)
async def add_medicine(
    body: MedicineWrite, user: Annotated[UserModel, Depends(get_user)]
) -> MedicineProposed:
    organization_id = _organization_id(user)
    try:
        made = await medicines.propose(
            organization_id,
            user.id,
            label=body.label,
            times=body.times,
            phone=body.phone,
            channel=body.channel,
            language=body.language,
            alert_member_ids=body.alert_member_ids,
        )
    except CareError as exc:
        raise _refusal(exc) from exc
    return MedicineProposed(
        medicine=Medicine(**made["medicine"]),
        card=await _card(organization_id, made["event_id"]),
    )


@router.post(
    "/medicines/{medicine_id}/pause",
    response_model=Medicine,
    dependencies=[_medicine_flag],
)
async def pause_medicine(
    medicine_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> Medicine:
    """Stop the calls, now. Starting them again is a new card."""
    try:
        return Medicine(
            **await medicines.pause(_organization_id(user), user.id, medicine_id)
        )
    except CareError as exc:
        raise _refusal(exc) from exc


@router.post(
    "/medicines/{medicine_id}/resume",
    response_model=MedicineProposed,
    dependencies=[_medicine_flag],
)
async def resume_medicine(
    medicine_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> MedicineProposed:
    organization_id = _organization_id(user)
    try:
        made = await medicines.resume(organization_id, user.id, medicine_id)
    except CareError as exc:
        raise _refusal(exc) from exc
    return MedicineProposed(
        medicine=Medicine(**made["medicine"]),
        card=await _card(organization_id, made["event_id"]),
    )


@router.patch(
    "/medicines/{medicine_id}",
    response_model=MedicineProposed,
    dependencies=[_medicine_flag],
)
async def edit_medicine(
    medicine_id: int,
    body: MedicineEdit,
    user: Annotated[UserModel, Depends(get_user)],
) -> MedicineProposed:
    """Change the name, times or language. A running reminder stops until the
    card with the new details is confirmed."""
    organization_id = _organization_id(user)
    try:
        made = await medicines.edit(
            organization_id,
            user.id,
            medicine_id,
            label=body.label,
            times=body.times,
            language=body.language,
        )
    except CareError as exc:
        raise _refusal(exc) from exc
    return MedicineProposed(
        medicine=Medicine(**made["medicine"]),
        card=await _card(organization_id, made["event_id"]),
    )


@router.delete(
    "/medicines/{medicine_id}", status_code=204, dependencies=[_medicine_flag]
)
async def remove_medicine(
    medicine_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    """Take a reminder off the list; it stops at once."""
    try:
        await medicines.remove(_organization_id(user), user.id, medicine_id)
    except CareError as exc:
        raise _refusal(exc) from exc


@router.post(
    "/medicines/{medicine_id}/taken", response_model=Dose, dependencies=[_medicine_flag]
)
async def mark_taken(
    medicine_id: int, body: MarkTaken, user: Annotated[UserModel, Depends(get_user)]
) -> Dose:
    from datetime import datetime

    try:
        due = datetime.fromisoformat(body.due_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That is not a time.") from exc
    try:
        return Dose(
            **await medicines.mark_taken(
                _organization_id(user), user.id, medicine_id, due_at=due
            )
        )
    except CareError as exc:
        raise _refusal(exc) from exc


# --- scam check ---------------------------------------------------------------

_scam_flag = Depends(features.require(care.SCAM_CHECK, per_organization=True))


class ScamCheckWrite(BaseModel):
    #: The message, or what the caller said. Read once; not stored.
    text: str = Field(min_length=1, max_length=scam.MAX_CHARS)
    kind: Literal["message", "call"] = "message"

    model_config = ConfigDict(extra="forbid")


class ScamReason(BaseModel):
    code: str
    why: str


class ScamAnswer(BaseModel):
    id: int
    kind: str
    #: likely_scam | be_careful | no_signs_found
    verdict: str
    headline: str
    reasons: list[ScamReason]
    what_to_do: list[str]
    never_asks: str
    limits: str


class ScamHistoryItem(BaseModel):
    id: int
    kind: str
    verdict: str
    headline: str
    signals: list[str]
    at: str


class ScamHistory(BaseModel):
    checks: list[ScamHistoryItem]


@router.post("/scam-check", response_model=ScamAnswer, dependencies=[_scam_flag])
async def check_for_scam(
    body: ScamCheckWrite, user: Annotated[UserModel, Depends(get_user)]
) -> ScamAnswer:
    try:
        return ScamAnswer(
            **await scam.check(
                _organization_id(user), user.id, text=body.text, kind=body.kind
            )
        )
    except CareError as exc:
        raise _refusal(exc) from exc


@router.get("/scam-check/recent", response_model=ScamHistory, dependencies=[_scam_flag])
async def recent_scam_checks(
    user: Annotated[UserModel, Depends(get_user)],
) -> ScamHistory:
    return ScamHistory(
        checks=[
            ScamHistoryItem(**c)
            for c in await scam.recent(_organization_id(user), user.id)
        ]
    )


# --- tech help ---------------------------------------------------------------

_help_flag = Depends(features.require(care.TECH_HELP, per_organization=True))


class HelpTopic(BaseModel):
    slug: str
    title: str


class HelpTopics(BaseModel):
    topics: list[HelpTopic]


class HelpStart(BaseModel):
    guide: str | None = Field(default=None, max_length=64)
    question: str = Field(default="", max_length=500)

    model_config = ConfigDict(extra="forbid")


class HelpSession(BaseModel):
    id: int
    guide: str
    title: str
    #: active | done | stuck
    state: str
    step_number: int
    steps_total: int
    say: str
    is_alternative: bool
    version: int
    note: str | None = None
    family_told: list[str] = Field(default_factory=list)


class HelpStarted(BaseModel):
    matched: bool
    session: HelpSession | None = None
    note: str | None = None
    topics: list[HelpTopic] = Field(default_factory=list)


class HelpAnswer(BaseModel):
    worked: bool
    version: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


@router.get("/help/topics", response_model=HelpTopics, dependencies=[_help_flag])
async def help_topics() -> HelpTopics:
    return HelpTopics(topics=[HelpTopic(**t) for t in tech_help.topics()])


@router.post("/help/sessions", response_model=HelpStarted, dependencies=[_help_flag])
async def start_help(
    body: HelpStart, user: Annotated[UserModel, Depends(get_user)]
) -> HelpStarted:
    started: dict[str, Any] = await tech_help.start(
        _organization_id(user), user.id, guide=body.guide, question=body.question
    )
    return HelpStarted(**started)


@router.get(
    "/help/sessions/{session_id}", response_model=HelpSession, dependencies=[_help_flag]
)
async def get_help(
    session_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> HelpSession:
    try:
        return HelpSession(
            **await tech_help.get(_organization_id(user), user.id, session_id)
        )
    except CareError as exc:
        raise _refusal(exc) from exc


@router.post(
    "/help/sessions/{session_id}/answer",
    response_model=HelpSession,
    dependencies=[_help_flag],
)
async def answer_help(
    session_id: int, body: HelpAnswer, user: Annotated[UserModel, Depends(get_user)]
) -> HelpSession:
    try:
        return HelpSession(
            **await tech_help.answer(
                _organization_id(user),
                user.id,
                session_id,
                worked=body.worked,
                version=body.version,
            )
        )
    except CareError as exc:
        raise _refusal(exc) from exc

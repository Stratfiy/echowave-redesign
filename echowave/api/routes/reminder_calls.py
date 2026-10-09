"""Reminder calls over HTTP (services/reminder_calls).

Thin: each route resolves the signed-in person and their workspace and
hands over to ``services/reminder_calls/schedule``. The whole group is a 404
while ``reminder_calls`` is off for the person's workspace. Setting a
reminder call is a card on the thread (Decibyl's ``remind_me_by_call``), so
there is no route that schedules one unseen.

* ``GET /reminder-calls`` -- the person's reminder calls here, each with its
  recent occurrences: the task state and every ring's delivery state.
* ``POST /reminder-calls/{id}/cancel`` -- stop a reminder; nothing more rings.
* ``POST /reminder-calls/occurrences/{id}/done`` -- "I've done it", in the
  app. Moves the task only; a retry still waiting is not rung.
* ``POST /reminder-calls/ask`` -- "Call me" chosen in Today's reminder
  editor: the same draft and the same card (number card first, if needed)
  as Decibyl's tool, returned to be answered in the editor. Still a card:
  nothing is scheduled until the person confirms it.
* ``GET /reminder-calls/cards/{event_id}`` -- one of the person's own
  reminder-call cards as it stands now, for the screen that showed it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.routes.agent_timeline import TimelineEvent, _as_event
from api.services import features
from api.services.auth.depends import get_user
from api.services.reminder_calls import NotHere, ReminderCallError, schedule, tools

router = APIRouter(
    prefix="/reminder-calls",
    tags=["reminder-calls"],
    dependencies=[Depends(features.require("reminder_calls", per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class ReminderCallRing(BaseModel):
    attempt: int
    #: queued | dispatching | accepted | answered | no_answer | failed |
    #: unknown | skipped
    state: str
    reason: str | None = None


class ReminderCallOccurrence(BaseModel):
    id: int
    due_at: str
    #: open | snoozed | user_reported_done | cancelled
    task_state: str
    calls: list[ReminderCallRing]


class ReminderCall(BaseModel):
    id: int
    title: str
    language: str
    #: Masked: "+91 98••••3210".
    number: str
    timezone: str
    local_time: str
    recurrence: str
    weekday: int | None = None
    #: active | cancelled
    state: str
    next_due_at: str | None = None
    #: The next ring in the person's words: "Tue 13 Oct 2026, 09:00 IST
    #: (Asia/Kolkata)".
    next_due_label: str | None = None
    recent: list[ReminderCallOccurrence]


class ReminderCallList(BaseModel):
    reminders: list[ReminderCall]


class ReminderCallChanged(BaseModel):
    changed: bool


@router.get("", response_model=ReminderCallList)
async def list_reminder_calls(
    user: Annotated[UserModel, Depends(get_user)],
) -> ReminderCallList:
    organization_id = _organization_id(user)
    rows = await schedule.list_for(organization_id, user.id)
    return ReminderCallList(reminders=[ReminderCall(**r) for r in rows])


@router.post("/{schedule_id}/cancel", response_model=ReminderCallChanged)
async def cancel_reminder_call(
    schedule_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> ReminderCallChanged:
    organization_id = _organization_id(user)
    if not await schedule.cancel(organization_id, user.id, schedule_id):
        raise HTTPException(status_code=404, detail="That reminder is not here.")
    return ReminderCallChanged(changed=True)


@router.post("/occurrences/{occurrence_id}/done", response_model=ReminderCallChanged)
async def mark_reminder_done(
    occurrence_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> ReminderCallChanged:
    organization_id = _organization_id(user)
    try:
        changed = await schedule.mark_done(organization_id, user.id, occurrence_id)
    except NotHere as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ReminderCallChanged(changed=changed)


class ReminderCallAsk(BaseModel):
    title: str = Field(max_length=200)
    #: today, tomorrow, a weekday or YYYY-MM-DD (for a one-off).
    date: str | None = Field(default=None, max_length=32)
    #: HH:MM, the person's local time.
    time: str = Field(max_length=16)
    #: once | daily | weekdays | weekly
    recurrence: str = Field(default="once", max_length=16)
    #: 0 (Monday) to 6, for weekly.
    weekday: int | None = Field(default=None, ge=0, le=6)
    timezone: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=8)
    #: Only when the person typed a number to ring.
    phone_number: str | None = Field(default=None, max_length=32)


class ReminderCallAsked(BaseModel):
    #: proposed | already_proposed (a card to answer) | no_line |
    #: needs_number (no card: ``message`` says why)
    status: str
    message: str | None = None
    card: TimelineEvent | None = None


async def _own_card(
    organization_id: int, user_id: int, event_id: int | None
) -> TimelineEvent | None:
    from api.enums import AgentEventKind
    from api.services.workflow import actions

    if not event_id:
        return None
    row = await db_client.get_agent_event(event_id, organization_id=organization_id)
    payload = (row.payload or {}) if row is not None else {}
    if (
        row is None
        or row.kind != AgentEventKind.ACTION_PROPOSED.value
        or payload.get("action") not in actions.REMINDER_CALL_ACTIONS
        or payload.get("only_user_id") != user_id
    ):
        return None
    return _as_event(row)


@router.post("/ask", response_model=ReminderCallAsked)
async def ask_for_reminder_call(
    body: ReminderCallAsk, user: Annotated[UserModel, Depends(get_user)]
) -> ReminderCallAsked:
    organization_id = _organization_id(user)
    try:
        told = await tools.ask_from_today(organization_id, user.id, body.model_dump())
    except ReminderCallError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReminderCallAsked(
        status=told["status"],
        message=told["message"],
        card=await _own_card(organization_id, user.id, told["event_id"]),
    )


@router.get("/cards/{event_id}", response_model=TimelineEvent)
async def reminder_call_card(
    event_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> TimelineEvent:
    """Only reminder-call cards, only in this workspace, only the person's
    own."""
    organization_id = _organization_id(user)
    card = await _own_card(organization_id, user.id, event_id)
    if card is None:
        raise HTTPException(status_code=404, detail="That card is not here.")
    return card

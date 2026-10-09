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
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.reminder_calls import NotHere, schedule

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

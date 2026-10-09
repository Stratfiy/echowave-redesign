""" "Call me when it's done" over HTTP (services/call_when_done).

Thin: each route resolves the signed-in person and their workspace and
hands over to ``services/call_when_done/optin``. The whole group is a 404
while ``call_when_done`` is off for the person's workspace.

* ``GET /call-when-done/status`` -- what the thread shows: pending asks on
  this conversation, the number on file (masked), whether calls can be
  placed here.
* ``POST /call-when-done`` -- ask to be called when "it" is done; with a
  ``phone``, the number card is put on the thread for the person to confirm.
* ``DELETE /call-when-done/{id}`` -- stop waiting.

The thread answers every ask itself; these responses carry the same line
so a screen can show it at once.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.call_when_done import CallWhenDoneError, NotHere, optin

router = APIRouter(
    prefix="/call-when-done",
    tags=["call-when-done"],
    dependencies=[Depends(features.require("call_when_done", per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class PendingCallback(BaseModel):
    id: int
    subject: str
    title: str | None = None


class CallWhenDoneStatus(BaseModel):
    pending: list[PendingCallback]
    #: The confirmed number, masked; null until one is confirmed.
    number: str | None = None
    #: False where the workspace cannot place calls (the person is told in
    #: the app instead).
    can_call: bool
    reason: str | None = None
    #: Whether to offer "Call me when done" (work running, nobody asked).
    offer: bool
    in_flight: str | None = None


class CallWhenDoneRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=64)
    task_id: int | None = None
    workflow_id: int | None = None
    phone: str | None = Field(default=None, max_length=32)

    model_config = ConfigDict(extra="forbid")


class CallWhenDoneResponse(BaseModel):
    callback_id: int
    subject: str
    state: str
    #: What Decibyl said on the thread.
    line: str
    can_call: bool
    reason: str | None = None
    number: str | None = None
    card_event_id: int | None = None


@router.get("/status", response_model=CallWhenDoneStatus)
async def call_when_done_status(
    user: Annotated[UserModel, Depends(get_user)],
    thread_id: Annotated[str | None, Query(max_length=64)] = None,
) -> CallWhenDoneStatus:
    organization_id = _organization_id(user)
    return CallWhenDoneStatus(**await optin.status(organization_id, user.id, thread_id))


@router.post("", response_model=CallWhenDoneResponse)
async def ask_to_be_called(
    body: CallWhenDoneRequest, user: Annotated[UserModel, Depends(get_user)]
) -> CallWhenDoneResponse:
    organization_id = _organization_id(user)
    try:
        made = await optin.opt_in(
            organization_id,
            user.id,
            thread_id=body.thread_id,
            task_id=body.task_id,
            workflow_id=body.workflow_id,
            phone=body.phone,
        )
    except NotHere as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CallWhenDoneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CallWhenDoneResponse(
        **{k: made[k] for k in CallWhenDoneResponse.model_fields if k in made}
    )


@router.delete("/{callback_id}", status_code=204)
async def stop_waiting(
    callback_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    organization_id = _organization_id(user)
    if not await optin.cancel(organization_id, user.id, callback_id):
        raise HTTPException(status_code=404, detail="That is not here.")

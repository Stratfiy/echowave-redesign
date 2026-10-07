"""The desktop app's half of "work on my computer".

Thin, like every router here: resolve the caller, delegate to
``services/workflow/desktop_steps``, shape the reply. Behind the
``desktop_computer_use`` flag for the caller's organisation, and a 404 while
it is off -- which is also how the desktop app learns it may not start.

Nothing here receives a screenshot. The desktop sends the text of one held
step (what it would do, in which app, with the exact detail), polls the
card, claims it once when it is released, and reports what happened.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.workflow import actions, desktop_steps

router = APIRouter(
    prefix="/desktop",
    tags=["desktop"],
    dependencies=[
        Depends(features.require("desktop_computer_use", per_organization=True))
    ],
)


def _organization(user: UserModel) -> int:
    if user.selected_organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


@router.get("/status")
async def desktop_status(
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, Any]:
    """Reached at all means the switch is on for this organisation."""
    _organization(user)
    return {"computer_use": True}


class StepRequest(BaseModel):
    kind: str = Field(max_length=16)
    app: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=400)
    detail: str = Field(min_length=1, max_length=1000)
    fingerprint: str = Field(min_length=64, max_length=64)
    step: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(default="", max_length=64)
    request_id: str = Field(default="", max_length=64)
    device: str = Field(default="", max_length=120)
    #: The Decibyl thread the person started the task from, if any.
    thread_id: str | None = Field(default=None, max_length=64)


@router.post("/steps")
async def propose_step(
    body: StepRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    organization_id = _organization(user)
    arguments = body.model_dump(exclude={"thread_id"})
    # Only the action's name travels with the card; the step's coordinates
    # and text stay in the fingerprint the desktop will have to match.
    arguments["step"] = {"name": str(body.step.get("name") or "")}
    try:
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=user.id,
            thread_id=body.thread_id,
            arguments=arguments,
        )
    except desktop_steps.DesktopStepError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"event_id": event_id, "state": actions.PROPOSED}


@router.get("/steps/{event_id}")
async def get_step(
    event_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await desktop_steps.state(
            organization_id=_organization(user), user_id=user.id, event_id=event_id
        )
    except desktop_steps.DesktopStepError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ClaimRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)


@router.post("/steps/{event_id}/claim")
async def claim_step(
    event_id: int, body: ClaimRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        claimed = await desktop_steps.claim(
            organization_id=_organization(user),
            user_id=user.id,
            event_id=event_id,
            fingerprint=body.fingerprint,
        )
    except desktop_steps.DesktopStepError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not claimed:
        raise HTTPException(status_code=409, detail="Not released, or already taken.")
    return {"claimed": True}


class OutcomeRequest(BaseModel):
    #: true done, false failed, null "the computer does not know" (unknown).
    ok: bool | None
    note: str = Field(default="", max_length=600)


@router.post("/steps/{event_id}/outcome")
async def report_step(
    event_id: int, body: OutcomeRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        return await desktop_steps.report(
            organization_id=_organization(user),
            user_id=user.id,
            event_id=event_id,
            ok=body.ok,
            note=body.note,
        )
    except desktop_steps.DesktopStepError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/steps/{event_id}/cancel")
async def cancel_step(
    event_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        state = await desktop_steps.cancel(
            organization_id=_organization(user), user_id=user.id, event_id=event_id
        )
    except (desktop_steps.DesktopStepError, actions.ActionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"state": state}


class ReceiptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64)


@router.post("/receipts")
async def post_receipt(
    body: ReceiptRequest, user: Annotated[UserModel, Depends(get_user)]
) -> dict[str, Any]:
    try:
        await desktop_steps.post_receipt(
            organization_id=_organization(user),
            thread_id=body.thread_id,
            text=body.text,
        )
    except desktop_steps.DesktopStepError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"posted": True}

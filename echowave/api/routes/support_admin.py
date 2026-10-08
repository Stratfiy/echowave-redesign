"""The staff side of launch stream `support`: the inbox and case (screen 32)
and typed support actions (screen 33).

Any staff tier reads and works cases (``get_staff``); every case open,
reply, note and change writes ``admin_action_log`` (services/support).
Approving an action needs a second person at the command's approver tier,
enforced in ``services/support/actions``. ``support_inbox`` gates the case
routes and ``support_actions`` the action routes; each is a 404 while off.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.db import db_client
from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_staff
from api.services.support import actions, attachments, commands, tickets

router = APIRouter(
    prefix="/admin/support",
    tags=["admin-support"],
    dependencies=[Depends(features.require("support_inbox")), Depends(get_staff)],
)

actions_router = APIRouter(
    prefix="/admin/support/actions",
    tags=["admin-support"],
    dependencies=[Depends(features.require("support_actions")), Depends(get_staff)],
)

Staff = Annotated[UserModel, Depends(get_staff)]


# --- the inbox and case (screen 32) -----------------------------------------


@router.get("/tickets")
async def support_queue(
    staff: Staff,
    status: str | None = "active",
    assignee: str | None = None,
    severity: str | None = None,
    overdue: bool = False,
    requester_user_id: int | None = None,
) -> dict[str, Any]:
    try:
        rows = await tickets.queue(
            staff_id=staff.id,
            status=status or None,
            assignee=assignee,
            severity=severity,
            overdue_only=overdue,
            requester_user_id=requester_user_id,
        )
    except (tickets.TicketError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"tickets": rows}


@router.get("/tickets/{ticket_id}")
async def support_case(ticket_id: int, staff: Staff) -> dict[str, Any]:
    try:
        return await tickets.case(ticket_id=ticket_id, staff_id=staff.id)
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class StaffReply(BaseModel):
    body: str = Field(min_length=1, max_length=tickets.BODY_LIMIT)
    client_key: str | None = Field(default=None, max_length=64)
    #: Where the case goes after the reply; null leaves it as it is.
    then_status: str | None = tickets.WAITING

    model_config = ConfigDict(extra="forbid")


@router.post("/tickets/{ticket_id}/replies", status_code=201)
async def support_reply(
    ticket_id: int, body: StaffReply, staff: Staff
) -> dict[str, Any]:
    """A reply the customer reads. AI drafts are never sent from here: only
    what a person typed and pressed Send on."""
    try:
        return await tickets.reply_as_staff(
            ticket_id=ticket_id,
            staff_id=staff.id,
            body=body.body,
            client_key=body.client_key,
            then_status=body.then_status,
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tickets.TicketError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class NoteWrite(BaseModel):
    body: str = Field(min_length=1, max_length=tickets.BODY_LIMIT)
    client_key: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


@router.post("/tickets/{ticket_id}/notes", status_code=201)
async def support_note(ticket_id: int, body: NoteWrite, staff: Staff) -> dict[str, Any]:
    """An internal note. Staff only; never part of the customer's thread."""
    try:
        return await tickets.add_note(
            ticket_id=ticket_id,
            staff_id=staff.id,
            body=body.body,
            client_key=body.client_key,
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tickets.TicketError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class CaseUpdate(BaseModel):
    expected_version: int
    assignee_user_id: int | None = None
    severity: str | None = None
    status: str | None = None
    linked_incident: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


@router.patch("/tickets/{ticket_id}")
async def support_update(
    ticket_id: int, body: CaseUpdate, staff: Staff
) -> dict[str, Any]:
    """Assign, set severity or status, link an incident. Only the fields
    sent change (an explicit null assignee unassigns); a stale version is
    a 409 with the case as it is now."""
    changes = {
        name: getattr(body, name)
        for name in body.model_fields_set
        if name != "expected_version"
    }
    for name in ("severity", "status"):
        if name in changes and changes[name] is None:
            raise HTTPException(status_code=422, detail=f"{name} cannot be empty.")
    try:
        return await tickets.update_case(
            ticket_id=ticket_id,
            staff_id=staff.id,
            expected_version=body.expected_version,
            changes=changes,
        )
    except tickets.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except tickets.Stale as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), "current": exc.current}
        ) from exc
    except tickets.TicketError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/attachments/{attachment_id}/link")
async def attachment_link(attachment_id: int, staff: Staff) -> dict[str, Any]:
    try:
        url = await attachments.staff_link(
            attachment_id=attachment_id, staff_id=staff.id
        )
    except attachments.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except attachments.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"url": url, "expires_in": attachments.LINK_SECONDS}


@router.get("/staff")
async def support_staff() -> dict[str, Any]:
    """Who a case can be assigned to."""
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(UserModel.id, UserModel.email, UserModel.staff_role)
            .where(UserModel.staff_role.is_not(None))
            .order_by(UserModel.id)
        )
        return {
            "staff": [
                {"id": uid, "email": email, "role": role}
                for uid, email, role in rows.all()
            ]
        }


@router.get("/summary")
async def support_summary(days: int = 30) -> dict[str, Any]:
    return await tickets.summary(min(max(days, 1), 365))


# --- typed support actions (screen 33) --------------------------------------


def _action_error(exc: Exception) -> HTTPException:
    if isinstance(exc, actions.NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, actions.Forbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, commands.InvalidTarget):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@actions_router.get("/commands")
async def support_commands(organization_id: int | None = None) -> dict[str, Any]:
    """Every typed command and its state for the workspace: available,
    needs setup or unavailable, each with the reason."""
    return {"commands": commands.catalogue(organization_id)}


class ActionTarget(BaseModel):
    kind: str = Field(max_length=32)
    organization_id: int
    target_user_id: int | None = None
    ticket_id: int | None = None
    params: dict[str, Any] = {}

    model_config = ConfigDict(extra="forbid")


@actions_router.post("/preview")
async def support_action_preview(body: ActionTarget) -> dict[str, Any]:
    try:
        return await actions.preview(
            kind=body.kind,
            organization_id=body.organization_id,
            target_user_id=body.target_user_id,
            ticket_id=body.ticket_id,
            params=body.params,
        )
    except (actions.ActionError, commands.InvalidTarget) as exc:
        raise _action_error(exc) from exc


class ActionRequest(ActionTarget):
    reason: str = Field(min_length=5, max_length=500)
    #: The preview version the requester read; a different one is a 409.
    expected_version: str | None = Field(default=None, max_length=64)


@actions_router.post("", status_code=201)
async def support_action_request(
    body: ActionRequest,
    response: Response,
    staff: Staff,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(
            status_code=422, detail="An Idempotency-Key header is required."
        )
    try:
        row, created = await actions.request(
            staff=staff,
            kind=body.kind,
            organization_id=body.organization_id,
            target_user_id=body.target_user_id,
            ticket_id=body.ticket_id,
            params=body.params,
            reason=body.reason,
            idempotency_key=idempotency_key,
            expected_version=body.expected_version,
        )
    except (actions.ActionError, commands.InvalidTarget) as exc:
        raise _action_error(exc) from exc
    if not created:
        response.status_code = 200
    return {"action": row, "created": created}


@actions_router.get("")
async def support_action_list(
    state: str | None = None, ticket_id: int | None = None
) -> dict[str, Any]:
    try:
        return {"actions": await actions.list_actions(state=state, ticket_id=ticket_id)}
    except actions.ActionError as exc:
        raise _action_error(exc) from exc


@actions_router.get("/{action_id}")
async def support_action(action_id: int) -> dict[str, Any]:
    try:
        return await actions.get(action_id)
    except actions.ActionError as exc:
        raise _action_error(exc) from exc


class Approve(BaseModel):
    version: str = Field(max_length=64)

    model_config = ConfigDict(extra="forbid")


@actions_router.post("/{action_id}/approve")
async def support_action_approve(
    action_id: int, body: Approve, staff: Staff
) -> dict[str, Any]:
    try:
        return await actions.approve(
            action_id=action_id, staff=staff, version=body.version
        )
    except (actions.ActionError, commands.InvalidTarget) as exc:
        raise _action_error(exc) from exc


class Reject(BaseModel):
    note: str = Field(min_length=3, max_length=500)

    model_config = ConfigDict(extra="forbid")


@actions_router.post("/{action_id}/reject")
async def support_action_reject(
    action_id: int, body: Reject, staff: Staff
) -> dict[str, Any]:
    try:
        return await actions.reject(action_id=action_id, staff=staff, note=body.note)
    except actions.ActionError as exc:
        raise _action_error(exc) from exc


@actions_router.post("/{action_id}/withdraw")
async def support_action_withdraw(action_id: int, staff: Staff) -> dict[str, Any]:
    try:
        return await actions.withdraw(action_id=action_id, staff=staff)
    except actions.ActionError as exc:
        raise _action_error(exc) from exc


class Revise(BaseModel):
    params: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


@actions_router.post("/{action_id}/revise")
async def support_action_revise(
    action_id: int, body: Revise, staff: Staff
) -> dict[str, Any]:
    try:
        return await actions.revise(
            action_id=action_id, staff=staff, params=body.params
        )
    except (actions.ActionError, commands.InvalidTarget) as exc:
        raise _action_error(exc) from exc


@actions_router.post("/{action_id}/run", status_code=202)
async def support_action_run(
    action_id: int, response: Response, staff: Staff
) -> dict[str, Any]:
    """Accepted is queued, not succeeded: the worker runs it and the result
    shows on the action. A repeat while it is in flight is a 200, unchanged."""
    try:
        row, queued = await actions.run(action_id=action_id, staff=staff)
    except actions.ActionError as exc:
        raise _action_error(exc) from exc
    if not queued:
        response.status_code = 200
    return {"action": row, "queued": queued}


@actions_router.post("/{action_id}/reconcile")
async def support_action_reconcile(action_id: int, staff: Staff) -> dict[str, Any]:
    try:
        return await actions.reconcile(action_id=action_id, staff=staff)
    except actions.ActionError as exc:
        raise _action_error(exc) from exc

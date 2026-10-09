"""The Approve / Reject links mailed to invite approvers.

No session: the signed token in the link is the authority, and the service
checks it is unexpired and that the request is still pending
(``services/auth/invite_requests.py``). GET reads, POST decides, so a mail
scanner fetching the link changes nothing. Not behind the ``early_access``
flag: a request already mailed must stay decidable whatever the door does.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.routes.shell import _client_ip, _limit
from api.services.auth import invite_requests

router = APIRouter(prefix="/public/invite-requests", tags=["public-invite-requests"])


class InviteRequestView(BaseModel):
    id: int
    name: str | None = None
    email: str
    note: str | None = None
    occupation: str | None = None
    source: str | None = None
    #: ``pending``, ``approved`` or ``rejected``.
    state: str
    created_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    #: "Already approved by … on …" once decided.
    decided_message: str | None = None


class InviteDecisionPreview(BaseModel):
    #: ``approve`` or ``reject``: what the confirm button will do.
    action: str
    request: InviteRequestView


class InviteDecisionBody(BaseModel):
    token: str = Field(min_length=10, max_length=2000)


class InviteDecisionResult(BaseModel):
    action: str | None = None
    #: ``approved``, ``rejected`` or ``already_decided``.
    outcome: str
    message: str
    request: InviteRequestView
    mail_sent: bool | None = None
    #: Only when the welcome mail failed, so the approver can pass it on.
    signup_link: str | None = None


@router.get("/decide", response_model=InviteDecisionPreview)
async def preview_invite_decision(
    request: Request, token: str = Query(min_length=10, max_length=2000)
) -> dict:
    """What a mailed link would do. Changes nothing."""
    await _limit(request, "invite_decision_ip", _client_ip(request), 60)
    try:
        return await invite_requests.preview(token)
    except invite_requests.InvalidDecisionLink as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/decide", response_model=InviteDecisionResult)
async def confirm_invite_decision(body: InviteDecisionBody, request: Request) -> dict:
    """Carry out a mailed link, once."""
    await _limit(request, "invite_decision_ip", _client_ip(request), 60)
    try:
        return await invite_requests.decide(body.token)
    except invite_requests.InvalidDecisionLink as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

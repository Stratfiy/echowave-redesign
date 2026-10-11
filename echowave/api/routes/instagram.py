"""Instagram messages: connect the account, and Meta's webhook.

Signed in (``/instagram``): what is connected, the sign-in link the thread's
button opens, and disconnecting. The link and disconnecting are an admin's,
like every connection that answers for the whole workspace.

Public (``/public/instagram``): the page Instagram sends the owner back to
after signing in, and the webhook. The callback trusts nothing but the
signed ``state``; the webhook nothing but Meta's signature. A message is
answered in the background, so Meta gets its 200 at once and never retries
a message that is being answered.
"""

from __future__ import annotations

from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services.auth.depends import get_user
from api.services.messaging import instagram
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/instagram", tags=["instagram"])
public_router = APIRouter(prefix="/public/instagram", tags=["public-instagram"])


class InstagramAccountResponse(BaseModel):
    configured: bool
    connected: bool
    username: str | None = None
    answered_by: str | None = None


class InstagramConnectResponse(BaseModel):
    authorize_url: str


async def _organization(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if not instagram.enabled(organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return organization_id


async def _admin(user: UserModel, organization_id: int) -> None:
    membership = await db_client.get_membership(user.id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    if rank < ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]:
        raise HTTPException(
            status_code=403,
            detail="Only a workspace admin can connect or disconnect Instagram.",
        )


@router.get("/account", response_model=InstagramAccountResponse)
async def get_account(
    user: Annotated[UserModel, Depends(get_user)],
) -> InstagramAccountResponse:
    organization_id = await _organization(user)
    account = await db_client.get_instagram_account(organization_id)
    if account is None:
        return InstagramAccountResponse(
            configured=instagram.is_configured(), connected=False
        )
    agent = (
        await db_client.get_workflow(
            account.workflow_id, organization_id=organization_id
        )
        if account.workflow_id
        else None
    )
    return InstagramAccountResponse(
        configured=instagram.is_configured(),
        connected=True,
        username=account.username,
        answered_by=agent.name if agent else None,
    )


@router.post("/connect", response_model=InstagramConnectResponse)
async def connect(
    user: Annotated[UserModel, Depends(get_user)],
) -> InstagramConnectResponse:
    organization_id = await _organization(user)
    await _admin(user, organization_id)
    try:
        return InstagramConnectResponse(
            authorize_url=instagram.authorize_url(organization_id, user.id)
        )
    except instagram.InstagramError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/account", status_code=200)
async def disconnect(
    user: Annotated[UserModel, Depends(get_user)],
) -> dict[str, str]:
    organization_id = await _organization(user)
    await _admin(user, organization_id)
    account = await db_client.get_instagram_account(organization_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram is not connected.")
    await db_client.disconnect_instagram_account(
        organization_id=organization_id, account_id=account.id
    )
    return {"status": "disconnected"}


def _page(title: str, line: str) -> HTMLResponse:
    """What the owner sees in the tab Instagram sends back. The thread is in
    the other tab, so this only says what happened and to go back."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:15vh auto;"
        "padding:0 1rem;line-height:1.5'>"
        f"<h1 style='font-size:1.25rem'>{escape(title)}</h1><p>{escape(line)}</p>"
        "<p>You can close this tab and go back to Decibyl.</p></body>"
    )


@public_router.get("/callback", response_class=HTMLResponse)
async def callback(
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    if not code or not state:
        return _page(
            "Instagram was not connected",
            error_description or "The sign-in was cancelled.",
        )
    try:
        account = await instagram.complete_login(code=code, state=state)
    except instagram.InstagramError as exc:
        return _page("Instagram was not connected", str(exc))
    except Exception as exc:  # noqa: BLE001 - say so; never a stack trace
        logger.exception("Instagram sign-in failed: {}", exc)
        return _page(
            "Instagram was not connected",
            "Something went wrong on our side. Please try again in a minute.",
        )
    return _page(
        "Instagram is connected",
        f"@{account.username} is connected. Tell Decibyl which agent should "
        "answer its messages.",
    )


@public_router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    challenge = instagram.challenge_for(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed.")
    return challenge


@public_router.post("/webhook", status_code=200)
async def receive(
    request: Request,
    x_hub_signature_256: Annotated[
        str | None, Header(alias="X-Hub-Signature-256")
    ] = None,
) -> dict[str, Any]:
    raw = await request.body()
    if not instagram.verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Bad signature.")
    try:
        payload = await request.json()
    except ValueError:
        return {"status": "unreadable"}
    queued = 0
    for incoming in instagram.parse(payload):
        try:
            await enqueue_job(
                FunctionNames.ANSWER_INSTAGRAM_MESSAGE,
                incoming.account_ig_id,
                incoming.sender_id,
                incoming.message_id,
                incoming.text,
            )
            queued += 1
        except Exception as exc:  # noqa: BLE001 - one message, not all
            logger.exception(
                "Could not queue Instagram message {}: {}", incoming.message_id, exc
            )
    return {"status": "ok", "queued": queued}

"""HTTP surface for where a bot posts its events.

The other half of the trigger URL. A bot could be started from outside for a
while -- every trigger has a public URL and every bot has an address -- and
nothing could go the other way, so a bot that filed an outcome was the end of
the chain. This is the field somebody pastes their n8n (or Zapier, or their
own server's) URL into.

Org-scoped from the authenticated user, never from the body: a workflow id in
a URL proves a row exists, not that the caller may point it anywhere.

Admin to change it, like the routine routes next door. An outbound URL is
where this account's business events go; it is not a preference.

The secret comes back exactly once, on the save that creates it. A rotation
is an explicit ask, so moving the URL cannot quietly invalidate every
signature the receiver already knows how to check.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import AgentEventKind, OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.integrations import bot_event_webhook
from api.services.workflow import bot_notices
from api.utils.url_security import validate_user_configured_service_url

router = APIRouter(
    prefix="/workflows/{workflow_id}/event-webhook", tags=["event-webhooks"]
)

#: Every kind the timeline can write. The endpoint takes any of them: a
#: person wiring a pipeline knows what they want, and curating the list is
#: the screen's job. What the screen offers is bot_notices.CATALOGUE.
KNOWN_KINDS = frozenset(k.value for k in AgentEventKind)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _owned_workflow(workflow_id: int, organization_id: int):
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="No such bot here")
    return workflow


class EventWebhookWrite(BaseModel):
    url: str = Field(max_length=2048)
    #: Empty means the events a person can subscribe to on the bell, which is
    #: resolved when an event happens rather than copied in here -- so a kind
    #: added later reaches a webhook set up today.
    kinds: list[str] = Field(default_factory=list, max_length=60)
    is_active: bool = True
    #: Ask for a new signing secret. Off by default: a save about the URL
    #: must not silently break a receiver that is already checking
    #: signatures.
    rotate_secret: bool = False


class EventWebhookResponse(BaseModel):
    url: str
    kinds: list[str]
    is_active: bool
    #: The kinds this will actually send, with ``kinds`` empty resolved to the
    #: bell's set. Said out loud because "empty means the default set" is a
    #: rule nobody should have to infer from a blank field.
    sending: list[str]
    updated_at: Optional[datetime] = None
    #: Present only on the response that created or rotated it. Null every
    #: other time, because it is not stored anywhere it could be read back
    #: from and a field that sometimes holds a secret is one people learn to
    #: screenshot.
    secret: Optional[str] = None


def _as_response(row, *, secret: Optional[str] = None) -> EventWebhookResponse:
    kinds = [k for k in (row.kinds or []) if isinstance(k, str)]
    return EventWebhookResponse(
        url=row.url,
        kinds=kinds,
        is_active=bool(row.is_active),
        sending=sorted(kinds or bot_notices.NOTIFIABLE),
        updated_at=row.updated_at,
        secret=secret,
    )


@router.get("", response_model=Optional[EventWebhookResponse])
async def get_event_webhook(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> Optional[EventWebhookResponse]:
    """This bot's webhook, or null when it has none.

    Null rather than a 404: "this bot has no webhook" is the ordinary state
    of most bots, and a screen should not have to treat it as an error to
    render an empty field.
    """
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    row = await db_client.get_bot_event_webhook(
        workflow_id, organization_id=organization_id
    )
    return None if row is None else _as_response(row)


@router.put("", response_model=EventWebhookResponse)
async def save_event_webhook(
    workflow_id: int,
    body: EventWebhookWrite,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> EventWebhookResponse:
    """Point this bot at a URL, or change where it already points."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="A URL is needed")
    try:
        # Refused here so the person sees why while the field is in front of
        # them. Checked again at send time, because a hostname that resolves
        # publicly now can be re-pointed later and the attempt is the moment
        # that matters.
        validate_user_configured_service_url(url, field_name="URL")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    unknown = sorted({k for k in body.kinds if k not in KNOWN_KINDS})
    if unknown:
        # Named rather than dropped: a kind that is silently ignored is a
        # webhook somebody believes is subscribed to something it is not.
        raise HTTPException(
            status_code=422,
            detail=f"Not events this system writes: {', '.join(unknown)}",
        )

    existing = await db_client.get_bot_event_webhook(
        workflow_id, organization_id=organization_id
    )
    minted = (
        bot_event_webhook.new_secret()
        if existing is None or body.rotate_secret
        else existing.secret
    )
    row, created = await db_client.save_bot_event_webhook(
        workflow_id=workflow_id,
        organization_id=organization_id,
        url=url,
        secret=minted,
        kinds=body.kinds,
        is_active=body.is_active,
    )
    if body.rotate_secret and not created:
        await db_client.rotate_bot_event_webhook_secret(
            workflow_id=workflow_id, organization_id=organization_id, secret=minted
        )
    show = minted if (created or body.rotate_secret) else None
    return _as_response(row, secret=show)


@router.delete("", status_code=204)
async def delete_event_webhook(
    workflow_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> None:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    gone = await db_client.delete_bot_event_webhook(
        workflow_id, organization_id=organization_id
    )
    if not gone:
        raise HTTPException(status_code=404, detail="This bot has no webhook")


class EventWebhookTestResponse(BaseModel):
    sent: bool
    detail: str = ""


@router.post("/test", response_model=EventWebhookTestResponse)
async def test_event_webhook(
    workflow_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> EventWebhookTestResponse:
    """Send one sample delivery now.

    The thing anybody building against a webhook needs first: an actual POST
    to look at, so the flow on the other end can be written against the real
    body and the real headers rather than against documentation. Queued
    through the same engine as a real event, so what arrives is what will
    arrive.
    """
    organization_id = _organization_id(user)
    workflow = await _owned_workflow(workflow_id, organization_id)
    row = await db_client.get_bot_event_webhook(
        workflow_id, organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="This bot has no webhook")

    sent = await bot_event_webhook.send_test(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_name=getattr(workflow, "name", None),
        url=row.url,
    )
    if not sent:
        return EventWebhookTestResponse(
            sent=False, detail="Could not queue the test delivery"
        )
    return EventWebhookTestResponse(
        sent=True,
        detail="Sent. It arrives as a POST with a decibyl.test event.",
    )

"""A person's identity, connections and notifications over HTTP (launch
stream `identity`; IDENTITY.md).

Thin: each route resolves the signed-in person and their workspace and hands
over to the service that owns the rule. Every group is behind its own flag,
honouring per-workspace overrides, and is a 404 while off. No route takes
another person's id: what is read and written is always the caller's own,
in the caller's selected workspace.

* ``/me/connections`` -- apps and channels (screen 22).
* ``/me/email-identity`` -- the Decibyl address (screen 23); public inbound
  and events webhooks under ``/public/email-identity``.
* ``/me/phone-identity`` -- phone and verification (screen 24).
* ``/me/notifications``, ``/me/push-subscriptions`` -- screen 21.
* ``/me/identity-cards`` -- the person's own private action cards.
* ``/me/outcomes/{event_id}`` -- "did it arrive?" for an unknown send.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features
from api.services.auth.depends import get_user, require_verified_email
from api.services.identity import (
    cards,
    connections,
    email_identity,
    mobile_push,
    notifications,
    phone,
    reconcile,
)

router = APIRouter(tags=["identity"])

_connections = Depends(features.require(connections.FLAG, per_organization=True))
_email = Depends(features.require(email_identity.FLAG, per_organization=True))
_phone = Depends(features.require(phone.FLAG, per_organization=True))
_notifications = Depends(features.require(notifications.FLAG, per_organization=True))
_reconcile = Depends(features.require(reconcile.FLAG, per_organization=True))


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _is_admin(user: UserModel, organization_id: int) -> bool:
    membership = await db_client.get_membership(user.id, organization_id)
    rank = ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)
    return rank >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]


# --- cards ------------------------------------------------------------------


class IdentityCard(BaseModel):
    """One private card, in the shape the screen's approval panel reads."""

    event_id: int
    action: str
    label: str
    effect: str | None = None
    state: str
    #: The payload version a Confirm must name (task ledger); None while
    #: the ledger is off.
    version: str | None = None
    revisions: int = 0
    args: dict[str, Any] = Field(default_factory=dict)
    affected: list[str] = Field(default_factory=list)
    error: str | None = None
    done_note: str | None = None
    fires_at: str | None = None
    needs_person: bool = False
    reconciled: dict[str, Any] | None = None
    at: datetime | None = None


#: The arguments a screen may show. A send's body is the person's own
#: words, shown back to the person only (the card is private).
_SHOWN_ARGS = (
    "toolkit",
    "scope",
    "from_address",
    "to",
    "subject",
    "body",
    "address",
    "helper_name",
)


def _card(row: Any) -> IdentityCard:
    payload = dict(row.payload or {})
    args = payload.get("args") or {}
    return IdentityCard(
        event_id=row.id,
        action=str(payload.get("action") or ""),
        label=str(payload.get("label") or row.summary or ""),
        effect=payload.get("effect"),
        state=str(payload.get("state") or "proposed"),
        version=payload.get("version"),
        revisions=len(payload.get("revisions") or []) + 1,
        args={k: args[k] for k in _SHOWN_ARGS if k in args},
        affected=list(payload.get("affected") or []),
        error=payload.get("error"),
        done_note=(payload.get("done") or {}).get("note"),
        fires_at=payload.get("fires_at"),
        needs_person=bool((payload.get("reconcile") or {}).get("needs_person")),
        reconciled=payload.get("reconciled"),
        at=getattr(row, "at", None),
    )


async def _proposed(
    organization_id: int, user: UserModel, arguments: dict[str, Any]
) -> IdentityCard:
    try:
        told = await cards.propose(
            organization_id=organization_id, user_id=user.id, arguments=arguments
        )
    except cards.CardError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = await db_client.get_agent_event(
        told["event_id"], organization_id=organization_id
    )
    if row is None:
        raise HTTPException(status_code=409, detail="Could not put that on a card.")
    return _card(row)


class IdentityCards(BaseModel):
    cards: list[IdentityCard]


@router.get("/me/identity-cards", response_model=IdentityCards)
async def my_identity_cards(
    user: Annotated[UserModel, Depends(get_user)],
) -> IdentityCards:
    """The person's own identity cards in this workspace (any identity flag)."""
    organization_id = _organization_id(user)
    if not any(
        features.is_on(flag, organization_id)
        for flag in (connections.FLAG, email_identity.FLAG, phone.FLAG)
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    rows = await cards.mine(organization_id, user.id)
    return IdentityCards(cards=[_card(r) for r in rows])


class OutcomeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arrived: bool


class OutcomeSettled(BaseModel):
    state: str
    reconciled: dict[str, Any] | None = None


@router.post(
    "/me/outcomes/{event_id}", response_model=OutcomeSettled, dependencies=[_reconcile]
)
async def say_whether_it_arrived(
    event_id: int, body: OutcomeAnswer, user: Annotated[UserModel, Depends(get_user)]
) -> OutcomeSettled:
    """For a send whose outcome is unknown: the person who approved it says
    whether it arrived. Nothing is ever sent again."""
    organization_id = _organization_id(user)
    try:
        payload = await reconcile.person_says(
            organization_id,
            event_id,
            user.id,
            arrived=body.arrived,
            is_admin=await _is_admin(user, organization_id),
        )
    except reconcile.NotYours as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return OutcomeSettled(
        state=payload.get("state"), reconciled=payload.get("reconciled")
    )


# --- connections (screen 22) ------------------------------------------------


class AppItem(BaseModel):
    id: str
    toolkit: str
    app_name: str
    account: str | None = None
    owner: Literal["you", "workspace"]
    scope: str
    state: str
    reason: str | None = None
    connected_at: str | None = None
    last_success_at: str | None = None
    access: list[str] = Field(default_factory=list)
    purpose: str | None = None
    can_disconnect: bool = False
    consent_id: int | None = None


class AppsSection(BaseModel):
    state: Literal["ok", "needs_setup", "error"]
    reason: str | None = None
    per_person: bool
    items: list[AppItem]


class LinkedChannel(BaseModel):
    id: int
    display_name: str | None = None
    handle: str
    linked_at: str | None = None


class ChannelItem(BaseModel):
    channel: str
    name: str
    capability: Literal["available", "needs_setup", "disabled_by_policy", "unavailable"]
    reason: str | None = None
    verified_at: str | None = None
    last_delivery_ok_at: str | None = None
    delivery_failing: bool
    proactive: str
    reads_other_messages: bool
    state: str
    linked: list[LinkedChannel]


class ConnectionsResponse(BaseModel):
    apps: AppsSection
    channels: list[ChannelItem]
    is_admin: bool


@router.get(
    "/me/connections", response_model=ConnectionsResponse, dependencies=[_connections]
)
async def my_connections(
    user: Annotated[UserModel, Depends(get_user)],
) -> ConnectionsResponse:
    organization_id = _organization_id(user)
    admin = await _is_admin(user, organization_id)
    apps = await connections.apps(organization_id, user.id, is_admin=admin)
    rows = await connections.channels(organization_id, user.id)
    return ConnectionsResponse(
        apps=AppsSection(**apps),
        channels=[ChannelItem(**r) for r in rows],
        is_admin=admin,
    )


class ConnectionPreview(BaseModel):
    toolkit: str
    app_name: str
    access: list[str]
    per_person: bool
    can_connect_for_workspace: bool


@router.get(
    "/me/connections/preview",
    response_model=ConnectionPreview,
    dependencies=[_connections],
)
async def preview_connection(
    toolkit: Annotated[str, Query(min_length=2, max_length=64)],
    user: Annotated[UserModel, Depends(get_user)],
) -> ConnectionPreview:
    """What connecting this app grants, in the words that are stored with
    the consent -- shown before Connect (handoff 22)."""
    organization_id = _organization_id(user)
    try:
        view = await connections.preview(toolkit)
    except connections.ConnectionError_ as exc:
        _raise(exc)
    return ConnectionPreview(
        **view, can_connect_for_workspace=await _is_admin(user, organization_id)
    )


class StartConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    toolkit: str = Field(min_length=2, max_length=64)
    scope: Literal["mine", "workspace"] = "mine"
    purpose: str | None = Field(default=None, max_length=200)
    return_to: str | None = Field(default=None, max_length=500)


class StartedConnection(BaseModel):
    consent_id: int
    url: str
    expires_at: str | None = None
    access: list[str]
    app_name: str


def _raise(exc: connections.ConnectionError_) -> None:
    raise HTTPException(
        status_code=exc.status, detail={"code": exc.code, "message": str(exc)}
    )


@router.post(
    "/me/connections/start",
    response_model=StartedConnection,
    dependencies=[_connections],
)
async def start_connection(
    body: StartConnection, user: Annotated[UserModel, Depends(get_user)]
) -> StartedConnection:
    """Record the consent (what was shown, why, where to return), then hand
    back the app's own sign-in page."""
    organization_id = _organization_id(user)
    try:
        started = await connections.start(
            organization_id=organization_id,
            user_id=user.id,
            toolkit=body.toolkit,
            scope=body.scope,
            purpose=body.purpose,
            return_to=body.return_to,
            is_admin=await _is_admin(user, organization_id),
        )
    except connections.ConnectionError_ as exc:
        _raise(exc)
    return StartedConnection(**started)


class ConsentView(BaseModel):
    consent_id: int
    toolkit: str
    scope: str
    state: str
    reason: str | None = None
    reason_code: str | None = None
    purpose: str | None = None
    access: list[str]
    return_to: str | None = None
    connected_account_id: str | None = None
    started_at: datetime | None = None
    ready_at: datetime | None = None


@router.post(
    "/me/connections/consents/{consent_id}/complete",
    response_model=ConsentView,
    dependencies=[_connections],
)
async def complete_connection(
    consent_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> ConsentView:
    """Back from the app's sign-in: what actually happened."""
    try:
        view = await connections.complete(
            organization_id=_organization_id(user),
            user_id=user.id,
            consent_id=consent_id,
        )
    except connections.ConnectionError_ as exc:
        _raise(exc)
    return ConsentView(**view)


class Disconnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["mine", "workspace"]
    connected_account_id: str = Field(min_length=1, max_length=128)


@router.post(
    "/me/connections/disconnect",
    response_model=IdentityCard,
    dependencies=[_connections],
)
async def propose_disconnect(
    body: Disconnect, user: Annotated[UserModel, Depends(get_user)]
) -> IdentityCard:
    """Disconnecting deletes the account at the app, so it is a card: the
    exact connection and what stops working, then Approve."""
    organization_id = _organization_id(user)
    if body.scope == "workspace" and not await _is_admin(user, organization_id):
        raise HTTPException(
            status_code=403, detail="Only a workspace admin can disconnect that."
        )
    return await _proposed(
        organization_id,
        user,
        {
            "action": cards.DISCONNECT_APP,
            "scope": body.scope,
            "connected_account_id": body.connected_account_id,
        },
    )


# --- email identity (screen 23) ---------------------------------------------


class EmailReady(BaseModel):
    ready: bool
    reason: str | None = None


class EmailThread(BaseModel):
    thread_key: str
    from_address: str | None = None
    subject: str | None = None
    received_at: datetime
    messages: int
    attachments: list[dict[str, Any]]
    preview: str


class EmailIdentityView(BaseModel):
    state: str
    alias: str | None = None
    address: str | None = None
    pending_address: str | None = None
    domain: str
    next_step: str | None = None
    issue_code: str | None = None
    revision: int
    reserved_at: datetime | None = None
    active_at: datetime | None = None
    inbound: EmailReady
    outbound: EmailReady
    card_interest: bool
    threads: list[EmailThread] = Field(default_factory=list)


async def _email_view(user_id: int) -> EmailIdentityView:
    view = await email_identity.view(user_id)
    view["threads"] = await email_identity.threads(user_id)
    return EmailIdentityView(**view)


def _alias_error(exc: email_identity.AliasError) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail={"code": exc.code, "message": str(exc)}
    )


@router.get(
    "/me/email-identity", response_model=EmailIdentityView, dependencies=[_email]
)
async def my_email_identity(
    user: Annotated[UserModel, Depends(get_user)],
) -> EmailIdentityView:
    _organization_id(user)
    return await _email_view(user.id)


class AliasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(max_length=64)


class AliasCheck(BaseModel):
    alias: str
    available: bool
    reason: Literal["invalid", "reserved", "taken", "yours"] | None = None
    message: str | None = None


@router.post(
    "/me/email-identity/check", response_model=AliasCheck, dependencies=[_email]
)
async def check_alias(
    body: AliasRequest, user: Annotated[UserModel, Depends(get_user)]
) -> AliasCheck:
    return AliasCheck(**await email_identity.check(user.id, body.alias))


@router.post(
    "/me/email-identity/reserve",
    response_model=EmailIdentityView,
    dependencies=[_email],
)
async def reserve_alias(
    body: AliasRequest, user: Annotated[UserModel, Depends(require_verified_email)]
) -> EmailIdentityView:
    """Reserve after account verification (handoff 7)."""
    try:
        await email_identity.reserve(user.id, _organization_id(user), body.alias)
    except email_identity.AliasError as exc:
        raise _alias_error(exc) from exc
    return await _email_view(user.id)


@router.post(
    "/me/email-identity/provision",
    response_model=EmailIdentityView,
    dependencies=[_email],
)
async def provision_alias(
    user: Annotated[UserModel, Depends(get_user)],
) -> EmailIdentityView:
    try:
        await email_identity.provision(user.id)
    except email_identity.AliasError as exc:
        raise _alias_error(exc) from exc
    except email_identity.NeedsSetup as exc:
        raise HTTPException(
            status_code=503, detail={"code": "needs_setup", "message": str(exc)}
        ) from exc
    return await _email_view(user.id)


@router.post(
    "/me/email-identity/release",
    response_model=EmailIdentityView,
    dependencies=[_email],
)
async def release_alias(
    user: Annotated[UserModel, Depends(get_user)],
) -> EmailIdentityView:
    """Give up a reservation never used; an active address is not released here."""
    try:
        await email_identity.release(user.id)
    except email_identity.AliasError as exc:
        raise _alias_error(exc) from exc
    return await _email_view(user.id)


class SendFromAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(max_length=320)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=20_000)


@router.post(
    "/me/email-identity/send", response_model=IdentityCard, dependencies=[_email]
)
async def propose_send(
    body: SendFromAddress, user: Annotated[UserModel, Depends(get_user)]
) -> IdentityCard:
    """Sending is a card: the exact account, recipient and words, then Approve."""
    return await _proposed(
        _organization_id(user),
        user,
        {
            "action": cards.SEND_IDENTITY_EMAIL,
            "to": body.to,
            "subject": body.subject,
            "body": body.body,
        },
    )


class CardInterest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interested: bool


@router.put("/me/card-interest", response_model=CardInterest, dependencies=[_email])
async def card_interest(
    body: CardInterest, user: Annotated[UserModel, Depends(get_user)]
) -> CardInterest:
    """Interest in the virtual card, coming soon. No card details, ever."""
    return CardInterest(
        interested=await email_identity.set_card_interest(user.id, body.interested)
    )


@router.post(
    "/public/email-identity/inbound",
    dependencies=[Depends(features.require(email_identity.FLAG))],
)
async def email_identity_inbound(
    request: Request,
    x_decibyl_signature: Annotated[str | None, Header()] = None,
    x_decibyl_timestamp: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """The mail provider's inbound webhook: ``{recipient, raw_base64}``,
    signed. A bad signature is a 403; everything else is a 200 with a status
    word, so the provider never retries a message we chose to quarantine."""
    raw = await request.body()
    if not email_identity.verify(raw, x_decibyl_signature, x_decibyl_timestamp):
        raise HTTPException(status_code=403, detail="Bad signature.")
    try:
        recipient, mime = email_identity.payload_to_raw(await request.json())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": await email_identity.receive(recipient, mime)}


@router.post(
    "/public/email-identity/events",
    dependencies=[Depends(features.require(email_identity.FLAG))],
)
async def email_identity_events(
    request: Request,
    x_decibyl_signature: Annotated[str | None, Header()] = None,
    x_decibyl_timestamp: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Delivery, bounce and complaint events: ``{events: [{type, message_id}]}``."""
    raw = await request.body()
    if not email_identity.verify(raw, x_decibyl_signature, x_decibyl_timestamp):
        raise HTTPException(status_code=403, detail="Bad signature.")
    try:
        items = (await request.json()).get("events") or []
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Unreadable events.") from exc
    return {"applied": await email_identity.provider_events(list(items))}


# --- phone and verification (screen 24) -------------------------------------


class PhoneNumberRow(BaseModel):
    id: int
    address: str
    label: str | None = None
    state: str
    assigned_helper_id: int | None = None
    incoming_call_ok_at: str | None = None
    escalation_ok_at: str | None = None
    provisioned_at: str | None = None


class PaymentPolicy(BaseModel):
    key: str | None = None
    decided: bool
    who_pays: str
    amount: str
    steps: list[str]


class RequestState(BaseModel):
    available: bool
    reason: str | None = None


class HelperRow(BaseModel):
    id: int
    name: str


class AutopayState(BaseModel):
    required: bool
    authorised: bool
    status: str | None = None


class PhoneIdentityView(BaseModel):
    state: str
    next_step: str | None = None
    verification_status: str | None = None
    sources: dict[str, str]
    numbers: list[PhoneNumberRow] | None = None
    helpers: list[HelperRow] | None = None
    autopay: AutopayState | None = None
    payment: PaymentPolicy
    request: RequestState
    chat_needs_number: bool
    is_admin: bool = False


@router.get(
    "/me/phone-identity", response_model=PhoneIdentityView, dependencies=[_phone]
)
async def my_phone_identity(
    user: Annotated[UserModel, Depends(get_user)],
) -> PhoneIdentityView:
    organization_id = _organization_id(user)
    view = await phone.view(organization_id)
    return PhoneIdentityView(**view, is_admin=await _is_admin(user, organization_id))


class NumberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Omitted: the workspace's managed carrier account.
    telephony_configuration_id: int | None = None
    address: str = Field(max_length=20)
    helper_id: int


@router.post(
    "/me/phone-identity/request", response_model=IdentityCard, dependencies=[_phone]
)
async def propose_number(
    body: NumberRequest, user: Annotated[UserModel, Depends(require_verified_email)]
) -> IdentityCard:
    """A number pays every month, so it is a card, and an admin's."""
    organization_id = _organization_id(user)
    if not await _is_admin(user, organization_id):
        raise HTTPException(
            status_code=403, detail="Only a workspace admin can request a number."
        )
    return await _proposed(
        organization_id,
        user,
        {
            "action": cards.REQUEST_NUMBER,
            "telephony_configuration_id": body.telephony_configuration_id,
            "address": body.address,
            "helper_id": body.helper_id,
        },
    )


class Readiness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incoming_call_ok: bool
    escalation_ok: bool


@router.post(
    "/me/phone-identity/numbers/{phone_number_id}/readiness",
    response_model=PhoneIdentityView,
    dependencies=[_phone],
)
async def record_readiness(
    phone_number_id: int, body: Readiness, user: Annotated[UserModel, Depends(get_user)]
) -> PhoneIdentityView:
    """An admin records the test call and the handover; audited."""
    organization_id = _organization_id(user)
    if not await _is_admin(user, organization_id):
        raise HTTPException(
            status_code=403, detail="Only a workspace admin can record this."
        )
    try:
        view = await phone.record_readiness(
            organization_id,
            user.id,
            phone_number_id,
            incoming_call_ok=body.incoming_call_ok,
            escalation_ok=body.escalation_ok,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    return PhoneIdentityView(**view, is_admin=True)


# --- notifications (screen 21) ----------------------------------------------


class TopicSetting(BaseModel):
    on: bool
    snoozed_until: str | None = None


class TopicMeta(BaseModel):
    name: str
    label: str
    requested: bool


class Device(BaseModel):
    id: int
    label: str
    created_at: datetime
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    state: Literal["active", "failing", "revoked"]


class ChannelAvailability(BaseModel):
    available: bool
    reason: str | None = None


class NotificationsView(BaseModel):
    channels: dict[str, bool]
    topics: dict[str, TopicSetting]
    quiet_start: str | None = None
    quiet_end: str | None = None
    private_previews: bool
    revision: int
    updated_at: datetime | None = None
    topic_list: list[TopicMeta]
    availability: dict[str, ChannelAvailability]
    devices: list[Device]
    push_public_key: str | None = None
    suggestion_daily_cap: int


async def _availability(user: UserModel) -> dict[str, ChannelAvailability]:
    from api.services.messaging.email import email_is_configured

    linked = (
        await connections.channels(user.selected_organization_id, user.id)
        if user.selected_organization_id
        else []
    )
    any_linked = any(c["linked"] for c in linked)
    push_ready = notifications.push.configured() or mobile_push.enabled(
        user.selected_organization_id
    )
    return {
        "in_app": ChannelAvailability(
            available=False,
            reason="The bell in the app is shared by your workspace, so personal notices are not put there.",
        ),
        # Browsers need the operator's VAPID keys; the native app needs only
        # ``mobile_push`` (Expo holds the Apple and Google credentials).
        "push": ChannelAvailability(
            available=push_ready,
            reason=None if push_ready else "Push is not set up on this deployment yet.",
        ),
        "email": ChannelAvailability(
            available=email_is_configured() and bool(getattr(user, "email", None)),
            reason=None
            if email_is_configured()
            else "Email is not set up on this deployment yet.",
        ),
        "channel": ChannelAvailability(
            available=any_linked,
            reason=None
            if any_linked
            else "Link WhatsApp, Telegram, Slack or Teams first.",
        ),
    }


async def _notifications_view(user: UserModel) -> NotificationsView:
    from api import constants

    prefs = await notifications.get(user.id)
    return NotificationsView(
        channels=prefs["channels"],
        topics={k: TopicSetting(**v) for k, v in prefs["topics"].items()},
        quiet_start=prefs["quiet_start"],
        quiet_end=prefs["quiet_end"],
        private_previews=prefs["private_previews"],
        revision=prefs["revision"],
        updated_at=prefs["updated_at"],
        topic_list=[
            TopicMeta(name=name, label=meta["label"], requested=meta["requested"])
            for name, meta in notifications.TOPICS.items()
        ],
        availability=await _availability(user),
        devices=[Device(**d) for d in await notifications.devices(user.id)],
        push_public_key=notifications.push.public_key(),
        suggestion_daily_cap=constants.NOTIFY_SUGGESTION_DAILY_CAP,
    )


@router.get(
    "/me/notifications", response_model=NotificationsView, dependencies=[_notifications]
)
async def my_notifications(
    user: Annotated[UserModel, Depends(get_user)],
) -> NotificationsView:
    return await _notifications_view(user)


class NotificationsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    channels: dict[str, bool] | None = None
    topics: dict[str, dict[str, Any]] | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    private_previews: bool | None = None


@router.put(
    "/me/notifications", response_model=NotificationsView, dependencies=[_notifications]
)
async def save_notifications(
    body: NotificationsChange, user: Annotated[UserModel, Depends(get_user)]
) -> NotificationsView:
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    try:
        await notifications.save(user.id, changes, revision=revision)
    except notifications.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Changed in another window.",
                "stored_revision": exc.stored["revision"],
            },
        ) from exc
    except notifications.PreferencesInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _notifications_view(user)


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class PushSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(max_length=2000)
    keys: PushKeys
    device_label: str | None = Field(default=None, max_length=80)


@router.post(
    "/me/push-subscriptions",
    response_model=NotificationsView,
    dependencies=[_notifications],
)
async def add_push_subscription(
    body: PushSubscription, user: Annotated[UserModel, Depends(get_user)]
) -> NotificationsView:
    """This browser, after the person allowed notifications."""
    if not notifications.push.configured():
        raise HTTPException(
            status_code=503, detail="Push is not set up on this deployment yet."
        )
    try:
        await notifications.subscribe(
            user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            device_label=body.device_label,
        )
    except notifications.PreferencesInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _notifications_view(user)


@router.delete(
    "/me/push-subscriptions/{subscription_id}",
    response_model=NotificationsView,
    dependencies=[_notifications],
)
async def remove_push_subscription(
    subscription_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> NotificationsView:
    if not await notifications.unsubscribe(user.id, subscription_id):
        raise HTTPException(status_code=404, detail="Not found")
    return await _notifications_view(user)


class TestResult(BaseModel):
    push: str


@router.post(
    "/me/notifications/test", response_model=TestResult, dependencies=[_notifications]
)
async def test_notification(
    user: Annotated[UserModel, Depends(get_user)],
) -> TestResult:
    """One labelled test push to this person's own devices."""
    return TestResult(**await notifications.send_test(user.id))

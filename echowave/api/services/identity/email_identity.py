"""A person's Decibyl email address (handoff 7, 25; screen 23).

Lifecycle: unallocated -> (checking) -> reserved -> provisioning -> active,
with delivery_issue and suspended beside active, and released for a
reservation given up before it was ever active.

* **Checked server-side.** A name is valid, not reserved, and not anyone's
  -- live or retired -- before it is reserved, and the reservation itself is
  held by a partial unique index, so two requests for one name cannot both
  win. Collision messages never say whose it is.
* **Retired, never reused.** An address once held by a person is never
  given to another: mail meant for the first would reach the second.
* **Active only after a verified delivery.** Provisioning sends a check
  message to the address; it becomes active when that message arrives
  through the signed inbound webhook. Configuration alone never makes it
  active.
* **Inbound.** The provider posts raw MIME to a webhook signed with
  ``EMAIL_IDENTITY_WEBHOOK_SECRET`` (HMAC-SHA256 over ``timestamp.body``,
  five-minute replay window). Size limit, deduplication on Message-ID,
  thread association from References / In-Reply-To, and attachments listed
  with a basic type check (dangerous file types are blocked; this is not an
  antivirus scan and says so). Mail for an address nobody owns is
  quarantined and shown to nobody.
* **Outbound** only through an action card, only from the person's own
  active address (no relay, no other From), and only once the domain's
  sending records are verified (``EMAIL_IDENTITY_OUTBOUND_VERIFIED``).
  Bounces and complaints come back on the events webhook.
* **Not a mailbox.** There is no IMAP and nothing claims there is.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import smtplib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError

from api import constants
from api.db import db_client
from api.db.identity_models import (
    CardInterestModel,
    DeliveryReceiptModel,
    EmailIdentityMessageModel,
    EmailIdentityModel,
    EmailIdentitySendModel,
)
from api.services import features

FLAG = "identity_email"

RESERVED = "reserved"
PROVISIONING = "provisioning"
ACTIVE = "active"
DELIVERY_ISSUE = "delivery_issue"
SUSPENDED = "suspended"
RELEASED = "released"
#: States in which mail to the address reaches its owner.
RECEIVING = (PROVISIONING, ACTIVE, DELIVERY_ISSUE)

#: Names nobody may have: roles mail systems and people expect to reach
#: the operator, and ones that would impersonate it.
RESERVED_NAMES = frozenset(
    {
        "abuse", "admin", "administrator", "api", "billing", "contact", "decibyl",
        "dmarc", "help", "hello", "hostmaster", "info", "legal", "mail",
        "mailer-daemon", "no-reply", "noreply", "notifications", "postmaster",
        "privacy", "root", "sales", "security", "staff", "support", "system",
        "team", "test", "webmaster", "www",
    }
)  # fmt: skip
_ALIAS = re.compile(r"^[a-z0-9](?:[a-z0-9.\-]{1,30})[a-z0-9]$")
#: Blocked outright whatever they claim to be.
BLOCKED_EXTENSIONS = frozenset(
    {
        "apk", "app", "bat", "cmd", "com", "cpl", "dll", "exe", "hta", "iso",
        "jar", "js", "jse", "lnk", "msi", "ps1", "scr", "vbe", "vbs", "wsf",
    }
)  # fmt: skip
REPLAY_WINDOW_SECONDS = 300
MAX_BODY_CHARS = 50_000


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def domain() -> str:
    return constants.EMAIL_IDENTITY_DOMAIN


def address_of(alias: str) -> str:
    return f"{alias}@{domain()}"


class AliasError(Exception):
    """Refused, with a specific reason that names nobody else."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


class NeedsSetup(Exception):
    """The deployment is missing what this step needs; says what."""


def _now() -> datetime:
    return datetime.now(UTC)


def normalise(alias: Any) -> str:
    return str(alias or "").strip().lower()


def _invalid_reason(alias: str) -> str | None:
    if len(alias) < 3 or len(alias) > 32:
        return "Use 3 to 32 characters."
    if not _ALIAS.match(alias) or ".." in alias or "--" in alias:
        return "Use letters, numbers, dots and hyphens, starting and ending with a letter or number."
    return None


async def _live(user_id: int) -> EmailIdentityModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(EmailIdentityModel).where(
                EmailIdentityModel.user_id == user_id,
                EmailIdentityModel.released_at.is_(None),
            )
        )


async def check(user_id: int, alias: Any) -> dict[str, Any]:
    """Whether ``alias`` could be this person's. ``reason`` is one of
    invalid, reserved, taken, yours -- never who has it."""
    wanted = normalise(alias)
    invalid = _invalid_reason(wanted)
    if invalid:
        return {
            "alias": wanted,
            "available": False,
            "reason": "invalid",
            "message": invalid,
        }
    if wanted in RESERVED_NAMES:
        return {
            "alias": wanted,
            "available": False,
            "reason": "reserved",
            "message": "That name is kept for Decibyl itself. Try another.",
        }
    async with db_client.async_session() as session:
        holders = list(
            await session.scalars(
                select(EmailIdentityModel.user_id).where(
                    EmailIdentityModel.alias == wanted
                )
            )
        )
    if holders and any(h != user_id for h in holders):
        return {
            "alias": wanted,
            "available": False,
            "reason": "taken",
            "message": "That address is taken. Try another.",
        }
    if holders:
        return {
            "alias": wanted,
            "available": False,
            "reason": "yours",
            "message": "You have used that address before.",
        }
    return {"alias": wanted, "available": True, "reason": None, "message": None}


async def reserve(
    user_id: int, organization_id: int | None, alias: Any
) -> dict[str, Any]:
    checked = await check(user_id, alias)
    if not checked["available"]:
        raise AliasError(
            checked["reason"],
            checked["message"],
            422 if checked["reason"] == "invalid" else 409,
        )
    if await _live(user_id) is not None:
        raise AliasError("has_one", "You already have a Decibyl address.")
    row = EmailIdentityModel(
        user_id=user_id,
        organization_id=organization_id,
        alias=checked["alias"],
        state=RESERVED,
    )
    try:
        async with db_client.async_session() as session:
            session.add(row)
            await session.commit()
    except IntegrityError as exc:
        # Somebody reserved the same name, or this person a second one, in
        # the moment since the check. The index decided; say which.
        if await _live(user_id) is not None:
            raise AliasError("has_one", "You already have a Decibyl address.") from exc
        raise AliasError("taken", "That address is taken. Try another.") from exc
    return await view(user_id)


def inbound_ready() -> bool:
    return bool(constants.EMAIL_IDENTITY_WEBHOOK_SECRET)


def outbound_ready() -> bool:
    from api.services.messaging.email import email_is_configured

    return bool(constants.EMAIL_IDENTITY_OUTBOUND_VERIFIED and email_is_configured())


async def provision(user_id: int) -> dict[str, Any]:
    """Reserved (or provisioning, to resend) -> provisioning: send the check
    message. Active follows only when it arrives."""
    from api.services.messaging.email import email_is_configured, send_email

    identity = await _live(user_id)
    if identity is None or identity.state not in (RESERVED, PROVISIONING):
        raise AliasError("wrong_state", "There is no reserved address to set up.")
    if not inbound_ready():
        raise NeedsSetup(
            "Receiving mail at Decibyl addresses is not set up on this deployment yet."
        )
    if not email_is_configured():
        raise NeedsSetup(
            "Sending the check message is not set up on this deployment yet."
        )
    token = secrets.token_urlsafe(18)
    async with db_client.async_session() as session:
        await session.execute(
            update(EmailIdentityModel)
            .where(EmailIdentityModel.id == identity.id)
            .values(
                state=PROVISIONING,
                provisioning_at=identity.provisioning_at or _now(),
                probe_hash=hashlib.sha256(token.encode()).hexdigest(),
                issue_code=None,
                revision=EmailIdentityModel.revision + 1,
                updated_at=_now(),
            )
        )
        await session.commit()
    sent = await send_email(
        to=address_of(identity.alias),
        subject=f"Decibyl address check {token}",
        body_text=(
            "This message checks that your Decibyl address receives mail. "
            "Nothing to do; it becomes active when this arrives."
        ),
        sender="notifications",
    )
    if not sent.ok:
        await _set_issue(identity.id, "check_not_sent")
    return await view(user_id)


async def _set_issue(identity_id: int, code: str) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            update(EmailIdentityModel)
            .where(EmailIdentityModel.id == identity_id)
            .values(issue_code=code, updated_at=_now())
        )
        await session.commit()


async def release(user_id: int) -> dict[str, Any]:
    """Give up a reservation that was never active. An active address is not
    released from here: mail may depend on it (ask Help)."""
    identity = await _live(user_id)
    if identity is None:
        raise AliasError("wrong_state", "There is no address to give up.")
    if identity.state not in (RESERVED, PROVISIONING):
        raise AliasError(
            "active",
            "An address that has received mail cannot be given up here. Ask Help.",
        )
    async with db_client.async_session() as session:
        await session.execute(
            update(EmailIdentityModel)
            .where(EmailIdentityModel.id == identity.id)
            .values(state=RELEASED, released_at=_now(), updated_at=_now())
        )
        await session.commit()
    return await view(user_id)


# --- reading ----------------------------------------------------------------


_NEXT = {
    None: "Choose an address.",
    RESERVED: "Set it up: we send a check message to it.",
    PROVISIONING: "Waiting for the check message to arrive.",
    ACTIVE: None,
    DELIVERY_ISSUE: "Mail sent from it bounced recently. Check the address you wrote to.",
    SUSPENDED: "Sending is paused after a complaint. Ask Help.",
}


async def view(user_id: int) -> dict[str, Any]:
    identity = await _live(user_id)
    async with db_client.async_session() as session:
        interested = await session.get(CardInterestModel, user_id) is not None
    state = identity.state if identity else "unallocated"
    issue = identity.issue_code if identity else None
    next_step = _NEXT.get(identity.state if identity else None)
    if issue == "check_not_sent":
        next_step = "The check message could not be sent. Try again."
    return {
        "state": state,
        "alias": identity.alias if identity else None,
        # The address is shown as usable only once active (handoff 25).
        "address": address_of(identity.alias)
        if identity and identity.state in (ACTIVE, DELIVERY_ISSUE, SUSPENDED)
        else None,
        "pending_address": address_of(identity.alias) if identity else None,
        "domain": domain(),
        "next_step": next_step,
        "issue_code": issue,
        "revision": identity.revision if identity else 0,
        "reserved_at": identity.reserved_at if identity else None,
        "active_at": identity.active_at if identity else None,
        "inbound": {
            "ready": inbound_ready(),
            "reason": None
            if inbound_ready()
            else "Receiving mail is not set up on this deployment yet.",
        },
        "outbound": {
            "ready": outbound_ready() and state == ACTIVE,
            "reason": None
            if outbound_ready()
            else "Sending from Decibyl addresses is not set up on this deployment yet.",
        },
        "card_interest": interested,
    }


async def threads(user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """The person's recent mail, one row per thread, newest first."""
    async with db_client.async_session() as session:
        latest = (
            select(
                EmailIdentityMessageModel.thread_key,
                func.max(EmailIdentityMessageModel.id).label("last_id"),
                func.count().label("messages"),
            )
            .where(
                EmailIdentityMessageModel.user_id == user_id,
                EmailIdentityMessageModel.status == "delivered",
            )
            .group_by(EmailIdentityMessageModel.thread_key)
            .subquery()
        )
        rows = (
            await session.execute(
                select(EmailIdentityMessageModel, latest.c.messages)
                .join(latest, EmailIdentityMessageModel.id == latest.c.last_id)
                .order_by(EmailIdentityMessageModel.id.desc())
                .limit(max(1, min(limit, 100)))
            )
        ).all()
    return [
        {
            "thread_key": m.thread_key,
            "from_address": m.from_address,
            "subject": m.subject,
            "received_at": m.received_at,
            "messages": count,
            "attachments": list(m.attachments or []),
            "preview": (m.body_text or "")[:200],
        }
        for m, count in rows
    ]


async def set_card_interest(user_id: int, interested: bool) -> bool:
    """Interest in the virtual card, nothing more (handoff 7)."""
    async with db_client.async_session() as session:
        row = await session.get(CardInterestModel, user_id)
        if interested and row is None:
            session.add(CardInterestModel(user_id=user_id))
        elif not interested and row is not None:
            await session.delete(row)
        await session.commit()
    return interested


# --- the inbound webhook ----------------------------------------------------


def verify(
    raw: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    now: float | None = None,
) -> bool:
    secret = constants.EMAIL_IDENTITY_WEBHOOK_SECRET
    if not secret or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - sent_at) > REPLAY_WINDOW_SECONDS:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    given = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, given)


def _thread_key(message: Any, message_id: str) -> str:
    references = str(message.get("References") or "").split()
    if references:
        return references[0][:255]
    reply_to = str(message.get("In-Reply-To") or "").strip()
    return (reply_to or message_id)[:255]


def _body_text(message: Any) -> str:
    part = None
    try:
        part = message.get_body(preferencelist=("plain", "html"))
        content = part.get_content() if part is not None else ""
    except Exception:  # noqa: BLE001 - a body that will not decode is empty
        content = ""
    if part is not None and part.get_content_type() == "text/html":
        content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"[ \t]+", " ", content).strip()[:MAX_BODY_CHARS]


def _attachments(message: Any) -> list[dict[str, Any]]:
    found = []
    for part in message.iter_attachments():
        name = str(part.get_filename() or "attachment")[:200]
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            size = len(part.get_payload(decode=True) or b"")
        except Exception:  # noqa: BLE001
            size = 0
        found.append(
            {
                "filename": name,
                "content_type": part.get_content_type(),
                "size_bytes": size,
                "blocked": extension in BLOCKED_EXTENSIONS,
                # Said plainly: a file-type check, not an antivirus scan.
                "check": "file_type_only",
            }
        )
    return found


async def receive(recipient: str, raw_mime: bytes) -> str:
    """One inbound message. Returns a status word for the webhook's log:
    delivered, activated, quarantined, refused, duplicate, too_large."""
    to_address = parseaddr(recipient or "")[1].strip().lower()
    message_id_fallback = "sha256:" + hashlib.sha256(raw_mime).hexdigest()
    if len(raw_mime) > constants.EMAIL_IDENTITY_MAX_INBOUND_BYTES:
        await _store(
            to_address,
            message_id_fallback,
            None,
            status="refused",
            reason="too_large",
            size=len(raw_mime),
        )
        return "too_large"
    message = BytesParser(policy=policy.default).parsebytes(raw_mime)
    message_id = (
        str(message.get("Message-ID") or "").strip()[:255] or message_id_fallback
    )
    local, _, at_domain = to_address.partition("@")
    identity = None
    if at_domain == domain().lower() and local:
        async with db_client.async_session() as session:
            identity = await session.scalar(
                select(EmailIdentityModel).where(
                    EmailIdentityModel.alias == local,
                    EmailIdentityModel.released_at.is_(None),
                )
            )
    subject = str(message.get("Subject") or "")[:500]
    if identity is not None and identity.state == PROVISIONING and identity.probe_hash:
        token = (
            subject.rsplit(" ", 1)[-1]
            if subject.startswith("Decibyl address check ")
            else ""
        )
        if token and hmac.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), identity.probe_hash
        ):
            return await _activate(identity.id, to_address, message_id)
    if identity is None:
        status, reason = "quarantined", "unknown_recipient"
    elif identity.state == SUSPENDED:
        status, reason = "refused", "suspended"
    elif identity.state in RECEIVING:
        status, reason = "delivered", None
    else:
        status, reason = "quarantined", "not_receiving"
    stored = await _store(
        to_address,
        message_id,
        identity if status == "delivered" else None,
        status=status,
        reason=reason,
        size=len(raw_mime),
        thread_key=_thread_key(message, message_id),
        from_address=parseaddr(str(message.get("From") or ""))[1][:320] or None,
        subject=subject,
        body=_body_text(message) if status == "delivered" else None,
        attachments=_attachments(message) if status == "delivered" else [],
    )
    if not stored:
        return "duplicate"
    if status == "delivered":
        from api.services.identity import notifications

        await notifications.notify(
            identity.user_id,
            topic="mail",
            title="New mail at your Decibyl address",
            body=f"From {parseaddr(str(message.get('From') or ''))[1] or 'someone'}",
            link="/settings/identity",
            dedupe_key=f"mail:{identity.id}:{message_id}"[:128],
        )
    return status


async def _activate(identity_id: int, recipient: str, message_id: str) -> str:
    async with db_client.async_session() as session:
        await session.execute(
            update(EmailIdentityModel)
            .where(
                EmailIdentityModel.id == identity_id,
                EmailIdentityModel.state == PROVISIONING,
            )
            .values(
                state=ACTIVE,
                active_at=_now(),
                probe_hash=None,
                issue_code=None,
                revision=EmailIdentityModel.revision + 1,
                updated_at=_now(),
            )
        )
        await session.commit()
    return "activated"


async def _store(
    recipient: str,
    message_id: str,
    identity: EmailIdentityModel | None,
    *,
    status: str,
    reason: str | None,
    size: int,
    thread_key: str | None = None,
    from_address: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """Write the message once; False when this Message-ID was seen."""
    row = EmailIdentityMessageModel(
        identity_id=identity.id if identity else None,
        user_id=identity.user_id if identity else None,
        status=status,
        recipient=recipient[:320] or "unknown",
        message_id=message_id,
        thread_key=thread_key or message_id,
        from_address=from_address,
        subject=subject,
        body_text=body,
        size_bytes=size,
        attachments=attachments or [],
        reason_code=reason,
    )
    try:
        async with db_client.async_session() as session:
            session.add(row)
            await session.commit()
    except IntegrityError:
        return False
    return True


def payload_to_raw(payload: dict[str, Any]) -> tuple[str, bytes]:
    recipient = str(payload.get("recipient") or "")
    try:
        raw = base64.b64decode(str(payload.get("raw_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("raw_base64 is not base64") from exc
    if not recipient or not raw:
        raise ValueError("recipient and raw_base64 are required")
    return recipient, raw


# --- sending ----------------------------------------------------------------


async def sendable(user_id: int) -> EmailIdentityModel:
    """The person's address, if it may send now. Raises CardError."""
    from api.services.identity.cards import CardError

    identity = await _live(user_id)
    if identity is None or identity.state not in (ACTIVE, DELIVERY_ISSUE):
        raise CardError("Your Decibyl address is not active yet.")
    if not outbound_ready():
        raise CardError(
            "Sending from Decibyl addresses is not set up on this deployment yet."
        )
    return identity


@dataclass
class _Refused(Exception):
    code: str


def _smtp_send(message: EmailMessage) -> None:
    smtp_cls = (
        smtplib.SMTP_SSL
        if constants.SMTP_USE_TLS and constants.SMTP_PORT == 465
        else smtplib.SMTP
    )
    with smtp_cls(constants.SMTP_HOST, constants.SMTP_PORT, timeout=15) as client:
        if constants.SMTP_USE_TLS and constants.SMTP_PORT != 465:
            client.starttls()
        if constants.SMTP_USERNAME and constants.SMTP_PASSWORD:
            client.login(constants.SMTP_USERNAME, constants.SMTP_PASSWORD)
        try:
            client.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            raise _Refused("recipient_refused") from exc
        except smtplib.SMTPSenderRefused as exc:
            raise _Refused("sender_refused") from exc
        except smtplib.SMTPDataError as exc:
            if 500 <= exc.smtp_code < 600:
                raise _Refused(f"smtp_{exc.smtp_code}") from exc
            raise


async def send(
    user_id: int,
    *,
    identity_id: int,
    card_event_id: int,
    to: str,
    subject: str,
    body: str,
) -> str:
    """Send once, from the owner's own address. The send row is written
    before the mail server is called, so a send that breaks midway is still
    on record; a second run for the same card never sends again."""
    from api.services.identity.cards import CardError

    identity = await sendable(user_id)
    if identity.id != identity_id:
        raise CardError("That is not your address.")
    address = address_of(identity.alias)
    message_id = make_msgid(domain=domain())
    try:
        async with db_client.async_session() as session:
            session.add(
                EmailIdentitySendModel(
                    identity_id=identity.id,
                    user_id=user_id,
                    card_event_id=card_event_id,
                    message_id=message_id,
                    to_address=to,
                    state="sending",
                )
            )
            await session.commit()
    except IntegrityError as exc:
        raise CardError("This email was already sent once from this card.") from exc
    message = EmailMessage()
    message["From"] = address
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    try:
        await asyncio.to_thread(_smtp_send, message)
    except _Refused as refused:
        await _send_state(card_event_id, "failed", refused.code)
        raise CardError("The mail server refused it. Nothing was sent.") from refused
    # Anything else (a timeout, a dropped connection) propagates: whether the
    # server took it is not known, and the card says so. The row stays
    # "sending" for reconciliation.
    await _send_state(card_event_id, "accepted", None)
    return f"Sent from {address} to {to}."


async def _send_state(card_event_id: int, state: str, code: str | None) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            update(EmailIdentitySendModel)
            .where(EmailIdentitySendModel.card_event_id == card_event_id)
            .values(state=state, detail_code=code, updated_at=_now())
        )
        await session.commit()


async def send_for_card(card_event_id: int) -> EmailIdentitySendModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(EmailIdentitySendModel).where(
                EmailIdentitySendModel.card_event_id == card_event_id
            )
        )


#: What the provider's events webhook says, and what it does to a send.
_EVENT_STATE = {
    "delivered": "delivered",
    "bounce": "bounced",
    "complaint": "complained",
}


async def provider_events(items: list[dict[str, Any]]) -> int:
    """Delivery, bounce and complaint events for sends. A bounce marks the
    address's delivery as having an issue; a complaint pauses its sending.
    Returns how many applied."""
    applied = 0
    for item in items[:500]:
        kind = str(item.get("type") or "").lower()
        message_id = str(item.get("message_id") or "").strip()[:255]
        state = _EVENT_STATE.get(kind)
        if not state or not message_id:
            continue
        async with db_client.async_session() as session:
            send_row = await session.scalar(
                select(EmailIdentitySendModel).where(
                    EmailIdentitySendModel.message_id == message_id
                )
            )
            if send_row is None:
                continue
            send_row.state = state
            send_row.updated_at = _now()
            try:
                async with session.begin_nested():
                    session.add(
                        DeliveryReceiptModel(
                            provider="identity_email",
                            provider_message_id=message_id,
                            idempotency_key=f"identity_email:{send_row.card_event_id}",
                            status=state,
                        )
                    )
            except IntegrityError:
                pass
            if state in ("bounced", "complained"):
                await session.execute(
                    update(EmailIdentityModel)
                    .where(
                        and_(
                            EmailIdentityModel.id == send_row.identity_id,
                            EmailIdentityModel.state.in_((ACTIVE, DELIVERY_ISSUE)),
                        )
                    )
                    .values(
                        state=SUSPENDED if state == "complained" else DELIVERY_ISSUE,
                        suspended_at=_now() if state == "complained" else None,
                        issue_code=state,
                        updated_at=_now(),
                    )
                )
            await session.commit()
            applied += 1
    return applied

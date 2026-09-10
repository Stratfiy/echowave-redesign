"""One send, three carriers.

Each provider gets a small function rather than a class hierarchy. There are
three of them, they each make one HTTP request, and the differences between them
are exactly the interesting part — a base class would hide the one thing worth
reading.

**Failures are returned, never raised past the caller.** A follow-up message
that fails must not fail the call it followed: the conversation already
happened, the outcome is already recorded, and losing the run's data because a
carrier returned 429 would be a far worse outcome than a missing SMS. The result
carries what went wrong so it can land on the run and be seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from loguru import logger

#: Carriers that can send as well as dial. Twilio and Plivo both bill messaging
#: to the same account as voice, so a customer who can place a call can already
#: send a message. WhatsApp is Twilio's Business API, which is why it shares the
#: credential shape and differs only in the address prefix.
PROVIDERS: tuple[str, ...] = ("twilio", "plivo", "whatsapp", "meta_whatsapp")

#: The platform's own WhatsApp sender, on Meta's Cloud API. Unlike the three
#: carriers above it needs no sender number from the customer — the sender is
#: the business phone number id in the credentials — and outside a 24-hour
#: reply window it may only send a pre-approved template, so a message on it
#: usually carries one. See platform_whatsapp.py.
META_WHATSAPP = "meta_whatsapp"

#: How long to wait on the carrier. Short on purpose: this runs after the call
#: has ended, and a task that hangs for a minute holds a worker slot that other
#: runs' post-call work is queued behind.
TIMEOUT_SECONDS = 15.0

#: The longest single SMS body we will send. Carriers segment past 160 GSM-7
#: characters (70 for Unicode, which includes every Indic script), and each
#: segment bills separately — a 1,000-character Telugu message is fifteen
#: segments and a surprising invoice. Truncating silently would be worse, so a
#: body past this is refused with a message that says why.
MAX_BODY_LENGTH = 1600


class MessagingError(Exception):
    """The message could not be sent."""


class MessagingProviderNotSupported(MessagingError):
    """The telephony provider on this configuration cannot send messages."""


@dataclass(frozen=True)
class SendResult:
    """What happened, in a shape that can be written onto the run.

    ``ok`` is the only field a caller must read. The rest exist so a support
    conversation can start from the record rather than from the carrier's
    dashboard.
    """

    ok: bool
    provider: str
    to: str
    message_id: str | None = None
    error: str | None = None
    status_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "to": self.to,
            "message_id": self.message_id,
            "error": self.error,
            "status_code": self.status_code,
        }


def supported_providers() -> tuple[str, ...]:
    return PROVIDERS


def _normalise_number(value: str) -> str:
    """E.164, or as close as the input allows.

    Carriers reject anything else, and the numbers reaching here come from CSV
    columns and caller ID alike: ``9876543210``, ``+91 98765 43210``,
    ``091-9876543210``. Only formatting is stripped — no country code is
    invented, because guessing +91 for a number that was meant to be +1 sends
    someone else's customer a message about an appointment they do not have.
    """
    cleaned = re.sub(r"[^\d+]", "", (value or "").strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    return cleaned


def _validate(to: str, body: str) -> None:
    number = _normalise_number(to)
    if not number:
        raise MessagingError("No recipient number.")
    if not number.startswith("+"):
        raise MessagingError(
            f"'{to}' has no country code. Carriers need E.164 (+919876543210) — "
            "a bare 10-digit number is ambiguous and will be rejected."
        )
    if not body.strip():
        raise MessagingError("Message body is empty.")
    if len(body) > MAX_BODY_LENGTH:
        raise MessagingError(
            f"Message is {len(body)} characters, over the {MAX_BODY_LENGTH} "
            "limit. Carriers split long messages into separately-billed "
            "segments — Indic scripts at 70 characters each — so this would "
            "cost far more than it looks."
        )


async def _send_twilio(
    client: httpx.AsyncClient,
    credentials: Mapping[str, Any],
    *,
    to: str,
    from_: str,
    body: str,
    whatsapp: bool = False,
) -> SendResult:
    account_sid = credentials.get("account_sid")
    auth_token = credentials.get("auth_token")
    if not account_sid or not auth_token:
        raise MessagingError(
            "This Twilio configuration has no account_sid/auth_token stored."
        )

    # WhatsApp rides the same Messages endpoint; only the addresses change.
    prefix = "whatsapp:" if whatsapp else ""
    response = await client.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        auth=(account_sid, auth_token),
        data={"To": f"{prefix}{to}", "From": f"{prefix}{from_}", "Body": body},
    )
    if response.status_code >= 400:
        return SendResult(
            ok=False,
            provider="whatsapp" if whatsapp else "twilio",
            to=to,
            error=_carrier_error(response),
            status_code=response.status_code,
        )
    return SendResult(
        ok=True,
        provider="whatsapp" if whatsapp else "twilio",
        to=to,
        message_id=(response.json() or {}).get("sid"),
        status_code=response.status_code,
    )


async def _send_plivo(
    client: httpx.AsyncClient,
    credentials: Mapping[str, Any],
    *,
    to: str,
    from_: str,
    body: str,
) -> SendResult:
    auth_id = credentials.get("auth_id") or credentials.get("account_sid")
    auth_token = credentials.get("auth_token")
    if not auth_id or not auth_token:
        raise MessagingError(
            "This Plivo configuration has no auth_id/auth_token stored."
        )

    # Plivo wants the numbers without the leading +.
    response = await client.post(
        f"https://api.plivo.com/v1/Account/{auth_id}/Message/",
        auth=(auth_id, auth_token),
        json={"src": from_.lstrip("+"), "dst": to.lstrip("+"), "text": body},
    )
    if response.status_code >= 400:
        return SendResult(
            ok=False,
            provider="plivo",
            to=to,
            error=_carrier_error(response),
            status_code=response.status_code,
        )
    payload = response.json() or {}
    message_uuid = payload.get("message_uuid")
    return SendResult(
        ok=True,
        provider="plivo",
        to=to,
        message_id=message_uuid[0] if isinstance(message_uuid, list) else message_uuid,
        status_code=response.status_code,
    )


async def _send_meta_whatsapp(
    client: httpx.AsyncClient,
    credentials: Mapping[str, Any],
    *,
    to: str,
    body: str,
    template: Mapping[str, Any] | None = None,
) -> SendResult:
    """One message on Meta's Cloud API, as a template or as free text.

    A template is what a business may send unprompted: the name of an approved
    template, its language, and the values for its numbered placeholders.
    Free text is accepted by Meta only inside the 24 hours after the customer
    last wrote, so a node with no template works for a reply and fails, with
    Meta's own words, for a first contact.
    """
    token = credentials.get("access_token")
    phone_number_id = credentials.get("phone_number_id")
    if not token or not phone_number_id:
        raise MessagingError(
            "The platform WhatsApp sender has no access token or phone number id."
        )
    version = credentials.get("graph_version") or "v21.0"
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
    }
    name = (template or {}).get("name") if template else None
    if name:
        params = [str(v) for v in (template or {}).get("params") or []]
        component: dict[str, Any] = {
            "name": name,
            "language": {"code": (template or {}).get("language") or "en"},
        }
        if params:
            component["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in params],
                }
            ]
        payload.update({"type": "template", "template": component})
    else:
        payload.update({"type": "text", "text": {"body": body}})

    response = await client.post(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    if response.status_code >= 400:
        return SendResult(
            ok=False,
            provider=META_WHATSAPP,
            to=to,
            error=_carrier_error(response),
            status_code=response.status_code,
        )
    messages = (response.json() or {}).get("messages") or []
    return SendResult(
        ok=True,
        provider=META_WHATSAPP,
        to=to,
        message_id=(messages[0] or {}).get("id") if messages else None,
        status_code=response.status_code,
    )


def _carrier_error(response: httpx.Response) -> str:
    """The carrier's own words, which are usually the useful ones.

    Twilio's "The 'To' number is not a valid mobile number" tells an operator
    exactly what to fix; "HTTP 400" tells them to open a support ticket.
    """
    try:
        payload = response.json()
    except Exception:
        return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        # Meta nests it: {"error": {"message": "...", "code": 131047}}.
        nested = payload.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"].strip()[:300]
        for key in ("message", "error", "error_message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return str(payload)[:300]


async def send_message(
    *,
    provider: str,
    credentials: Mapping[str, Any],
    to: str,
    from_: str,
    body: str,
    template: Mapping[str, Any] | None = None,
) -> SendResult:
    """Send one message and report what happened.

    Raises :class:`MessagingError` only for things the caller got wrong — an
    unsupported provider, a missing credential, an unusable number. A carrier
    that refuses the message comes back as ``SendResult(ok=False)``, because
    that is an outcome to record rather than an exception to handle.
    """
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise MessagingProviderNotSupported(
            f"'{provider}' cannot send messages. Supported: {', '.join(PROVIDERS)}."
        )

    _validate(to, body)
    to = _normalise_number(to)
    from_ = _normalise_number(from_)
    if not from_ and provider != META_WHATSAPP:
        raise MessagingError(
            "No sender number. Set one on the node, or a default caller ID on "
            "the telephony configuration."
        )

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            if provider == META_WHATSAPP:
                result = await _send_meta_whatsapp(
                    client, credentials, to=to, body=body, template=template
                )
            elif provider == "plivo":
                result = await _send_plivo(
                    client, credentials, to=to, from_=from_, body=body
                )
            else:
                result = await _send_twilio(
                    client,
                    credentials,
                    to=to,
                    from_=from_,
                    body=body,
                    whatsapp=provider == "whatsapp",
                )
        except httpx.HTTPError as exc:
            # The network, not the carrier. Same shape either way: the caller
            # records it and the call is unaffected.
            logger.warning("Message to {} failed to reach {}: {}", to, provider, exc)
            return SendResult(ok=False, provider=provider, to=to, error=str(exc))

    if result.ok:
        logger.info("Sent {} message to {} ({}).", provider, to, result.message_id)
    else:
        logger.warning("{} refused the message to {}: {}", provider, to, result.error)
    return result

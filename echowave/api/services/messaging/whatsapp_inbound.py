"""A person messages the platform WhatsApp number; Decibyl answers (A3 groundwork).

Until this module the platform number could speak and never hear. Meta now
POSTs every message to ``/api/v1/public/whatsapp/webhook``; this is what
happens to one.

**Whose message is it?** The sender's number is looked up among the
numbers accounts have *verified* (proved by answering a code). A match
routes the message to that account's Decibyl thread -- the same thread as
the Home screen, so what was said on WhatsApp is on the screen and vice
versa. No match: the message is dropped, quietly and with a 200, because a
stranger writing to the platform number is not anybody's customer yet and
Meta must not retry.

**A file is a document.** A document or a photo is downloaded from Meta
(``/{media_id}`` gives a short-lived URL; the bytes need the bearer token),
stored at the same key the knowledge base uses, registered as a document
and processed through the same pipeline -- OCR, extraction, chunks -- so
"read it" (A4) is the pipeline the account already pays for. Decibyl sees
it as a chat attachment, exactly as a file dropped on the Home screen.

**Twice is once.** Message ids dedupe for a day in Redis, the way trigger
events do; a redelivery answers nothing twice.

**The 24-hour window is a fact, not a guess.** Every inbound message
refreshes ``session_open(number)`` for 24 hours. Senders consult it: free
text and files go while it is open; outside it, an approved template or an
honest refusal. That is the rule the A1 sender could only describe.

**Only Meta may POST.** ``X-Hub-Signature-256`` is an HMAC over the raw
body with the app secret; a bad or missing signature is a 403 before the
body is parsed. The one-time GET that registers the webhook answers the
challenge only for the configured verify token.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api import constants
from api.db import db_client

SESSION_TTL_SECONDS = 24 * 60 * 60
DEDUPE_TTL_SECONDS = 24 * 60 * 60
_SESSION_PREFIX = "whatsapp:session:"
_SEEN_PREFIX = "whatsapp:seen:"
MAX_MEDIA_BYTES = 25 * 1024 * 1024

#: Message types this understands. Anything else (audio, sticker, location,
#: reaction, a button reply) is acknowledged and dropped for now.
TEXT = "text"
DOCUMENT = "document"
IMAGE = "image"


@dataclass(frozen=True)
class Inbound:
    message_id: str
    sender: str  # E.164 with +
    sender_name: str
    kind: str
    text: str = ""
    media_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    caption: str | None = None
    phone_number_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The webhook's two halves.


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256: sha256=<hex>`` over the raw body with the app
    secret. No secret configured means nothing can be verified: refuse."""
    secret = (constants.WHATSAPP_APP_SECRET or "").strip()
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    presented = header.strip()
    if presented.startswith("sha256="):
        presented = presented[len("sha256=") :]
    return hmac.compare_digest(expected, presented)


def challenge_for(
    mode: str | None, token: str | None, challenge: str | None
) -> str | None:
    """Meta's registration GET: the challenge back, or nothing."""
    expected = (constants.WHATSAPP_WEBHOOK_VERIFY_TOKEN or "").strip()
    if not expected or mode != "subscribe" or not token:
        return None
    if not hmac.compare_digest(expected, token):
        return None
    return challenge or ""


def parse(payload: Any) -> list[Inbound]:
    """Every message in one webhook POST. Statuses (sent/delivered/read) and
    unknown shapes are skipped; the sender's display name rides along."""
    out: list[Inbound] = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            if not isinstance(value, dict):
                continue
            names = {
                str((c.get("wa_id") or "")): str(
                    ((c.get("profile") or {}).get("name")) or ""
                )
                for c in value.get("contacts") or []
                if isinstance(c, dict)
            }
            phone_number_id = (
                str(((value.get("metadata") or {}).get("phone_number_id")) or "")
                or None
            )
            for message in value.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                kind = str(message.get("type") or "")
                sender_digits = str(message.get("from") or "").lstrip("+")
                if not sender_digits or not message.get("id"):
                    continue
                sender = f"+{sender_digits}"
                base = dict(
                    message_id=str(message["id"]),
                    sender=sender,
                    sender_name=names.get(sender_digits, ""),
                    phone_number_id=phone_number_id,
                    raw=message,
                )
                if kind == TEXT:
                    out.append(
                        Inbound(
                            kind=TEXT,
                            text=str((message.get("text") or {}).get("body") or ""),
                            **base,
                        )
                    )
                elif kind in (DOCUMENT, IMAGE):
                    media = message.get(kind) or {}
                    out.append(
                        Inbound(
                            kind=kind,
                            text=str(media.get("caption") or ""),
                            media_id=str(media.get("id") or "") or None,
                            mime_type=str(media.get("mime_type") or "") or None,
                            filename=str(media.get("filename") or "") or None,
                            caption=str(media.get("caption") or "") or None,
                            **base,
                        )
                    )
                else:
                    logger.info("WhatsApp {} message from {} ignored", kind, sender)
    return out


# ---------------------------------------------------------------------------
# Redis: twice is once, and the window.


async def _redis() -> aioredis.Redis | None:
    try:
        return await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - a missing Redis degrades, never blocks
        logger.warning("WhatsApp inbound: Redis unavailable: {}", exc)
        return None


async def seen_before(message_id: str) -> bool:
    client = await _redis()
    if client is None:
        return False
    try:
        return not await client.set(
            f"{_SEEN_PREFIX}{message_id}", "1", ex=DEDUPE_TTL_SECONDS, nx=True
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("WhatsApp inbound: could not dedupe {}: {}", message_id, exc)
        return False


async def touch_session(number: str) -> None:
    client = await _redis()
    if client is None:
        return
    try:
        await client.set(f"{_SESSION_PREFIX}{number}", "1", ex=SESSION_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WhatsApp inbound: could not note the session for {}: {}", number, exc
        )


async def session_open(number: str) -> bool | None:
    """True/False when Redis knows; None when it cannot say (then a sender
    tries and takes Meta's word for it, as before)."""
    client = await _redis()
    if client is None:
        return None
    try:
        return bool(await client.exists(f"{_SESSION_PREFIX}{number}"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WhatsApp inbound: could not read the session for {}: {}", number, exc
        )
        return None


# ---------------------------------------------------------------------------
# Media.


async def download_media(media_id: str) -> tuple[bytes, str]:
    """The bytes and MIME type of a received file, through Meta's two hops."""
    from api.services.messaging import platform_whatsapp

    creds = platform_whatsapp.credentials()
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    version = creds.get("graph_version") or "v21.0"
    async with httpx.AsyncClient(timeout=30.0) as client:
        meta = await client.get(
            f"https://graph.facebook.com/{version}/{media_id}", headers=headers
        )
        meta.raise_for_status()
        info = meta.json() or {}
        url = info.get("url")
        if not url:
            raise ValueError("Meta gave no URL for the media")
        blob = await client.get(str(url), headers=headers)
        blob.raise_for_status()
    if len(blob.content) > MAX_MEDIA_BYTES:
        raise ValueError("The file is larger than 25 MB")
    return blob.content, str(
        info.get("mime_type")
        or blob.headers.get("content-type")
        or "application/octet-stream"
    )


def _extension(mime_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime_type.split(";")[0].strip().lower(), "")


async def file_as_document(
    *, organization_id: int, user: Any, inbound: Inbound
) -> dict[str, Any] | None:
    """Store a received file as a knowledge-base document and start its
    processing. Returns the chat-attachment dict Decibyl reads, or None when
    the file could not be taken (the message still goes through as text)."""
    from api.services.knowledge_base import upload_keys
    from api.services.storage import storage_fs
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    if not inbound.media_id:
        return None
    try:
        data, mime = await download_media(inbound.media_id)
    except Exception as exc:  # noqa: BLE001 - say so in the thread, keep going
        logger.warning(
            "WhatsApp inbound: could not fetch media {}: {}", inbound.media_id, exc
        )
        return None
    filename = (
        inbound.filename
        or f"WhatsApp {inbound.kind} {inbound.message_id[-8:]}{_extension(mime)}"
    )
    document_uuid = str(uuid.uuid4())
    key = upload_keys.build_document_key(organization_id, document_uuid, filename)
    try:
        if not await storage_fs.acreate_file_from_bytes(key, data):
            raise ValueError("storage refused the file")
        document = await db_client.create_document(
            organization_id=organization_id,
            created_by=user.id,
            filename=filename,
            file_size_bytes=len(data),
            file_hash=hashlib.sha256(data).hexdigest(),
            mime_type=mime,
            custom_metadata={
                "s3_key": key,
                "source": "whatsapp",
                "from": inbound.sender,
            },
            document_uuid=document_uuid,
        )
        await enqueue_job(
            FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
            document.id,
            key,
            organization_id,
            str(getattr(user, "provider_id", "") or ""),
            128,
            "chunked",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("WhatsApp inbound: could not file {}: {}", filename, exc)
        return None
    return {
        "document_uuid": document_uuid,
        "filename": filename,
        "size_bytes": len(data),
    }


# ---------------------------------------------------------------------------
# The whole of one message.


async def _first_user(organization_id: int) -> Any | None:
    users = await db_client.get_organization_users(organization_id)
    return users[0] if users else None


async def handle(inbound: Inbound) -> str:
    """Route one message. Returns a status word for the webhook's log."""
    from api.services.workflow import decibyl

    organization_id = await db_client.find_organization_by_verified_number(
        inbound.sender.lstrip("+")
    )
    if organization_id is None:
        logger.info("WhatsApp message from an unverified number, dropped")
        return "unknown_number"
    if await seen_before(inbound.message_id):
        return "duplicate"
    await touch_session(inbound.sender)
    user = await _first_user(organization_id)
    if user is None:
        return "no_user"

    attachments: list[dict[str, Any]] = []
    text = (inbound.text or "").strip()
    if inbound.kind in (DOCUMENT, IMAGE):
        filed = await file_as_document(
            organization_id=organization_id, user=user, inbound=inbound
        )
        if filed:
            attachments.append(filed)
        elif not text:
            text = f"(sent a {inbound.kind} that could not be received)"
    if not text and not attachments:
        return "empty"
    line = text or "Shared " + ", ".join(a["filename"] for a in attachments)
    await decibyl.ask(
        organization_id=organization_id,
        user_id=user.id,
        text=text,
        attachments=attachments,
        line=line,
        preset=None,
        reply_to={"channel": "whatsapp", "to": inbound.sender},
    )
    return "accepted"


async def reply(*, organization_id: int, to: str, body: str) -> None:
    """Decibyl's answer back on WhatsApp, charged as a platform message.
    Never raises: the answer is already on the thread."""
    from api.services.billing import messaging_charges
    from api.services.messaging import platform_whatsapp
    from api.services.messaging.send import send_message

    body = (body or "").strip()
    if not body or not platform_whatsapp.is_configured():
        return
    try:
        result = await send_message(
            provider=platform_whatsapp.PROVIDER,
            credentials=platform_whatsapp.credentials(),
            to=to,
            from_="",
            body=body[:1600],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("WhatsApp reply to {} failed: {}", to, exc)
        return
    if not result.ok:
        logger.warning("WhatsApp refused the reply to {}: {}", to, result.error)
        return
    try:
        async with db_client.async_session() as session:
            await messaging_charges.debit_message(
                session,
                organization_id=organization_id,
                message_id=result.message_id or f"reply:{to}:{hash(body)}",
                node_name="Decibyl",
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not charge the WhatsApp reply to {}: {}", to, exc)


__all__ = [
    "DOCUMENT",
    "IMAGE",
    "Inbound",
    "TEXT",
    "challenge_for",
    "download_media",
    "file_as_document",
    "handle",
    "parse",
    "reply",
    "seen_before",
    "session_open",
    "touch_session",
    "verify_signature",
]

"""Find a person's document and hand it to them, on their own channel (A2).

"Where is my Aadhaar" ends in a link and then the file, on WhatsApp or by
email. The finding is a Composio call against the workspace's connected
Google Drive; the sending is the platform WhatsApp sender's document
message (``messaging.send.Attachment``) or the email attachment path.

Four rules, and they are the whole of the care required:

**An identity document goes only to its owner's own verified channel.** An
Aadhaar, PAN, passport, driving licence or voter ID is sent to a WhatsApp
number this account has proved it answers, or to the verified email of a
member of this account -- never to a number or address that merely came up
in conversation, never at a third party's request, and never through a bot:
these tools exist on Decibyl's Home thread only, so no bot-to-bot delegation
can reach them.

**An identity document is sent only after a yes.** The tool proposes a card
that names the file and the channel; a person confirms it; the send runs.
Any other document -- a rent agreement, an invoice -- is sent in the turn.

**Numbers are masked.** An Aadhaar or PAN number that reaches the thread
shows its last four characters; the full number is shown only when the
person asks for it in that turn, and the model is told so.

**Every send is a receipt line** on the timeline, with the file, the channel
and the masked destination, so an account can see what left and when.

Composio's Drive tool names and argument shapes are as documented for its
v3.1 toolkit (``GOOGLEDRIVE_FIND_FILE``, ``GOOGLEDRIVE_DOWNLOAD_FILE``); a
change on their side reads as ``status: error`` with their words, never as
a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from api.db import db_client
from api.enums import AgentEventKind
from api.services.workflow import agent_timeline

#: What the person may call the document and what it is.
IDENTITY = "identity"
INSURANCE = "insurance"
PROPERTY = "property"
VEHICLE = "vehicle"
MEDICAL = "medical"
FINANCE = "finance"
EDUCATION = "education"
OTHER = "other"

_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        IDENTITY,
        (
            "aadhaar",
            "aadhar",
            "adhar",
            "pan card",
            "pan ",
            "passport",
            "driving licence",
            "driving license",
            "dl ",
            "voter",
            "epic",
        ),
    ),
    (INSURANCE, ("insurance", "policy", "mediclaim", "lic ")),
    (PROPERTY, ("rent agreement", "lease", "sale deed", "property", "khata", "ec ")),
    (VEHICLE, ("rc ", "registration certificate", "puc", "vehicle", "car ", "bike ")),
    (MEDICAL, ("prescription", "report", "scan", "discharge", "medical")),
    (
        FINANCE,
        ("invoice", "receipt", "bank", "statement", "itr", "form 16", "salary slip"),
    ),
    (EDUCATION, ("marksheet", "mark sheet", "degree", "certificate", "transcript")),
)


def classify(name: str) -> str:
    """The class a name or a phrase belongs to; ``other`` when unsure."""
    text = f" {name.lower()} "
    for kind, needles in _KINDS:
        if any(needle in text for needle in needles):
            return kind
    return OTHER


def is_identity(name: str) -> bool:
    return classify(name) == IDENTITY


_AADHAAR = re.compile(r"\b(\d{4})[ -]?(\d{4})[ -]?(\d{4})\b")
_PAN = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z])\b")


def mask(text: str) -> str:
    """Aadhaar and PAN numbers with only their last four characters showing."""
    text = _AADHAAR.sub(lambda m: f"XXXX XXXX {m.group(3)}", text)
    return _PAN.sub(lambda m: f"XXXXX{m.group(2)[-3:]}{m.group(3)}", text)


def mask_destination(value: str) -> str:
    """``+919876543210`` -> ``+91…3210``; ``meera@x.in`` -> ``m…@x.in``."""
    value = (value or "").strip()
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}…@{domain}"
    digits = value.replace(" ", "")
    return f"{digits[:3]}…{digits[-4:]}" if len(digits) > 7 else digits


@dataclass(frozen=True)
class Found:
    file_id: str
    name: str
    link: str | None
    mime_type: str
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "link": self.link,
            "mime_type": self.mime_type,
            "kind": self.kind,
            "identity": self.kind == IDENTITY,
        }


class DocumentError(Exception):
    """One line the model is told, so it can say so."""


FIND_TOOL = "GOOGLEDRIVE_FIND_FILE"
DOWNLOAD_TOOL = "GOOGLEDRIVE_DOWNLOAD_FILE"
DRIVE = "googledrive"
MAX_RESULTS = 5
MAX_FILE_BYTES = 25 * 1024 * 1024


async def _drive_account(organization_id: int) -> str | None:
    from api.services.integrations.composio import client as composio

    try:
        accounts = await composio.connected_accounts(organization_id)
    except Exception as exc:  # noqa: BLE001 - a missing Drive is an answer
        logger.warning(
            "Could not list Drive accounts for org {}: {}", organization_id, exc
        )
        return None
    for account in accounts:
        if account.get("app") == DRIVE:
            return str(account["connected_account_id"])
    return None


async def _drive(
    organization_id: int, tool: str, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    """One Drive call, billed as a tool call on success."""
    from api.services.billing import events as billing_events
    from api.services.integrations.composio import client as composio

    account = await _drive_account(organization_id)
    if account is None:
        raise DocumentError(
            "Google Drive is not connected here. Connect it from Marketplace → "
            "Connectors and ask again."
        )
    try:
        result = await composio.execute_tool(
            tool_slug=tool,
            arguments=arguments,
            organization_id=organization_id,
            connected_account_id=account,
        )
    except Exception as exc:  # noqa: BLE001 - the thread must keep answering
        logger.error("Drive call {} failed for org {}: {}", tool, organization_id, exc)
        raise DocumentError("Drive did not answer just now.") from exc
    if result.get("status") != "success":
        raise DocumentError(str(result.get("error") or "Drive did not answer."))
    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.tool_call_event(DRIVE),
        ref_id=ref_id,
        note=f"{tool} via googledrive (Decibyl)",
    )
    return result.get("data") or {}


def _files_of(data: Any) -> list[dict[str, Any]]:
    """Composio's find returns ``{"files": [...]}``; be generous about the key."""
    if isinstance(data, dict):
        for key in ("files", "items", "results"):
            if isinstance(data.get(key), list):
                return [f for f in data[key] if isinstance(f, dict)]
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict)]
    return []


async def find(organization_id: int, query: str, *, ref_id: str) -> list[Found]:
    """Files in the person's Drive whose name, then whose text, matches."""
    query = (query or "").strip()[:120]
    if not query:
        raise DocumentError("Say what the document is called or is about.")
    data = await _drive(
        organization_id,
        FIND_TOOL,
        {"name_contains": query, "page_size": MAX_RESULTS},
        ref_id=f"{ref_id}:name",
    )
    files = _files_of(data)
    if not files:
        data = await _drive(
            organization_id,
            FIND_TOOL,
            {"full_text_contains": query, "page_size": MAX_RESULTS},
            ref_id=f"{ref_id}:text",
        )
        files = _files_of(data)
    found: list[Found] = []
    for f in files[:MAX_RESULTS]:
        name = str(f.get("name") or "")
        found.append(
            Found(
                file_id=str(f.get("id") or ""),
                name=name,
                link=f.get("webViewLink") or f.get("web_view_link") or f.get("link"),
                mime_type=str(
                    f.get("mimeType") or f.get("mime_type") or "application/pdf"
                ),
                kind=classify(f"{name} {query}"),
            )
        )
    return found


async def download(
    organization_id: int, file_id: str, *, ref_id: str
) -> tuple[bytes, str, str]:
    """The file's bytes, name and MIME type, through Composio's download."""
    data = await _drive(
        organization_id, DOWNLOAD_TOOL, {"file_id": file_id}, ref_id=ref_id
    )
    blob = data.get("downloaded_content") if isinstance(data, dict) else None
    blob = blob if isinstance(blob, dict) else data
    url = (blob or {}).get("s3url") or (blob or {}).get("url")
    name = str((blob or {}).get("name") or "document")
    mime = str(
        (blob or {}).get("mimetype")
        or (blob or {}).get("mime_type")
        or "application/pdf"
    )
    if not url:
        raise DocumentError("Drive gave no way to fetch that file.")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(str(url))
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DocumentError("Could not fetch the file from Drive.") from exc
    if len(response.content) > MAX_FILE_BYTES:
        raise DocumentError("That file is too large to send from here (25 MB).")
    return response.content, name, mime


# ---------------------------------------------------------------------------
# Whose channel is it?


async def own_channels(organization_id: int) -> tuple[set[str], set[str]]:
    """(verified WhatsApp numbers, verified member emails) of this account."""
    from api.services.compliance.dnd import normalise_number

    numbers: set[str] = set()
    for row in await db_client.list_verified_numbers(organization_id):
        if getattr(row, "status", "") == "verified":
            n = normalise_number(str(getattr(row, "phone_number", "") or ""))
            if n:
                numbers.add(f"+{n}")
    emails: set[str] = set()
    for user in await db_client.get_organization_users(organization_id):
        if getattr(user, "email_verified_at", None) and getattr(user, "email", None):
            emails.add(str(user.email).strip().lower())
    return numbers, emails


async def check_destination(
    organization_id: int, *, channel: str, to: str, identity: bool
) -> str:
    """The normalised destination, or a refusal a person would accept."""
    from api.services.compliance.dnd import normalise_number

    channel = (channel or "").strip().lower()
    if channel not in ("whatsapp", "email"):
        raise DocumentError("Say whether to send it on WhatsApp or by email.")
    to = (to or "").strip()
    if channel == "email":
        if "@" not in to:
            raise DocumentError("That is not an email address.")
        to = to.lower()
    else:
        # normalise_number gives a digits-only key and assumes India for a
        # bare ten digits; a WhatsApp destination must say its country.
        digits = normalise_number(to)
        bare = to.replace(" ", "").replace("-", "")
        if not digits or not (bare.startswith("+") or bare.startswith("00")):
            raise DocumentError("That is not a WhatsApp number with a country code.")
        to = f"+{digits}"
    if identity:
        numbers, emails = await own_channels(organization_id)
        allowed = emails if channel == "email" else numbers
        if to not in allowed:
            raise DocumentError(
                "An identity document goes only to your own verified "
                + ("email" if channel == "email" else "WhatsApp number")
                + ", never to anyone else. Verify it under Settings first."
            )
    return to


# ---------------------------------------------------------------------------
# Sending, and the receipt.


async def deliver(
    organization_id: int,
    *,
    file_id: str,
    name: str,
    channel: str,
    to: str,
    note: str = "",
    ref_id: str,
) -> str:
    """Fetch the file and send it; one receipt line either way. Returns the
    line for the thread. Raises DocumentError with the reason on refusal."""
    identity = is_identity(name)
    to = await check_destination(
        organization_id, channel=channel, to=to, identity=identity
    )
    data, real_name, mime = await download(
        organization_id, file_id, ref_id=f"{ref_id}:dl"
    )
    filename = (
        name if name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")) else real_name
    )
    if channel == "whatsapp":
        from api.services.messaging import platform_whatsapp
        from api.services.messaging.send import Attachment, send_message

        if not platform_whatsapp.is_configured():
            raise DocumentError("WhatsApp sending is not set up on this deployment.")
        result = await send_message(
            provider=platform_whatsapp.PROVIDER,
            credentials=platform_whatsapp.credentials(),
            to=to,
            from_="",
            body=note or "",
            attachment=Attachment(
                data=data, filename=filename, mime_type=mime, caption=note or None
            ),
        )
        if not result.ok:
            raise DocumentError(f"WhatsApp did not take it: {result.error}")
        await _charge_whatsapp(organization_id, message_id=result.message_id or ref_id)
    else:
        from api.services.messaging.email import send_email

        result = await send_email(
            to=to,
            subject=filename,
            body_text=note or f"{filename}, as you asked.",
            attachment_bytes=data,
            attachment_filename=filename,
            attachment_mime_type=mime,
        )
        if not result.ok:
            raise DocumentError(f"Email did not go: {result.error}")
    line = f"Sent {filename} to {mask_destination(to)} on {'WhatsApp' if channel == 'whatsapp' else 'email'}."
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.AGENT_ACTED.value,
        summary=line,
        payload={
            "document": filename,
            "kind": classify(filename),
            "identity": identity,
            "channel": channel,
            "to": mask_destination(to),
            "message_id": getattr(result, "message_id", None),
        },
        in_channel=False,
    )
    return line


async def _charge_whatsapp(organization_id: int, *, message_id: str) -> None:
    """The platform message price, the same one a bot's WhatsApp pays."""
    from api.services.billing import messaging_charges

    try:
        async with db_client.async_session() as session:
            await messaging_charges.debit_message(
                session,
                organization_id=organization_id,
                message_id=message_id,
                node_name="document",
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - the file went; log the miss loudly
        logger.error("Could not charge document send {}: {}", message_id, exc)


# ---------------------------------------------------------------------------
# The tools Decibyl offers.

FIND_TOOL_NAME = "find_document"
SEND_TOOL_NAME = "send_document"


def find_tool_schema() -> dict[str, Any]:
    return {
        "name": FIND_TOOL_NAME,
        "description": (
            "Find a document in the person's Google Drive by what it is called or "
            "is about (their Aadhaar, the rent agreement, last month's invoice). "
            "Returns up to five matches with a link. Runs now; nothing is sent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or words in it."}
            },
            "required": ["query"],
        },
    }


def send_tool_schema() -> dict[str, Any]:
    return {
        "name": SEND_TOOL_NAME,
        "description": (
            "Send a document found with find_document to the person, as a file, "
            "on WhatsApp or by email. An identity document (Aadhaar, PAN, "
            "passport, driving licence, voter ID) proposes a card naming the file "
            "and the destination for the person to confirm, and goes only to "
            "their own verified number or email. Anything else is sent now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "name": {"type": "string", "description": "The file's name."},
                "channel": {"type": "string", "enum": ["whatsapp", "email"]},
                "to": {
                    "type": "string",
                    "description": "Their WhatsApp number with country code, or email.",
                },
                "note": {"type": "string", "description": "One line to go with it."},
            },
            "required": ["file_id", "name", "channel", "to"],
        },
    }


async def find_for_thread(
    organization_id: int, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    try:
        found = await find(
            organization_id, str(arguments.get("query") or ""), ref_id=ref_id
        )
    except DocumentError as exc:
        return {"status": "error", "error": str(exc)}
    if not found:
        return {"status": "success", "files": [], "note": "Nothing in Drive matched."}
    return {"status": "success", "files": [f.as_dict() for f in found]}


async def send_for_thread(
    organization_id: int, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    """An identity document becomes a card; anything else goes now."""
    from api.services.workflow import actions

    name = str(arguments.get("name") or "").strip()
    if is_identity(name):
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.SEND_DOCUMENT,
                "file_id": arguments.get("file_id"),
                "name": name,
                "channel": arguments.get("channel"),
                "to": arguments.get("to"),
                "note": arguments.get("note") or "",
                "why": "Asked in the thread",
            },
            in_channel=False,
        )
    try:
        line = await deliver(
            organization_id,
            file_id=str(arguments.get("file_id") or ""),
            name=name,
            channel=str(arguments.get("channel") or ""),
            to=str(arguments.get("to") or ""),
            note=str(arguments.get("note") or ""),
            ref_id=ref_id,
        )
    except DocumentError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "success", "note": line}


__all__ = [
    "DocumentError",
    "FIND_TOOL_NAME",
    "SEND_TOOL_NAME",
    "Found",
    "check_destination",
    "classify",
    "deliver",
    "download",
    "find",
    "find_for_thread",
    "find_tool_schema",
    "is_identity",
    "mask",
    "mask_destination",
    "own_channels",
    "send_for_thread",
    "send_tool_schema",
]

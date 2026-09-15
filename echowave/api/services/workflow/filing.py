"""File a document: classify it, name it consistently, put it in Drive (A3).

A PDF or a photo arrives on WhatsApp or email. Until now it was stored,
read for its fields (``document_fields``) and left in the knowledge base
under whatever name the phone gave it -- ``IMG_4471.jpg``. This files it
where a person would: in the workspace's Google Drive, under a folder for
what it is, with a name that says what it is, whose it is and when it came.

    Decibyl/Insurance/Insurance - Policy LIC-77 - 2026-09-15.pdf
    Decibyl/Identity/Identity - Aadhaar - Meera Iyer - 2026-09-15.jpg

Three rules.

**Ask once when unclear.** A document whose class cannot be told from its
name or its text is not filed under ``Other`` on a guess. The person is
asked, once, on the thread (and on WhatsApp when that is where it came
from); the answer arrives through the ``file_document`` tool and the file
is filed. Asked once means asked once: the question is marked on the
document and never repeated.

**Never guess the owner of an identity document.** An Aadhaar is filed
under its holder's name, and the holder's name comes from a person: the
confirmed fields (``document_fields.confirm``) or the answer to the
question. A name read off the card by a model is a proposal, and an
Aadhaar filed under the wrong person is the one filing mistake that
matters. Until a person says whose it is, an identity document stays
where it landed and the thread says so.

**A filing is a receipt line.** The thread shows the name, the folder and
the link, so what left for Drive and when is on the timeline like every
other action.

Drive calls go through the same Composio Drive account ``documents``
uses; the tool names and argument shapes are as documented for its v3.1
toolkit and a change on their side reads as ``status: error`` with their
words, never as a crash. Uploading goes through Composio's file API: ask
for an upload slot, put the bytes there, then hand the slot's key to the
upload tool.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from api.constants import COMPOSIO_BASE_URL
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, documents

ROOT_FOLDER = "Decibyl"
FOLDERS = {
    documents.IDENTITY: "Identity",
    documents.INSURANCE: "Insurance",
    documents.PROPERTY: "Property",
    documents.VEHICLE: "Vehicle",
    documents.MEDICAL: "Medical",
    documents.FINANCE: "Finance",
    documents.EDUCATION: "Education",
    documents.OTHER: "Other",
}
KINDS = tuple(k for k in FOLDERS if k != documents.OTHER)

#: On the document's ``custom_metadata``.
FILED_KEY = "filed"
ASKED_KEY = "filing_asked"

FIND_FILE_TOOL = "GOOGLEDRIVE_FIND_FILE"
CREATE_FOLDER_TOOL = "GOOGLEDRIVE_CREATE_FOLDER"
UPLOAD_TOOL = "GOOGLEDRIVE_UPLOAD_FILE"
FOLDER_MIME = "application/vnd.google-apps.folder"

#: How much of the text is read to tell what a document is.
CLASSIFY_CHARS = 2_000
MAX_SUBJECT = 60
MAX_NAME = 120

#: What an identity document is called, from its name or text. Used in the
#: filed name and in the question, so "Whose Aadhaar is this?" rather than
#: "Whose identity document is this?".
_IDENTITY_TYPES = (
    ("Aadhaar", ("aadhaar", "aadhar", "adhar")),
    ("PAN", ("pan card", "pan ", "permanent account number")),
    ("Passport", ("passport",)),
    ("Driving licence", ("driving licence", "driving license", "dl ")),
    ("Voter ID", ("voter", "epic")),
)

_PHONE_NAME = re.compile(
    r"^(whatsapp \w+ \w+|img[_ -]?\d+|image|photo|document|scan)", re.I
)


@dataclass(frozen=True)
class Decision:
    """What the document is, what to call it, and what to ask if anything."""

    kind: str
    subject: str | None
    question: str | None

    @property
    def ready(self) -> bool:
        return self.question is None


def identity_type(name: str, text: str = "") -> str:
    haystack = f" {name.lower()} {text[:CLASSIFY_CHARS].lower()} "
    for label, needles in _IDENTITY_TYPES:
        if any(needle in haystack for needle in needles):
            return label
    return "Identity document"


def _stem(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "").strip()
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def _clean_subject(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"[\\/:*?\"<>|]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:MAX_SUBJECT] or None


def _extension(filename: str, mime_type: str | None) -> str:
    match = re.search(r"(\.[A-Za-z0-9]{1,5})$", filename or "")
    if match:
        return match.group(1).lower()
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get((mime_type or "").split(";")[0].strip().lower(), "")


def filing_name(
    kind: str,
    subject: str | None,
    when: datetime,
    *,
    filename: str,
    mime_type: str | None,
) -> str:
    """``Insurance - Policy LIC-77 - 2026-09-15.pdf``: the class, the
    subject when there is one, the date it came, the original extension."""
    parts = [FOLDERS.get(kind, FOLDERS[documents.OTHER])]
    cleaned = _clean_subject(subject)
    if cleaned:
        parts.append(cleaned)
    parts.append(when.astimezone(UTC).strftime("%Y-%m-%d"))
    name = " - ".join(parts)
    return name[:MAX_NAME].rstrip(" -") + _extension(filename, mime_type)


def decide(
    *,
    filename: str,
    text: str,
    kind_hint: str | None = None,
    proposed: dict[str, Any] | None = None,
    confirmed: dict[str, Any] | None = None,
) -> Decision:
    """What to file it as, or what to ask.

    The class comes from the name, then the text, then the hint the reader
    left. An identity document's subject is its confirmed holder and
    nothing else. Any other document's subject is its policy or document
    number when one was read, else the name it came with, unless the phone
    named it.
    """
    kind = documents.classify(filename)
    if kind == documents.OTHER and text:
        kind = documents.classify(text[:CLASSIFY_CHARS])
    if kind == documents.OTHER and kind_hint in FOLDERS:
        kind = kind_hint
    if kind == documents.OTHER:
        return Decision(
            kind=documents.OTHER,
            subject=None,
            question=(
                f"I could not tell what {filename} is. What should I file it "
                f"as: {', '.join(KINDS)}? Say the kind and, if it helps, whose "
                "or what it is."
            ),
        )

    if kind == documents.IDENTITY:
        what = identity_type(filename, text)
        holder = _clean_subject((confirmed or {}).get("holder_name"))
        if not holder:
            return Decision(
                kind=kind,
                subject=None,
                question=(
                    f"Whose {what} is this ({filename})? I file identity "
                    "documents under the owner's name and never guess it."
                ),
            )
        return Decision(kind=kind, subject=f"{what} - {holder}", question=None)

    fields = {**(proposed or {}), **(confirmed or {})}
    subject = None
    if fields.get("policy_number"):
        subject = f"Policy {fields['policy_number']}"
    elif fields.get("document_number") and kind != documents.FINANCE:
        subject = f"No {fields['document_number']}"
    if subject is None:
        stem = _stem(filename)
        if stem and not _PHONE_NAME.match(stem):
            subject = stem
    return Decision(kind=kind, subject=_clean_subject(subject), question=None)


# ---------------------------------------------------------------------------
# Drive


class FilingError(Exception):
    """One line the thread can show."""


async def _drive(
    organization_id: int, tool: str, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    try:
        return await documents._drive(organization_id, tool, arguments, ref_id=ref_id)
    except documents.DocumentError as exc:
        raise FilingError(str(exc)) from exc


async def _find_folder(
    organization_id: int, name: str, parent_id: str | None, *, ref_id: str
) -> str | None:
    arguments: dict[str, Any] = {
        "name_exact": name,
        "mime_type": FOLDER_MIME,
        "page_size": 5,
    }
    if parent_id:
        arguments["parent"] = parent_id
    data = await _drive(organization_id, FIND_FILE_TOOL, arguments, ref_id=ref_id)
    for f in documents._files_of(data):
        if str(f.get("name") or "") == name and (
            str(f.get("mimeType") or f.get("mime_type") or "") in ("", FOLDER_MIME)
        ):
            return str(f.get("id") or "") or None
    return None


async def _ensure_folder(
    organization_id: int, name: str, parent_id: str | None, *, ref_id: str
) -> str:
    found = await _find_folder(
        organization_id, name, parent_id, ref_id=f"{ref_id}:find"
    )
    if found:
        return found
    arguments: dict[str, Any] = {"name": name}
    if parent_id:
        arguments["parent_id"] = parent_id
    data = await _drive(
        organization_id, CREATE_FOLDER_TOOL, arguments, ref_id=f"{ref_id}:create"
    )
    folder_id = (data.get("id") if isinstance(data, dict) else None) or (
        isinstance(data, dict) and (data.get("folder") or {}).get("id")
    )
    if not folder_id:
        raise FilingError(f"Drive did not give back a folder id for {name}.")
    return str(folder_id)


async def folder_for(
    organization_id: int, kind: str, *, ref_id: str
) -> tuple[str, str]:
    """``(folder id, folder label)`` for the class, under the root, created
    when missing."""
    label = FOLDERS.get(kind, FOLDERS[documents.OTHER])
    root = await _ensure_folder(
        organization_id, ROOT_FOLDER, None, ref_id=f"{ref_id}:root"
    )
    folder = await _ensure_folder(
        organization_id, label, root, ref_id=f"{ref_id}:{kind}"
    )
    return folder, label


async def _upload_slot(
    *, filename: str, mime_type: str, data: bytes, timeout_secs: float = 30.0
) -> dict[str, Any]:
    """Composio's file API: an upload slot for this tool, and the bytes put
    in it. Returns the reference the upload tool takes."""
    from api.services.integrations.composio.client import _headers

    body = {
        "toolkit_slug": documents.DRIVE,
        "tool_slug": UPLOAD_TOOL,
        "filename": filename,
        "mimetype": mime_type,
        "md5": hashlib.md5(data).hexdigest(),  # noqa: S324 - a checksum, not a secret
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.post(
                f"{COMPOSIO_BASE_URL}/api/v3/files/upload/request",
                headers=_headers(),
                json=body,
            )
            response.raise_for_status()
            slot = response.json() or {}
            put_url = slot.get("new_presigned_url") or slot.get("presigned_url")
            if put_url:
                put = await client.put(
                    str(put_url), content=data, headers={"Content-Type": mime_type}
                )
                put.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not stage {} for Drive: {}", filename, exc)
        raise FilingError("Could not hand the file to Drive just now.") from exc
    key = slot.get("key") or slot.get("s3key")
    if not key:
        raise FilingError("Drive's upload slot came back without a key.")
    return {"name": filename, "mimetype": mime_type, "s3key": str(key)}


async def _bytes_of(document: Any) -> bytes:
    from api.services.storage import get_storage

    key = ((getattr(document, "custom_metadata", None) or {}).get("s3_key")) or ""
    if not key:
        raise FilingError("The file's storage key is missing.")
    data = await get_storage().aread_bytes(key, documents.MAX_FILE_BYTES)
    if not data:
        raise FilingError("The file could not be read back from storage.")
    return data


async def upload(
    organization_id: int,
    document: Any,
    *,
    kind: str,
    subject: str | None,
    ref_id: str,
) -> dict[str, Any]:
    """Put the document in its class folder under its filing name. Returns
    what was written to the document: file id, link, folder, name."""
    when = getattr(document, "created_at", None) or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    mime = getattr(document, "mime_type", None) or "application/octet-stream"
    name = filing_name(kind, subject, when, filename=document.filename, mime_type=mime)
    folder_id, label = await folder_for(organization_id, kind, ref_id=ref_id)
    data = await _bytes_of(document)
    staged = await _upload_slot(filename=name, mime_type=mime, data=data)
    result = await _drive(
        organization_id,
        UPLOAD_TOOL,
        {"file_to_upload": staged, "folder_to_upload_to": folder_id},
        ref_id=f"{ref_id}:upload",
    )
    file = result.get("file") if isinstance(result, dict) else None
    file = (
        file if isinstance(file, dict) else (result if isinstance(result, dict) else {})
    )
    return {
        "file_id": str(file.get("id") or ""),
        "link": file.get("webViewLink")
        or file.get("web_view_link")
        or file.get("link"),
        "folder": f"{ROOT_FOLDER}/{label}",
        "name": name,
        "kind": kind,
        "at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# The two moments, and the tool


async def _say(organization_id: int, document: Any, body: str, *, kind: str) -> None:
    """One line on the thread, and on WhatsApp when the file came from there."""
    meta = dict(getattr(document, "custom_metadata", None) or {})
    await agent_timeline.record(
        organization_id=organization_id,
        kind=kind,
        actor=AgentEventActor.AGENT.value,
        summary=body.splitlines()[0][:500],
        payload={
            "body": body,
            "from": "Decibyl",
            "document_uuid": document.document_uuid,
        },
        in_channel=False,
    )
    if meta.get("source") == "whatsapp" and meta.get("from"):
        from api.services.messaging import whatsapp_inbound

        try:
            await whatsapp_inbound.reply(
                organization_id=organization_id, to=str(meta["from"]), body=body
            )
        except Exception as exc:  # noqa: BLE001 - the thread has it
            logger.warning("Could not send the filing line on WhatsApp: {}", exc)


async def file_now(
    organization_id: int, document: Any, *, kind: str, subject: str | None
) -> dict[str, Any]:
    """File it and say so. Never raises: a Drive that will not take it is a
    line on the thread, and the document stays where it landed."""
    ref = f"filing:{organization_id}:{document.document_uuid}"
    try:
        filed = await upload(
            organization_id, document, kind=kind, subject=subject, ref_id=ref
        )
    except FilingError as exc:
        await _say(
            organization_id,
            document,
            f"Could not file {document.filename} in Drive: {exc}",
            kind=AgentEventKind.COULD_NOT.value,
        )
        return {"status": "error", "error": str(exc)}
    await db_client.merge_document_custom_metadata(
        document.id,
        organization_id=organization_id,
        patch={FILED_KEY: filed, "kind": kind},
    )
    line = f"Filed {filed['name']} in Drive › {filed['folder']}"
    if filed.get("link"):
        line += f"\n{filed['link']}"
    await _say(organization_id, document, line, kind=AgentEventKind.AGENT_ACTED.value)
    return {"status": "success", **filed}


async def ask_once(
    organization_id: int, document: Any, question: str
) -> dict[str, Any]:
    meta = dict(getattr(document, "custom_metadata", None) or {})
    if meta.get(ASKED_KEY):
        return {"status": "asked"}
    await db_client.merge_document_custom_metadata(
        document.id,
        organization_id=organization_id,
        patch={ASKED_KEY: datetime.now(UTC).isoformat()},
    )
    ref = f" (ref {document.document_uuid[:8]})"
    await _say(
        organization_id, document, question + ref, kind=AgentEventKind.MESSAGE.value
    )
    return {"status": "asked", "question": question}


def _already_filed(document: Any) -> bool:
    return bool((getattr(document, "custom_metadata", None) or {}).get(FILED_KEY))


async def on_read(organization_id: int, document_id: int) -> dict[str, Any]:
    """After the reader has proposed its fields: file what is clear, ask
    once about what is not."""
    document = await db_client.get_document_by_id(document_id)
    if document is None or int(document.organization_id) != int(organization_id):
        return {"status": "no_document"}
    if _already_filed(document):
        return {"status": "already_filed"}
    meta = dict(document.custom_metadata or {})
    decision = decide(
        filename=document.filename,
        text=getattr(document, "full_text", "") or "",
        kind_hint=meta.get("kind"),
        proposed=meta.get("fields_proposed"),
        confirmed=meta.get("fields_confirmed"),
    )
    if not decision.ready:
        return await ask_once(organization_id, document, decision.question or "")
    return await file_now(
        organization_id, document, kind=decision.kind, subject=decision.subject
    )


async def on_confirmed(
    organization_id: int, document: Any, fields: dict[str, Any]
) -> dict[str, Any]:
    """The person confirmed the fields. An identity document waiting on its
    holder's name can now be filed; anything else was filed on reading."""
    if _already_filed(document):
        return {"status": "already_filed"}
    meta = dict(getattr(document, "custom_metadata", None) or {})
    decision = decide(
        filename=document.filename,
        text=getattr(document, "full_text", "") or "",
        kind_hint=meta.get("kind"),
        proposed=meta.get("fields_proposed"),
        confirmed=fields,
    )
    if decision.kind != documents.IDENTITY:
        # Anything else was filed when it was read, or asked about; a
        # confirmation of its fields changes nothing about where it goes.
        return {"status": "not_identity"}
    if not decision.ready:
        return {"status": "waiting", "question": decision.question}
    return await file_now(
        organization_id, document, kind=decision.kind, subject=decision.subject
    )


TOOL_NAME = "file_document"


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "File a document that arrived on WhatsApp or email into the "
            "workspace's Google Drive, after the person has said what it is "
            "or whose it is. Use it when Decibyl asked about a document "
            "(ref …) and the person answered. Pass the kind, and for an "
            "identity document the owner's name exactly as the person gave "
            "it -- never a name you read off the document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_uuid": {
                    "type": "string",
                    "description": "The document's reference from the thread (first 8 chars will do).",
                },
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "What it is.",
                },
                "subject": {
                    "type": "string",
                    "description": (
                        "Whose or what it is, in the person's words: the owner's "
                        "name for an identity document, the policy or property for "
                        "the rest. Omit if they did not say."
                    ),
                },
            },
            "required": ["document_uuid", "kind"],
        },
    }


async def file_for_thread(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    uuid = str(arguments.get("document_uuid") or "").strip()
    kind = str(arguments.get("kind") or "").strip().lower()
    subject = _clean_subject(arguments.get("subject"))
    if kind not in KINDS:
        return {"status": "error", "error": f"kind must be one of {', '.join(KINDS)}"}
    document = None
    if uuid:
        document = await db_client.get_document_by_uuid(
            uuid, organization_id=organization_id
        )
        if document is None and len(uuid) >= 8:
            document = await db_client.find_document_by_uuid_prefix(
                uuid, organization_id=organization_id
            )
    if document is None:
        return {"status": "error", "error": "No such document here."}
    if _already_filed(document):
        filed = (document.custom_metadata or {}).get(FILED_KEY) or {}
        return {
            "status": "already_filed",
            "name": filed.get("name"),
            "link": filed.get("link"),
        }
    if kind == documents.IDENTITY:
        if not subject:
            return {
                "status": "error",
                "error": (
                    "An identity document is filed under its owner's name; ask "
                    "whose it is and pass their answer as subject."
                ),
            }
        what = identity_type(
            document.filename, getattr(document, "full_text", "") or ""
        )
        subject = f"{what} - {subject}"
    return await file_now(organization_id, document, kind=kind, subject=subject)


__all__ = [
    "ASKED_KEY",
    "FILED_KEY",
    "FOLDERS",
    "KINDS",
    "ROOT_FOLDER",
    "TOOL_NAME",
    "Decision",
    "FilingError",
    "decide",
    "file_for_thread",
    "file_now",
    "filing_name",
    "folder_for",
    "identity_type",
    "on_confirmed",
    "on_read",
    "tool_schema",
    "upload",
]

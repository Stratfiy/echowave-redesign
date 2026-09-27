"""Where a template or a document comes from: a standard format, an upload,
a Google Doc, or an email attachment.

**Standard formats** ship in ``standard/``. **An upload** is a knowledge-base
document of this workspace, found by its uuid with the organization in the
query. **A Google Doc** is exported as Word through the connected Google
Drive (Composio ``GOOGLEDRIVE_DOWNLOAD_FILE`` with a .docx ``mime_type``) and
then filled exactly like an upload; when only Google Docs is connected, or
the export fails, its plain text is read (``GOOGLEDOCS_GET_DOCUMENT_PLAINTEXT``)
and made into a plain Word template, and the caller is told the layout was
not kept. **An email attachment** is fetched through the connected Gmail
(``GMAIL_GET_ATTACHMENT``) and stored as an upload, through the same path a
WhatsApp file takes, so it can be read like one.

An app that is not connected is never a dead end: the caller gets
``needs_app`` and puts the connect card on the thread (connector_offer).
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from api.services.documents import formats, templates

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_BYTES = 25 * 1024 * 1024
DRIVE = "googledrive"
DOCS = "googledocs"
GMAIL = "gmail"
DRIVE_DOWNLOAD = "GOOGLEDRIVE_DOWNLOAD_FILE"
DOCS_PLAINTEXT = "GOOGLEDOCS_GET_DOCUMENT_PLAINTEXT"
GMAIL_ATTACHMENT = "GMAIL_GET_ATTACHMENT"
GMAIL_MESSAGE = "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID"
ONEDRIVE = "one_drive"
ONEDRIVE_BY_SHARING_URL = "ONE_DRIVE_GET_DRIVE_ITEM_BY_SHARING_URL"
ONEDRIVE_DOWNLOAD = "ONE_DRIVE_DOWNLOAD_FILE"

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_GOOGLE_URL = re.compile(r"/(?:document|file)/d/([A-Za-z0-9_-]{20,})")
_GOOGLE_ID = re.compile(r"^[A-Za-z0-9_-]{25,80}$")
#: A OneDrive or SharePoint sharing link, personal or business. Only the host
#: is read here; the link is handed whole to Microsoft to resolve, which is
#: the one party that knows what it points at.
_ONEDRIVE_URL = re.compile(
    r"^https://(?:[a-z0-9-]+\.sharepoint\.com|1drv\.ms|onedrive\.live\.com)/",
    re.IGNORECASE,
)


class SourceError(ValueError):
    """What went wrong, in words for the model. ``needs_app`` names an app
    to offer a connect card for."""

    def __init__(self, message: str, *, needs_app: str | None = None) -> None:
        super().__init__(message)
        self.needs_app = needs_app


@dataclass
class Template:
    data: bytes
    label: str
    #: A caveat the person should hear, e.g. that a Google Doc was read as text.
    note: str | None = None


def google_doc_id(value: str) -> str | None:
    text = (value or "").strip()
    match = _GOOGLE_URL.search(text)
    if match:
        return match.group(1)
    if _GOOGLE_ID.match(text) and not _UUID.match(text):
        return text
    return None


async def resolve_template(
    organization_id: int, template: Any, *, kind: str
) -> Template:
    """The template's bytes. ``template`` is a standard format name (or
    empty for the kind's own), an uploaded document's uuid, a Google Doc
    id or link, or a OneDrive/SharePoint sharing link."""
    text = str(template or "").strip()
    if not text or text.lower() in ("standard", "default"):
        if kind not in formats.STANDARD:
            raise SourceError(
                "There is no standard format for that kind; give a template "
                "(an uploaded Word file's uuid, a Google Doc link or a OneDrive link)."
            )
        text = kind
    if text.lower() in formats.STANDARD:
        name = text.lower()
        return Template(formats.FORMATS[name].template_bytes(), f"standard:{name}")
    if _UUID.match(text):
        data, filename, _ = await uploaded_bytes(organization_id, text)
        if not filename.lower().endswith(".docx"):
            raise SourceError(
                f"{filename} is not a Word (.docx) file. A template must be "
                ".docx with {{field}} placeholders."
            )
        return Template(data, f"upload:{text}")
    doc_id = google_doc_id(text)
    if doc_id:
        return await google_doc_template(organization_id, doc_id)
    link = onedrive_link(text)
    if link:
        return await onedrive_template(organization_id, link)
    raise SourceError(
        f"{text!r} is not a template: use one of {', '.join(formats.STANDARD)}, an "
        "uploaded Word file's uuid, a Google Doc link, or a OneDrive link."
    )


async def uploaded_bytes(
    organization_id: int, document_uuid: str
) -> tuple[bytes, str, str]:
    """(bytes, filename, mime) of this workspace's uploaded document."""
    from api.db import db_client
    from api.services import storage

    document = await db_client.get_document_by_uuid(document_uuid, organization_id)
    if document is None:
        raise SourceError("No uploaded document with that id in this workspace.")
    key = ((document.custom_metadata or {}).get("s3_key")) or ""
    if not key:
        raise SourceError(f"{document.filename} has no stored file to read.")
    data = await storage.storage_fs.aread_bytes(key, MAX_BYTES + 1)
    if not data:
        raise SourceError(
            f"{document.filename} could not be read from storage just now."
        )
    if len(data) > MAX_BYTES:
        raise SourceError(f"{document.filename} is larger than 25 MB.")
    return data, str(document.filename or "document"), str(document.mime_type or "")


# ---------------------------------------------------------------------------
# Composio: Drive, Docs, Gmail


async def _account(organization_id: int, app: str) -> str | None:
    from api.services.integrations.composio import client as composio

    try:
        accounts = await composio.connected_accounts(organization_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable list is "not connected"
        logger.warning(
            "Could not list connected apps for org {}: {}", organization_id, exc
        )
        return None
    for account in accounts:
        if account.get("app") == app:
            return str(account["connected_account_id"])
    return None


async def _run(organization_id: int, account: str, slug: str, arguments: dict) -> Any:
    from api.services.integrations.composio import client as composio

    try:
        result = await composio.execute_tool(
            tool_slug=slug,
            arguments=arguments,
            organization_id=organization_id,
            connected_account_id=account,
        )
    except Exception as exc:
        raise SourceError(f"{slug} did not answer just now.") from exc
    if result.get("status") != "success":
        raise SourceError(str(result.get("error") or f"{slug} did not succeed."))
    return result.get("data")


def _find_file(data: Any, depth: int = 0) -> dict[str, Any] | None:
    """The file object inside a Composio result, wherever it is nested:
    ``{s3url|url, name|file_name, mimetype}`` or inline base64 ``data``."""
    if depth > 5:
        return None
    if isinstance(data, dict):
        url = data.get("s3url") or data.get("s3_url")
        if (
            not url
            and isinstance(data.get("url"), str)
            and data["url"].startswith("http")
        ):
            url = data["url"]
        if url or (isinstance(data.get("data"), str) and len(data["data"]) > 16):
            return data
        for value in data.values():
            found = _find_file(value, depth + 1)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = _find_file(value, depth + 1)
            if found:
                return found
    return None


async def _file_bytes(data: Any, *, what: str) -> tuple[bytes, str, str]:
    found = _find_file(data)
    if found is None:
        raise SourceError(f"{what} came back without a file.")
    name = str(
        found.get("name") or found.get("file_name") or found.get("filename") or "file"
    )
    mime = str(
        found.get("mimetype") or found.get("mime_type") or found.get("mimeType") or ""
    )
    url = found.get("s3url") or found.get("s3_url") or found.get("url")
    if url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(str(url))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"Could not fetch {what}.") from exc
        blob = response.content
    else:
        raw = str(found.get("data"))
        try:
            blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            raise SourceError(
                f"{what} came back in a form that could not be read."
            ) from exc
    if len(blob) > MAX_BYTES:
        raise SourceError(f"{what} is larger than 25 MB.")
    return blob, name, mime


def onedrive_link(value: str) -> str | None:
    text = (value or "").strip()
    return text if _ONEDRIVE_URL.match(text) else None


async def onedrive_template(organization_id: int, link: str) -> Template:
    """A Word file on OneDrive or SharePoint, by its sharing link.

    Two calls on the connected OneDrive account: the link becomes an item
    (id, drive, name), and the item is downloaded. A spreadsheet is refused
    by name -- filling an .xlsx format is its own slice -- so the person
    hears which file it was rather than "not a template"."""
    account = await _account(organization_id, ONEDRIVE)
    if not account:
        raise SourceError(
            "OneDrive is not connected, so the link cannot be read.",
            needs_app=ONEDRIVE,
        )
    item = await _run(
        organization_id, account, ONEDRIVE_BY_SHARING_URL, {"sharing_url": link}
    )
    item = item if isinstance(item, dict) else {}
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        raise SourceError("That OneDrive link did not resolve to a file.")
    name = str(item.get("name") or "")
    if name and not name.lower().endswith(".docx"):
        # Known before the download: say which file, and do not fetch it.
        raise SourceError(
            f"{name} is not a Word (.docx) file. A template must be .docx "
            "with {{field}} placeholders."
        )
    arguments: dict[str, Any] = {"item_id": item_id}
    drive_id = (item.get("parentReference") or {}).get("driveId")
    if drive_id:
        arguments["drive_id"] = str(drive_id)
    if name:
        arguments["file_name"] = name
    data = await _run(organization_id, account, ONEDRIVE_DOWNLOAD, arguments)
    blob, filename, _ = await _file_bytes(data, what="The OneDrive file")
    filename = name or filename
    if not filename.lower().endswith(".docx") or blob[:2] != b"PK":
        raise SourceError(
            f"{filename} is not a Word (.docx) file. A template must be .docx "
            "with {{field}} placeholders."
        )
    templates.inspect(blob)  # proves it opens
    return Template(blob, f"onedrive:{item_id}")


async def google_doc_template(organization_id: int, doc_id: str) -> Template:
    drive = await _account(organization_id, DRIVE)
    export_error: str | None = None
    if drive:
        try:
            data = await _run(
                organization_id,
                drive,
                DRIVE_DOWNLOAD,
                {"fileId": doc_id, "mime_type": DOCX_MIME},
            )
            blob, _, _ = await _file_bytes(data, what="The Google Doc")
            if blob[:2] == b"PK":
                templates.inspect(blob)  # proves it opens
                return Template(blob, f"google:{doc_id}")
            export_error = "the export was not a Word file"
        except (SourceError, templates.TemplateError) as exc:
            export_error = str(exc)
            logger.warning("Google Doc {} export as .docx failed: {}", doc_id, exc)
    docs = await _account(organization_id, DOCS)
    if docs:
        data = await _run(
            organization_id,
            docs,
            DOCS_PLAINTEXT,
            {"document_id": doc_id, "include_tables": True},
        )
        text = _plain_text(data)
        if not text.strip():
            raise SourceError("That Google Doc is empty.")
        return Template(
            templates.docx_from_text(text),
            f"google-text:{doc_id}",
            note=(
                "The Google Doc was read as text"
                + (f" ({export_error})" if export_error else "")
                + ", so the drafted file has its words and fields but not its "
                "layout. Connect Google Drive to keep the layout."
            ),
        )
    if drive:
        raise SourceError(f"That Google Doc could not be exported: {export_error}.")
    raise SourceError(
        "Google Drive is not connected, so the Google Doc cannot be read.",
        needs_app=DRIVE,
    )


def _plain_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("plain_text", "plaintext", "text", "content", "document_text"):
            if isinstance(data.get(key), str):
                return data[key]
        for value in data.values():
            text = _plain_text(value)
            if text:
                return text
    return ""


def _attachments_of(message: Any) -> list[dict[str, Any]]:
    """Every ``{attachmentId, filename}`` in a fetched Gmail message."""
    found: list[dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            attachment_id = node.get("attachmentId") or node.get("attachment_id")
            name = node.get("filename") or node.get("file_name") or node.get("name")
            if attachment_id and name:
                found.append(
                    {"attachment_id": str(attachment_id), "filename": str(name)}
                )
            body = node.get("body")
            if (
                isinstance(body, dict)
                and body.get("attachmentId")
                and node.get("filename")
            ):
                found.append(
                    {
                        "attachment_id": str(body["attachmentId"]),
                        "filename": str(node["filename"]),
                    }
                )
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(message)
    unique: dict[str, dict[str, Any]] = {}
    for item in found:
        unique.setdefault(item["attachment_id"], item)
    return list(unique.values())


async def gmail_attachment(
    organization_id: int,
    *,
    message_id: str,
    attachment_id: str | None,
    filename: str | None,
) -> tuple[bytes, str, str]:
    """(bytes, filename, mime) of one attachment on one Gmail message."""
    account = await _account(organization_id, GMAIL)
    if account is None:
        raise SourceError("Gmail is not connected here.", needs_app=GMAIL)
    if not attachment_id:
        if not filename:
            raise SourceError(
                "Say which attachment: its attachment_id or its filename."
            )
        message = await _run(
            organization_id,
            account,
            GMAIL_MESSAGE,
            {"message_id": message_id, "format": "full", "user_id": "me"},
        )
        wanted = filename.strip().lower()
        options = _attachments_of(message)
        match = next(
            (a for a in options if a["filename"].lower() == wanted), None
        ) or next((a for a in options if wanted in a["filename"].lower()), None)
        if match is None:
            names = ", ".join(a["filename"] for a in options) or "none"
            raise SourceError(
                f"No attachment called {filename!r} on that email (it has: {names})."
            )
        attachment_id, filename = match["attachment_id"], match["filename"]
    data = await _run(
        organization_id,
        account,
        GMAIL_ATTACHMENT,
        {
            "message_id": message_id,
            "attachment_id": attachment_id,
            "file_name": filename or "attachment",
            "user_id": "me",
        },
    )
    blob, name, mime = await _file_bytes(data, what="The attachment")
    return blob, (filename or name or "attachment"), mime


async def store_as_upload(
    organization_id: int,
    *,
    data: bytes,
    filename: str,
    mime: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Store a file as this workspace's uploaded document and start its
    processing: the path a WhatsApp file takes (whatsapp_inbound)."""
    from api.db import db_client
    from api.services import storage
    from api.services.knowledge_base import upload_keys
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    users = await db_client.get_organization_users(organization_id)
    if not users:
        raise SourceError("This workspace has no member to own the file.")
    user = users[0]
    document_uuid = str(uuid.uuid4())
    key = upload_keys.build_document_key(organization_id, document_uuid, filename)
    if not await storage.storage_fs.acreate_file_from_bytes(key, data):
        raise SourceError("The file could not be stored just now.")
    document = await db_client.create_document(
        organization_id=organization_id,
        created_by=user.id,
        filename=filename,
        file_size_bytes=len(data),
        file_hash=hashlib.sha256(data).hexdigest(),
        mime_type=mime or "application/octet-stream",
        custom_metadata={"s3_key": key, **source},
        document_uuid=document_uuid,
    )
    try:
        await enqueue_job(
            FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
            document.id,
            key,
            organization_id,
            str(getattr(user, "provider_id", "") or ""),
            128,
            "chunked",
        )
    except Exception as exc:  # noqa: BLE001 - stored and readable; indexing can wait
        logger.warning("Could not queue processing for {}: {}", filename, exc)
    return {
        "document_uuid": document_uuid,
        "filename": filename,
        "size_bytes": len(data),
    }


__all__ = [
    "SourceError",
    "Template",
    "gmail_attachment",
    "google_doc_id",
    "google_doc_template",
    "resolve_template",
    "store_as_upload",
    "uploaded_bytes",
]

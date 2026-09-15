"""Read a filed document, ask once, remember what was confirmed (A4).

A document that arrived on WhatsApp has been through OCR and extraction by
the time this runs; its text is on the row. This pulls the handful of
fields worth remembering -- the document's number, whose it is, when it was
issued and when it expires, a policy number, an amount -- with the
account's own model, and then does the one thing the confirm-before-believe
rule allows: shows them, in one message, and asks "correct?".

Nothing is believed until the person says so. The proposal sits on the
document (``custom_metadata.fields_proposed``); ``confirm_document`` writes
the confirmed fields as *confirmed* facts about the document (same table
and gate as everything else the business knows), and turns any expiry date
into three reminders -- 60, 30 and 7 days before -- as dated tasks on the
board, which the daily sweep sends on WhatsApp when they fall due.

Reminders are board tasks, not routines, on purpose: a routine belongs to
a bot and runs a bot, and the person on the Everyday plan may have none.
A dated task belongs to the team, shows on the board, and is exactly what
"remind me before it expires" is.

Aadhaar and PAN numbers are shown masked here, as everywhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.workflow import agent_timeline, documents

FIELDS: tuple[str, ...] = (
    "document_number",
    "holder_name",
    "issue_date",
    "expiry_date",
    "policy_number",
    "amount",
)
LABELS = {
    "document_number": "Number",
    "holder_name": "Holder",
    "issue_date": "Issued",
    "expiry_date": "Expires",
    "policy_number": "Policy",
    "amount": "Amount",
}
REMIND_DAYS_BEFORE = (60, 30, 7)
SUBJECT_DOCUMENT = "document"
MAX_TEXT_CHARS = 8_000
PROPOSED_KEY = "fields_proposed"
CONFIRMED_KEY = "fields_confirmed"

SYSTEM = (
    "You read one document and report its key fields as JSON. Fields: "
    "document_number, holder_name, issue_date, expiry_date, policy_number, "
    "amount. Dates as YYYY-MM-DD. Omit a field you cannot read; never guess. "
    'Answer with JSON only: {"document_number": "...", ...}'
)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean(raw: Any) -> dict[str, str]:
    """Only the fields we asked for, only when non-empty, dates well-formed."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in FIELDS:
        value = str(raw.get(key) or "").strip()
        if not value or value.lower() in ("null", "none", "n/a", "unknown"):
            continue
        if key.endswith("_date") and not _DATE.match(value):
            continue
        out[key] = value[:120]
    return out


def lines_for(filename: str, fields: dict[str, str], document_uuid: str = "") -> str:
    """The one message: what was read, masked, and the question. The
    document's reference rides on the last line so a later "yes" on the
    thread can name it."""
    ref = f" (ref {document_uuid[:8]})" if document_uuid else ""
    if not fields:
        return f"I filed {filename}{ref} but could not read any details from it."
    rows = [
        f"{LABELS[k]}: {documents.mask(v)}" for k, v in fields.items() if k in LABELS
    ]
    return (
        f"Here is what I read from {filename}{ref}:\n"
        + "\n".join(rows)
        + "\nCorrect? Say yes, or tell me what to change."
    )


async def extract(organization_id: int, text: str) -> dict[str, str]:
    """The fields, from the account's own model. Empty on any failure."""
    from api.services.agent_builder import client, settings

    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        return {}
    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_model(session)
        conversation = client.Conversation()
        conversation.add_user(text)
        reply = await client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[],
        )
        return clean(parse_llm_json(reply.text or ""))
    except Exception as exc:  # noqa: BLE001 - a document read badly is still filed
        logger.warning("Could not read fields for org {}: {}", organization_id, exc)
        return {}


async def propose(organization_id: int, document_id: int) -> dict[str, Any]:
    """Read the document, park the proposal on it, and ask -- on the thread,
    and on WhatsApp when that is where the document came from."""
    document = await db_client.get_document_by_id(document_id)
    if document is None or int(document.organization_id) != int(organization_id):
        return {"status": "no_document"}
    meta = dict(document.custom_metadata or {})
    fields = await extract(organization_id, getattr(document, "full_text", "") or "")
    await db_client.merge_document_custom_metadata(
        document_id,
        organization_id=organization_id,
        patch={PROPOSED_KEY: fields, "kind": documents.classify(document.filename)},
    )
    body = lines_for(document.filename, fields, document.document_uuid)
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=body.splitlines()[0],
        payload={
            "body": body,
            "from": "Decibyl",
            "document_uuid": document.document_uuid,
        },
        in_channel=False,
    )
    if meta.get("source") == "whatsapp" and meta.get("from"):
        from api.services.messaging import whatsapp_inbound

        await whatsapp_inbound.reply(
            organization_id=organization_id, to=str(meta["from"]), body=body
        )
    return {"status": "proposed", "fields": fields}


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(hour=9, tzinfo=UTC)
    except ValueError:
        return None


async def file_reminders(
    organization_id: int, *, filename: str, expiry: str, document_uuid: str
) -> list[str]:
    """Three dated tasks before the expiry; none for a date already inside
    the window. Returns the due dates filed."""
    when = _parse_date(expiry)
    if when is None:
        return []
    now = datetime.now(UTC)
    filed: list[str] = []
    for days in REMIND_DAYS_BEFORE:
        due = when - timedelta(days=days)
        if due <= now:
            continue
        await db_client.create_task(
            organization_id=organization_id,
            title=f"{filename} expires on {expiry} ({days} days)"[:200],
            brief=f"Renew or replace {filename} before {expiry}.",
            status="todo",
            due_at=due,
            source_run_id=None,
        )
        filed.append(due.date().isoformat())
    return filed


async def confirm(
    organization_id: int, *, document_uuid: str, corrections: dict[str, Any] | None
) -> dict[str, Any]:
    """The person said yes (with corrections, maybe): believe the fields,
    file the reminders, say what changed."""
    document = await db_client.get_document_by_uuid(
        document_uuid, organization_id=organization_id
    )
    if document is None and len(document_uuid) >= 8:
        # The thread shows the first eight characters of the uuid; the model
        # may hand back just those.
        document = await db_client.find_document_by_uuid_prefix(
            document_uuid, organization_id=organization_id
        )
    if document is None:
        return {"status": "error", "error": "No such document here."}
    document_uuid = document.document_uuid
    meta = dict(document.custom_metadata or {})
    fields = dict(meta.get(PROPOSED_KEY) or {})
    fields.update(clean(corrections or {}))
    if not fields:
        return {
            "status": "error",
            "error": "There is nothing read from this document to confirm.",
        }
    await db_client.remember_organisation_facts(
        organization_id=organization_id,
        facts={
            **fields,
            "filename": document.filename,
            "kind": meta.get("kind") or documents.classify(document.filename),
        },
        status="confirmed",
        subject_type=SUBJECT_DOCUMENT,
        subject_key=document_uuid,
    )
    reminders = []
    if fields.get("expiry_date"):
        reminders = await file_reminders(
            organization_id,
            filename=document.filename,
            expiry=fields["expiry_date"],
            document_uuid=document_uuid,
        )
    await db_client.merge_document_custom_metadata(
        document.id, organization_id=organization_id, patch={CONFIRMED_KEY: fields}
    )
    line = f"Remembered {len(fields)} details from {document.filename}"
    if reminders:
        line += f"; reminders on {', '.join(reminders)}"
    # An identity document waits for this moment to be filed: its holder's
    # name is now a person's word, not a model's reading (A3).
    from api.services.workflow import filing

    try:
        await filing.on_confirmed(organization_id, document, fields)
    except Exception as exc:  # noqa: BLE001 - the confirmation stands regardless
        logger.warning(
            "Could not file {} after confirmation: {}", document.filename, exc
        )
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.AGENT_ACTED.value,
        summary=line + ".",
        payload={
            "document_uuid": document_uuid,
            "fields": {k: documents.mask(v) for k, v in fields.items()},
            "reminders": reminders,
        },
        in_channel=False,
    )
    return {
        "status": "success",
        "note": line + ".",
        "fields": fields,
        "reminders": reminders,
    }


# --- The tool Decibyl offers ----------------------------------------------

TOOL_NAME = "confirm_document"


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "The person confirmed the details read from a filed document (or "
            "gave corrections). Believes them as confirmed facts about the "
            "document and files reminders 60, 30 and 7 days before any expiry. "
            "Use only after the person has said yes to the details shown."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_uuid": {"type": "string"},
                "corrections": {
                    "type": "object",
                    "description": 'Only the fields the person changed, e.g. {"expiry_date": "2027-03-31"}.',
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["document_uuid"],
        },
    }


async def confirm_for_thread(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    return await confirm(
        organization_id,
        document_uuid=str(arguments.get("document_uuid") or ""),
        corrections=arguments.get("corrections")
        if isinstance(arguments.get("corrections"), dict)
        else None,
    )


# --- The daily sweep ------------------------------------------------------


async def _already_sent_today(organization_id: int) -> bool:
    """One message a day per account, combined with everything else memory
    says unasked -- the silence rule, held in one place (quiet)."""
    from api.services.knowledge_graph import quiet

    return not await quiet.claim_daily_slot(organization_id)


async def remind_due(now: datetime | None = None) -> int:
    """Send today's due reminders, one message per account, on the thread
    and on the account's verified WhatsApp number. Returns accounts told."""
    now = now or datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = await db_client.tasks_due_between(start, start + timedelta(days=1))
    by_org: dict[int, list[Any]] = {}
    for task in tasks:
        by_org.setdefault(int(task.organization_id), []).append(task)
    told = 0
    for organization_id, rows in by_org.items():
        if await _already_sent_today(organization_id):
            continue
        body = "Due today:\n" + "\n".join(f"- {t.title}" for t in rows[:8])
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=f"{len(rows)} reminder{'s' if len(rows) != 1 else ''} due today",
            payload={"body": body, "from": "Decibyl", "tasks": [t.id for t in rows]},
            in_channel=False,
        )
        numbers, _ = await documents.own_channels(organization_id)
        if numbers:
            from api.services.messaging import whatsapp_inbound

            await whatsapp_inbound.reply(
                organization_id=organization_id, to=sorted(numbers)[0], body=body
            )
        told += 1
    return told


__all__ = [
    "CONFIRMED_KEY",
    "FIELDS",
    "PROPOSED_KEY",
    "REMIND_DAYS_BEFORE",
    "TOOL_NAME",
    "clean",
    "confirm",
    "confirm_for_thread",
    "extract",
    "file_reminders",
    "lines_for",
    "propose",
    "remind_due",
    "tool_schema",
]

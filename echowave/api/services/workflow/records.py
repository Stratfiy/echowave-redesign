"""Decibyl searches the workspace's own records on demand (D-1b).

The context a reply is built from already carries the contacts and the
documents *the question names* -- matched once, before the model speaks.
That answers "what do we know about Ravi" and cannot answer "list every
contact with no follow-up this month", because the model has nothing to
call. This is the call: one read tool over the account's own books --
contacts, uploaded documents, recent calls and filed outcomes -- filtered by
words and by date, capped, and run in the turn. Nothing here leaves the
organisation's rows, and nothing here writes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventKind

TOOL_NAME = "search_records"
KINDS = ("contacts", "documents", "calls", "outcomes")
MAX_ROWS = 25
DEFAULT_DAYS = 30


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Search this workspace's own records: contacts (name or number), "
            "documents (by name), calls (recent runs, by agent or caller) and "
            "outcomes (what calls achieved). Runs now, costs nothing, reads "
            "only this workspace. Use it when the context does not already "
            "carry the rows -- a list, a count, a date range -- and say what "
            "you found in the person's words, not as a dump."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Which book to look in.",
                },
                "query": {
                    "type": "string",
                    "description": "Words to match: a name, a number, a file name, an agent.",
                },
                "days": {
                    "type": "integer",
                    "description": f"For calls and outcomes: how far back. Default {DEFAULT_DAYS}.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"At most this many rows, up to {MAX_ROWS}.",
                },
            },
            "required": ["kind"],
        },
    }


def _terms(query: str) -> list[str]:
    return [t for t in (query or "").replace(",", " ").split() if len(t) >= 2][:6]


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value or MAX_ROWS), MAX_ROWS))
    except (TypeError, ValueError):
        return MAX_ROWS


def _days(value: Any) -> int:
    try:
        return max(1, min(int(value or DEFAULT_DAYS), 365))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


async def _contacts(
    organization_id: int, query: str, limit: int
) -> list[dict[str, Any]]:
    terms = _terms(query)
    if not terms:
        return []
    rows = await db_client.search_contacts_for_organization(
        organization_id, terms, limit=limit
    )
    return [
        {
            "name": r.name or "",
            "phone": r.phone_normalized or r.phone_raw or "",
            **({"email": r.email} if getattr(r, "email", None) else {}),
            "attributes": {
                k: v
                for k, v in (r.attributes or {}).items()
                if isinstance(v, (str, int, float))
            },
        }
        for r in rows
    ]


async def _documents(
    organization_id: int, query: str, limit: int
) -> list[dict[str, Any]]:
    rows = await db_client.get_documents_for_organization(organization_id)
    terms = [t.lower() for t in _terms(query)]
    out = []
    for row in rows:
        name = str(getattr(row, "filename", "") or "")
        if terms and not any(t in name.lower() for t in terms):
            continue
        out.append(
            {
                "name": name,
                "uuid": str(getattr(row, "document_uuid", "") or ""),
                "status": str(getattr(row, "status", "") or ""),
                "uploaded": getattr(row, "created_at", None).isoformat()
                if getattr(row, "created_at", None)
                else None,
            }
        )
        if len(out) >= limit:
            break
    return out


async def _events(
    organization_id: int, query: str, *, kinds: list[str], days: int, limit: int
) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await db_client.agent_events(
        organization_id=organization_id, kinds=kinds, limit=200
    )
    terms = [t.lower() for t in _terms(query)]
    out = []
    for row in rows:
        at = getattr(row, "at", None)
        if at is not None and at < since:
            continue
        summary = str(getattr(row, "summary", "") or "")
        if terms and not any(t in summary.lower() for t in terms):
            continue
        out.append(
            {
                "at": at.isoformat() if at else None,
                "kind": str(getattr(row, "kind", "") or ""),
                "bot_id": getattr(row, "workflow_id", None),
                "run_id": getattr(row, "workflow_run_id", None),
                "summary": summary[:300],
            }
        )
        if len(out) >= limit:
            break
    return out


async def for_thread(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering."""
    kind = str(arguments.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return {"status": "error", "error": f"kind must be one of {', '.join(KINDS)}"}
    query = str(arguments.get("query") or "").strip()[:200]
    limit = _limit(arguments.get("limit"))
    days = _days(arguments.get("days"))
    try:
        if kind == "contacts":
            if not _terms(query):
                return {
                    "status": "error",
                    "error": "Say a name or a number to look for.",
                }
            rows = await _contacts(organization_id, query, limit)
        elif kind == "documents":
            rows = await _documents(organization_id, query, limit)
        elif kind == "calls":
            rows = await _events(
                organization_id,
                query,
                kinds=[
                    AgentEventKind.CALL_ENDED.value,
                    AgentEventKind.CALL_ANSWERED.value,
                ],
                days=days,
                limit=limit,
            )
        else:
            rows = await _events(
                organization_id,
                query,
                kinds=[AgentEventKind.OUTCOME_FILED.value],
                days=days,
                limit=limit,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not search {} for org {}: {}", kind, organization_id, exc)
        return {"status": "error", "error": f"Could not read the {kind} just now."}
    out: dict[str, Any] = {
        "status": "success",
        "kind": kind,
        "rows": rows,
        "count": len(rows),
    }
    if not rows:
        out["note"] = f"No {kind} matched."
    elif len(rows) >= limit:
        out["note"] = (
            f"Showing the first {limit}; narrow the words or the days for the rest."
        )
    return out

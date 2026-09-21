"""Saving what a run found, as contacts (OP-4).

A prospecting run finds a clinic, reads its contact page, and has a name,
an address and a source. Until OP-3 the book could not hold a contact
without a number; now it can, and this is the tool that writes one. It
goes into a list called Prospects, made the first time it is needed, and
a prospect already there is refreshed rather than duplicated, on its
address or its number.

Organising is automatic under the default action policy: research and
organise run; sending waits for a card. So this runs in the turn and
returns what it wrote, and the source URL is kept on every row so a
person can see where a lead came from without asking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.utils.telephony_address import normalize_telephony_address

TOOL_NAME = "save_prospects"
LIST_NAME = "Prospects"
MAX_PER_CALL = 50
DESCRIPTION = (
    "Save people or businesses you found as contacts in the Prospects list, "
    "with where each came from. Runs now. A prospect already saved is "
    "refreshed, not duplicated. Give an email or a phone for each; one with "
    "neither is skipped. Read the list first with search_records so you do "
    "not propose a prospect that was already written to or declined."
)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "prospects": {
                    "type": "array",
                    "description": f"Up to {MAX_PER_CALL}.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "company": {"type": "string"},
                            "title": {"type": "string", "description": "Their role."},
                            "source_url": {
                                "type": "string",
                                "description": "The page this came from.",
                            },
                            "note": {
                                "type": "string",
                                "description": "One line on why they fit.",
                            },
                        },
                    },
                },
                "country": {
                    "type": "string",
                    "description": "Two-letter country for reading local phone numbers, e.g. IN.",
                },
            },
            "required": ["prospects"],
        },
    }


def rows_from(arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """(rows to upsert, skipped). A prospect with neither an address nor a
    usable number is skipped and counted, never silently dropped."""
    country = str(arguments.get("country") or "").strip().upper()[:2] or None
    found_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    skipped = 0
    for item in list(arguments.get("prospects") or [])[:MAX_PER_CALL]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        email = str(item.get("email") or "").strip()[:320]
        if "@" not in email:
            email = ""
        phone_raw = str(item.get("phone") or "").strip()[:64]
        normalized = None
        if phone_raw:
            try:
                address = normalize_telephony_address(phone_raw, country_hint=country)
                if (
                    address.address_type != "sip_extension"
                    or address.canonical.isdigit()
                ):
                    normalized = address.canonical
            except ValueError:
                normalized = None
        if not email and not normalized:
            skipped += 1
            continue
        attributes = {
            key: str(item.get(key) or "").strip()[:300]
            for key in ("company", "title", "source_url", "note")
            if str(item.get(key) or "").strip()
        }
        attributes["found_at"] = found_at
        rows.append(
            {
                "phone_raw": phone_raw if normalized else None,
                "phone_normalized": normalized,
                "email": email or None,
                "name": str(item.get("name") or "").strip()[:255] or None,
                "attributes": attributes,
            }
        )
    return rows, skipped


async def _list_id(organization_id: int) -> int:
    from api.db import db_client

    for row in await db_client.get_contact_lists(organization_id=organization_id):
        if (row.name or "").strip().lower() == LIST_NAME.lower():
            return int(row.id)
    made = await db_client.create_contact_list(
        organization_id=organization_id,
        name=LIST_NAME,
        description="Found by an agent on the web; source on each row.",
    )
    return int(made.id)


async def save(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool call. Never raises."""
    rows, skipped = rows_from(arguments)
    if not rows:
        return {
            "status": "error",
            "error": "Nothing to save: each prospect needs an email or a phone.",
            "skipped": skipped,
        }
    try:
        from api.db import db_client

        list_id = await _list_id(organization_id)
        written, more_skipped = await db_client.upsert_contacts(
            list_id, organization_id=organization_id, rows=rows
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save prospects for org {}: {}", organization_id, exc)
        return {
            "status": "error",
            "error": "The prospects could not be saved just now.",
        }
    return {
        "status": "success",
        "list": LIST_NAME,
        "saved": written,
        "skipped": skipped + more_skipped,
    }


__all__ = ["DESCRIPTION", "LIST_NAME", "TOOL_NAME", "rows_from", "save", "tool_schema"]

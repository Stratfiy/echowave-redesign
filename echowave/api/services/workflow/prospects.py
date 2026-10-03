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

A refresh merges onto what the row already carries rather than replacing
it (``upsert_contacts(merge_attributes=True)``): what a send stamped on a
prospect -- emailed, when, which subject -- survives the run finding the
same clinic again, and a status that means stop survives anything the
model writes. The same tool records how a conversation went (a reply, a
"not interested", a bounce), so the next run reads it off the list.

An address scraped off a page is often not an address: ``logo@2x.png``
from an image name, a Sentry or Wix tracking address from the page's
script, ``you@example.com`` from a form's placeholder. Those are refused
here with the reason, because a cold email to one is a bounce, and bounces
are what get a mailbox throttled.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.utils.telephony_address import normalize_telephony_address

TOOL_NAME = "save_prospects"
LIST_NAME = "Prospects"
MAX_PER_CALL = 50
#: What a run may record about how a prospect answered. ``emailed`` and
#: ``declined`` are not here: those are stamped by the send and by the card,
#: never claimed by the model. A prospect with no status is new.
STATUSES = (
    "replied",
    "interested",
    "meeting_booked",
    "not_interested",
    "unsubscribed",
    "bounced",
)
_TEXT_FIELDS = (
    "company",
    "title",
    "website",
    "source_url",
    "note",
    "hook",
    "reply_note",
)

DESCRIPTION = (
    "Save people or businesses you found as contacts in the Prospects list, "
    "with where each came from, or record how a saved prospect answered. "
    "Runs now. A prospect already saved is refreshed, not duplicated, and "
    "what was recorded on it before is kept. Give an email or a phone for "
    "each; one with neither is skipped, and an address that is plainly not "
    "a person's (an image name, a tracking address, a placeholder) is "
    "refused with the reason. Read the list first with search_records so "
    "you do not propose a prospect that was already written to or declined."
)

_VALID_EMAIL = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}$")
_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "domain.com",
        "email.com",
        "yourdomain.com",
        "yoursite.com",
        "yourcompany.com",
        "company.com",
        "test.com",
    }
)
_TRACKING_DOMAINS = ("sentry.io", "wixpress.com", "sentry-next.wixpress.com")
_FILE_ENDINGS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
_NO_REPLY = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster)\b")


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
                            "website": {
                                "type": "string",
                                "description": "Their own website.",
                            },
                            "note": {
                                "type": "string",
                                "description": "One line on why they fit.",
                            },
                            "hook": {
                                "type": "string",
                                "description": (
                                    "One specific thing read on their own page "
                                    "that the first line of an email can mention."
                                ),
                            },
                            "fit_score": {
                                "type": "integer",
                                "description": (
                                    "1-5: how well they match the ideal customer. "
                                    "5 is every part of it; 3 is worth one email."
                                ),
                            },
                            "status": {
                                "type": "string",
                                "enum": list(STATUSES),
                                "description": (
                                    "Only to record how they answered. Leave "
                                    "out for a new prospect."
                                ),
                            },
                            "reply_note": {
                                "type": "string",
                                "description": "One line on what their reply said.",
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


def junk_reason(email: str) -> str | None:
    """Why ``email`` is not an address a person reads, or None if it is."""
    address = email.strip().lower()
    if not _VALID_EMAIL.match(address):
        return "not a valid address"
    local, _, domain = address.rpartition("@")
    if address.endswith(_FILE_ENDINGS) or domain.endswith(_FILE_ENDINGS):
        return "a file name read off the page, not an address"
    if domain in _PLACEHOLDER_DOMAINS or local in {
        "you",
        "your",
        "yourname",
        "name",
        "email",
    }:
        return "a placeholder from a form, not an address"
    if any(domain == d or domain.endswith("." + d) for d in _TRACKING_DOMAINS):
        return "a tracking address from the page's code"
    if _NO_REPLY.match(local):
        return "a no-reply address nobody reads"
    return None


def rows_from(arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """(rows to upsert, skipped). A prospect with neither an address nor a
    usable number is skipped and counted, never silently dropped."""
    rows, rejected = rows_and_rejects(arguments)
    return rows, len(rejected)


def rows_and_rejects(
    arguments: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """(rows to upsert, what was skipped and why)."""
    country = str(arguments.get("country") or "").strip().upper()[:2] or None
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for item in list(arguments.get("prospects") or [])[:MAX_PER_CALL]:
        if not isinstance(item, dict):
            rejected.append({"who": str(item)[:80], "why": "not a prospect"})
            continue
        who = str(item.get("name") or item.get("company") or "").strip()[:80]
        email = str(item.get("email") or "").strip()[:320]
        email_problem = junk_reason(email) if "@" in email else None
        if "@" not in email or email_problem:
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
            rejected.append(
                {
                    "who": who or str(item.get("email") or "")[:80],
                    "why": email_problem or "no email or phone",
                }
            )
            continue
        attributes: dict[str, Any] = {
            key: str(item.get(key) or "").strip()[:300]
            for key in _TEXT_FIELDS
            if str(item.get(key) or "").strip()
        }
        score = _score(item.get("fit_score"))
        if score is not None:
            attributes["fit_score"] = score
        status = str(item.get("status") or "").strip().lower()
        if status in STATUSES:
            attributes["status"] = status
            attributes["status_at"] = now
        # Kept from the first save by the merge; refreshed as last_seen_at.
        attributes["found_at"] = now
        attributes["last_seen_at"] = now
        rows.append(
            {
                "phone_raw": phone_raw if normalized else None,
                "phone_normalized": normalized,
                "email": email or None,
                "name": str(item.get("name") or "").strip()[:255] or None,
                "attributes": attributes,
            }
        )
    return rows, rejected


def _score(value: Any) -> int | None:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return min(5, max(1, score))


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
    rows, rejected = rows_and_rejects(arguments)
    skipped = len(rejected)
    if not rows:
        out: dict[str, Any] = {
            "status": "error",
            "error": "Nothing to save: each prospect needs an email or a phone.",
            "skipped": skipped,
        }
        if any(r["why"] != "no email or phone" for r in rejected):
            out["rejected"] = rejected[:10]
        return out
    try:
        from api.db import db_client

        list_id = await _list_id(organization_id)
        written, more_skipped = await db_client.upsert_contacts(
            list_id,
            organization_id=organization_id,
            rows=rows,
            merge_attributes=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save prospects for org {}: {}", organization_id, exc)
        return {
            "status": "error",
            "error": "The prospects could not be saved just now.",
        }
    out = {
        "status": "success",
        "list": LIST_NAME,
        "saved": written,
        "skipped": skipped + more_skipped,
    }
    # The reason, so the model can go back to the page for the real
    # address instead of guessing one.
    if any(r["why"] != "no email or phone" for r in rejected):
        out["rejected"] = rejected[:10]
    return out


__all__ = [
    "DESCRIPTION",
    "LIST_NAME",
    "STATUSES",
    "TOOL_NAME",
    "junk_reason",
    "rows_and_rejects",
    "rows_from",
    "save",
    "tool_schema",
]

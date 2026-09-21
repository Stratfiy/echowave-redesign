"""A send is a card (OP-4): one email, one confirmation, on any run.

The Prospecting design says one approval card per email. The gate in
``unattended`` withholds a write on a routine run, which is right for a
bot nobody asked to send; a prospecting bot is asked to send, and what it
needs is not permission but review. So a bot may be set to **approve
sends**: every connected-app write it makes, on a call, in a chat or on
its schedule, becomes a ``run_tool`` card on its thread with the
recipient, the subject, the body and the sources the run read, and
nothing leaves until a person confirms. The model is told it has
proposed and moves on to the next lead.

Reads and staged writes (a draft) are untouched: a draft reaches nobody,
and a read changes nothing.

**Outcome recorded.** When the card is confirmed and the mail goes, the
prospect's contact row is stamped with when and what; when it is
declined, with that. The next run reads the book before it writes, so a
prospect declined once is not proposed again as new.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from loguru import logger

#: The key on ``workflow_configurations``, beside ``routine_writes``.
CONFIG_KEY = "approve_sends"

#: Argument names a mail tool uses for the person written to, across the
#: connectors Composio names. Read in this order; the first present wins.
RECIPIENT_KEYS = (
    "recipient_email",
    "to",
    "to_email",
    "to_recipients",
    "recipients",
    "email",
)
SUBJECT_KEYS = ("subject", "title")

#: How many sources one card names. The rest are counted.
MAX_SOURCES = 5

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)


def wants_approval(configurations: Mapping[str, Any] | None) -> bool:
    """Whether this bot's sends are cards. Only a plain ``true`` counts, for
    the same reason ``routine_writes`` reads that way."""
    if not isinstance(configurations, Mapping):
        return False
    return configurations.get(CONFIG_KEY) is True


def briefing_line() -> str:
    return (
        "Every send or change you make in a connected app becomes a card a "
        "person confirms; you will be told it is proposed. Propose it with "
        "the sources you used and move on to the next item. Do not propose "
        "the same message twice."
    )


def why_line(sources: list[str]) -> str:
    """The card's 'why', derived from what the run read rather than written
    by the model: the evidence is the sources, and a sentence the model
    wrote about them is not evidence."""
    seen: list[str] = []
    for s in sources:
        s = str(s or "").strip()
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return "Proposed by the bot on this run; it read no outside source."
    shown = ", ".join(seen[:MAX_SOURCES])
    more = len(seen) - MAX_SOURCES
    return f"Sources read on this run: {shown}" + (
        f" and {more} more." if more > 0 else "."
    )


async def propose_write(
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    tool: Any,
    arguments: dict[str, Any],
    sources: list[str],
) -> dict[str, Any]:
    """The write, as a card on the bot's thread. Returns what the model is
    told. Never raises: a card that cannot be written is told as such."""
    from api.services.workflow import actions

    try:
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            arguments={
                "action": actions.RUN_TOOL,
                "tool_uuid": getattr(tool, "tool_uuid", ""),
                "arguments": dict(arguments or {}),
                "why": why_line(sources),
            },
            in_channel=True,
        )
    except Exception as exc:  # noqa: BLE001 - the turn must finish
        logger.warning("Could not propose a send as a card: {}", exc)
        return {
            "status": "not_proposed",
            "reason": "The card could not be written; say so and stop.",
        }


def recipient_of(arguments: Mapping[str, Any]) -> str | None:
    """The address a mail tool was given, lower-cased, or None."""
    for key in RECIPIENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            value = value.get("email") or value.get("address")
        if isinstance(value, str):
            found = _EMAIL.search(value)
            if found:
                return found.group(0).lower()
    return None


def subject_of(arguments: Mapping[str, Any]) -> str:
    for key in SUBJECT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


async def _stamp(
    organization_id: int, arguments: Mapping[str, Any], fields: dict[str, Any]
) -> bool:
    """Write ``fields`` onto the contact the mail was addressed to, if the
    book has them. Never raises. False when nobody matched."""
    address = recipient_of(arguments)
    if not address:
        return False
    try:
        from api.db import db_client

        rows = await db_client.search_contacts_for_organization(
            organization_id, [address], limit=5
        )
        row = next(
            (r for r in rows if (r.email_normalized or "").lower() == address), None
        )
        if row is None:
            return False
        await db_client.touch_contact(
            row.id, organization_id=organization_id, attributes=fields
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not stamp contact {}: {}", address, exc)
        return False


async def note_sent(organization_id: int, arguments: Mapping[str, Any]) -> bool:
    """The mail went: record when and what on the prospect."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return await _stamp(
        organization_id,
        arguments,
        {
            "last_emailed_at": now,
            "last_subject": subject_of(arguments),
            "status": "emailed",
        },
    )


async def note_declined(organization_id: int, arguments: Mapping[str, Any]) -> bool:
    """A person declined the card: record it so the next run does not
    propose this prospect again as new."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return await _stamp(
        organization_id,
        arguments,
        {
            "declined_at": now,
            "declined_subject": subject_of(arguments),
            "status": "declined",
        },
    )


__all__ = [
    "CONFIG_KEY",
    "briefing_line",
    "note_declined",
    "note_sent",
    "propose_write",
    "recipient_of",
    "subject_of",
    "wants_approval",
    "why_line",
]

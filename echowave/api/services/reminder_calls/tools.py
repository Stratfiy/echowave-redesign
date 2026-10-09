"""Decibyl's tool for reminder calls: ``remind_me_by_call``.

The model passes what the person said; ``draft.clean`` makes it exact; the
answer is always a card on the thread, never a call scheduled unseen:

* no phone line in this workspace -> nothing is scheduled as a call that
  cannot ring; the model is told to offer an app reminder (push / in-app)
  instead, which works with no number;
* no number confirmed for reminder calls (or a new one given) -> the number
  card, with "I am 18 or over", carrying the reminder: once confirmed, the
  reminder's own card follows on the thread;
* otherwise -> the reminder card.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.services import reminder_calls as rc
from api.services.reminder_calls import ReminderCallError, cards, draft, number

TOOL_NAME = "remind_me_by_call"

RULES = (
    f"- {TOOL_NAME}: when the person asks to be phoned (called, rung) at a "
    "time with a reminder, call it once with their words as `title` "
    "(exactly what to read out), `date` (today, tomorrow, a weekday, next "
    "<weekday> or YYYY-MM-DD) and `time` (HH:MM, 24-hour), or `in_minutes` "
    "for 'in 2 hours'. Pass `recurrence` only if they said it repeats, "
    "`language` only if they named one, `phone_number` only if they gave "
    "one here. It puts a card on the thread for them to confirm; do not "
    "repeat what it says, and never claim a call is set before they "
    "confirm. If it says the workspace cannot call, offer an app reminder "
    "instead.\n"
)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Phone the person who asked, at a time they choose, to read out a "
            "reminder in their own words. Shows a card for them to confirm "
            "first; calls ring only within calling hours."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "What to remind them of, in their own words.",
                },
                "date": {
                    "type": "string",
                    "description": "today, tomorrow, a weekday, next <weekday> or YYYY-MM-DD.",
                },
                "time": {
                    "type": "string",
                    "description": "Local time, HH:MM (24-hour).",
                },
                "in_minutes": {
                    "type": "integer",
                    "description": "For 'in N minutes/hours': minutes from now.",
                },
                "recurrence": {
                    "type": "string",
                    "enum": list(draft.RECURRENCES),
                    "description": "Only if they said it repeats.",
                },
                "weekday": {
                    "type": "string",
                    "description": "For weekly: the day of the week.",
                },
                "language": {
                    "type": "string",
                    "description": "Call language code (en, hi, ta, ...), only if named.",
                },
                "phone_number": {
                    "type": "string",
                    "description": "The number to ring, only if they gave one.",
                },
            },
            "required": ["title"],
        },
    }


async def line_ready(organization_id: int) -> bool:
    from api.db import db_client

    try:
        return bool(
            await db_client.get_default_telephony_configuration(organization_id)
        )
    except Exception:  # noqa: BLE001 - a status read
        return False


async def ask(
    organization_id: int,
    user_id: int,
    arguments: dict[str, Any],
    *,
    thread_id: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Draft, then the right card. Raises ReminderCallError."""
    if not rc.enabled(organization_id):
        raise ReminderCallError("Reminder calls are not switched on here.")
    if not await line_ready(organization_id):
        return {
            "status": "no_line",
            "note": (
                "This workspace has no phone line for calling out, so a "
                "reminder call cannot ring. Offer to set an app reminder "
                "(a notification) instead; it needs no number."
            ),
        }
    cleaned = await draft.clean(organization_id, user_id, arguments, now=now)
    given = arguments.get("phone_number")
    phone = number.clean(str(given)) if given else None
    ready = await number.ready(organization_id, user_id)
    if phone is None:
        found = await number.on_file(organization_id, user_id)
        phone = ready or (found["phone"] if found else None)
    if phone is None:
        return {
            "status": "needs_number",
            "note": (
                "Ask which number to ring. Once they type it, call "
                f"{TOOL_NAME} again with phone_number."
            ),
        }
    if phone != ready:
        event_id = await number.propose(
            organization_id, user_id, phone, thread_id=thread_id, then=cleaned
        )
        return {
            "status": "proposed",
            "event_id": event_id,
            "note": (
                "Proposed the number card. Once they confirm it (with "
                "'I am 18 or over'), the reminder's own card follows. Say so "
                "in one line, then end your reply."
            ),
        }
    told = await cards.propose(
        organization_id, user_id, {**cleaned, "phone": phone}, thread_id=thread_id
    )
    return {
        "status": told.get("status"),
        "event_id": told.get("event_id"),
        "note": (
            "Proposed the reminder card. It rings only once they confirm it. "
            "Say so in one line, then end your reply."
        ),
    }


async def run_tool(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    user_id: int | None,
    thread_id: str | None,
) -> dict[str, Any]:
    if not user_id:
        return {
            "status": "not_done",
            "reason": "Only a signed-in person can be called.",
        }
    try:
        return await ask(organization_id, int(user_id), arguments, thread_id=thread_id)
    except (ReminderCallError, TypeError, ValueError) as exc:
        return {"status": "not_done", "reason": str(exc)}

"""Silence by default (Family B rules).

Three things memory may say without being asked -- the Sunday review (B3),
a connection it noticed (B4), a fact resurfaced before an event (B6) --
and two rules over all of them, held here so no feature can forget one:

- **A per-person off switch.** Each is off until the person turns it on,
  and each can be turned off on its own. Stored per user, not per
  account: a colleague's Sunday is not yours.
- **One message a day, combined.** An account hears from memory at most
  once a day across all three and the document reminders (A4). The slot
  is claimed in Redis before anything is composed, so two jobs on the
  same morning cannot both send.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import UserConfigurationKey

SUNDAY_REVIEW = "sunday_review"
CONNECTIONS = "connections"
SPACED_RECALL = "spaced_recall"
SWITCHES = (SUNDAY_REVIEW, CONNECTIONS, SPACED_RECALL)
LABELS = {
    SUNDAY_REVIEW: "the Sunday review",
    CONNECTIONS: "connections memory notices",
    SPACED_RECALL: "reminders of things you asked about",
}

KEY = UserConfigurationKey.MEMORY_MESSAGES.value
DAILY_PREFIX = "memory:said:"

TOOL_NAME = "memory_messages"


def defaults() -> dict[str, bool]:
    return {switch: False for switch in SWITCHES}


async def switches_for(user_id: int) -> dict[str, bool]:
    stored = await db_client.get_user_configuration_value(user_id, KEY) or {}
    out = defaults()
    for switch in SWITCHES:
        out[switch] = bool(stored.get(switch, False))
    return out


async def set_switch(user_id: int, switch: str, on: bool) -> dict[str, bool]:
    if switch not in SWITCHES:
        raise ValueError(f"no such switch: {switch}")
    current = await switches_for(user_id)
    current[switch] = bool(on)
    await db_client.upsert_user_configuration_value(user_id, KEY, current)
    return current


async def people_opted_in(switch: str) -> dict[int, list[Any]]:
    """{organization_id: [users]} who turned ``switch`` on, one query."""
    out: dict[int, list[Any]] = {}
    for organization_id, user in await db_client.users_with_configuration_flag(
        KEY, switch
    ):
        out.setdefault(organization_id, []).append(user)
    return out


async def claim_daily_slot(
    organization_id: int, *, now: datetime | None = None
) -> bool:
    """True if this account has not heard from memory today and now has.
    Without Redis the answer is True: a missing cap costs one message, a
    cap that fails closed would mean a person who asked hears nothing."""
    import redis.asyncio as aioredis

    from api import constants

    day = (now or datetime.now(UTC)).date().isoformat()
    try:
        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
        return bool(
            await client.set(
                f"{DAILY_PREFIX}{organization_id}:{day}", "1", ex=36 * 3600, nx=True
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Daily memory cap unavailable: {}", exc)
        return True


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Turn one of memory's own messages on or off for the person "
            "asking: sunday_review (one message a week on what memory "
            "learned, promises, dates coming up), connections (a line when "
            "memory notices a pattern), spaced_recall (a fact resurfaced "
            "before a related event). All are off until the person asks. "
            "Runs now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "switch": {"type": "string", "enum": list(SWITCHES)},
                "on": {"type": "boolean"},
            },
            "required": ["switch", "on"],
        },
    }


async def for_thread(
    organization_id: int, author_id: int | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    switch = str(arguments.get("switch") or "")
    if switch not in SWITCHES:
        return {
            "status": "error",
            "error": "Say which: sunday_review, connections or spaced_recall.",
        }
    on = bool(arguments.get("on"))
    user_id = author_id
    if user_id is None:
        # A line that came in on WhatsApp carries no signed-in person. An
        # account with one member is that member; anything else is settled
        # from the screen, where the person is known.
        users = await db_client.get_organization_users(organization_id)
        if len(users) == 1:
            user_id = int(users[0].id)
        else:
            return {
                "status": "error",
                "error": (
                    "I cannot tell which member of this workspace is asking "
                    "from here. Turn it on from Settings, under Memory messages."
                ),
            }
    try:
        switches = await set_switch(int(user_id), switch, on)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not set memory switch for user {}: {}", user_id, exc)
        return {"status": "error", "error": "Could not save that just now."}
    return {
        "status": "success",
        "switches": switches,
        "note": f"{LABELS[switch]} is now {'on' if on else 'off'} for this person.",
    }


__all__ = [
    "CONNECTIONS",
    "SPACED_RECALL",
    "SUNDAY_REVIEW",
    "SWITCHES",
    "TOOL_NAME",
    "claim_daily_slot",
    "for_thread",
    "people_opted_in",
    "set_switch",
    "switches_for",
    "tool_schema",
]

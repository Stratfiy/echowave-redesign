"""The approval cards Settings raises (rule: every send, payment, booking,
deletion or submit asks first, through the controls cards).

Three kinds, each one person's own:

* ``forget_memory`` -- forget one remembered fact. Reversible: forgetting is
  a status, so Undo puts it back, during the undo window and after.
* ``delete_saved_item`` -- delete one saved item. Reversible the same way
  (the row is marked deleted, not removed).
* ``delete_personal_data`` -- erase the person's own data store by store.
  Not reversible, and the card says so in as many words.

Every card goes through ``actions.propose_prepared`` and ``actions.settle``:
the same proposed -> armed -> running -> done life, the same payload-version
binding and the same run-once claim as a card in Chat. Each is recorded on
a thread of its own that nobody lists, settled from the screen that raised
it, and readable only by its owner -- a colleague asking for its id gets a
404, the way a wrong tenant does.
"""

from __future__ import annotations

import secrets
from typing import Any

from api.db import db_client
from api.enums import AgentEventKind
from api.services.workflow import actions

FORGET_MEMORY = "forget_memory"
DELETE_SAVED_ITEM = "delete_saved_item"
DELETE_PERSONAL_DATA = "delete_personal_data"
KINDS = (FORGET_MEMORY, DELETE_SAVED_ITEM, DELETE_PERSONAL_DATA)

THREAD_PREFIX = "set-"


class CardNotFound(LookupError):
    pass


async def propose(
    *,
    organization_id: int,
    owner_user_id: int,
    action: str,
    args: dict[str, Any],
    label: str,
    effect: str,
    reversible: bool,
) -> dict[str, Any]:
    """Raise one card for ``owner_user_id``. Returns its view."""
    if action not in KINDS:
        raise ValueError(action)
    payload = {
        "action": action,
        "args": dict(args),
        "label": label,
        "why": "",
        "effect": effect,
        "reversible": reversible,
        "origin": actions.SETTINGS_ORIGIN,
        "owner_user_id": owner_user_id,
    }
    event_id = await actions.propose_prepared(
        organization_id=organization_id,
        payload=payload,
        thread_id=THREAD_PREFIX + secrets.token_hex(16),
    )
    if event_id is None:
        raise actions.ActionError("Could not ask for your OK just now. Try again.")
    return await get(
        organization_id=organization_id, user_id=owner_user_id, event_id=event_id
    )


async def _event(organization_id: int, user_id: int, event_id: int) -> Any:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.ACTION_PROPOSED.value:
        raise CardNotFound
    payload = dict(event.payload or {})
    if (
        payload.get("origin") != actions.SETTINGS_ORIGIN
        or payload.get("owner_user_id") != user_id
    ):
        raise CardNotFound
    return event


def view(event: Any) -> dict[str, Any]:
    """What the screen draws: the exact act, its state and what it did."""
    payload = dict(event.payload or {})
    return {
        "event_id": int(event.id),
        "organization_id": int(event.organization_id),
        "action": payload.get("action"),
        "label": payload.get("label") or "",
        "effect": payload.get("effect") or "",
        "state": payload.get("state") or actions.PROPOSED,
        "ledger_state": payload.get("ledger_state"),
        "version": payload.get("version"),
        "reversible": bool(payload.get("reversible")),
        "fires_at": payload.get("fires_at"),
        "note": (payload.get("done") or {}).get("note"),
        "error": payload.get("error"),
        "args": {
            key: value
            for key, value in (payload.get("args") or {}).items()
            if key in ("fact_id", "item_id", "request_id", "title", "stores")
        },
    }


async def get(*, organization_id: int, user_id: int, event_id: int) -> dict[str, Any]:
    return view(await _event(organization_id, user_id, event_id))


async def settle(
    *,
    organization_id: int,
    user_id: int,
    event_id: int,
    verb: str,
    version: str | None,
) -> dict[str, Any]:
    """Do it / Don't / Undo, on the card -- only by its owner."""
    await _event(organization_id, user_id, event_id)
    await actions.settle(
        organization_id=organization_id,
        event_id=event_id,
        verb=verb,
        user_id=user_id,
        version=version,
    )
    return await get(
        organization_id=organization_id, user_id=user_id, event_id=event_id
    )


# --- what running a card does (called from actions._execute/_reverse) --------


async def execute(organization_id: int, payload: dict[str, Any]) -> str:
    action = payload.get("action")
    args = payload.get("args") or {}
    owner = payload.get("owner_user_id")
    if not isinstance(owner, int):
        raise actions.ActionError("This card has no owner, so nothing was done.")
    if action == FORGET_MEMORY:
        from api.services.settings import memory

        await memory.forget(
            organization_id=organization_id,
            user_id=owner,
            fact_id=int(args["fact_id"]),
        )
        return "Forgotten. It is no longer used in any answer."
    if action == DELETE_SAVED_ITEM:
        from api.services.settings import saved

        await saved.mark_deleted(
            organization_id=organization_id, user_id=owner, item_id=int(args["item_id"])
        )
        return "Deleted. The conversation it came from is unchanged."
    if action == DELETE_PERSONAL_DATA:
        from api.services.settings import privacy

        return await privacy.run_deletion(
            user_id=owner, request_id=int(args["request_id"])
        )
    raise actions.ActionError("Not something Settings can do.")


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    action = payload.get("action")
    args = payload.get("args") or {}
    owner = payload.get("owner_user_id")
    if not isinstance(owner, int):
        raise actions.ActionError("This cannot be put back.")
    if action == FORGET_MEMORY:
        from api.services.settings import memory

        await memory.restore(
            organization_id=organization_id,
            user_id=owner,
            fact_id=int(args["fact_id"]),
            status=str(args.get("was_status") or "confirmed"),
        )
        return
    if action == DELETE_SAVED_ITEM:
        from api.services.settings import saved

        await saved.restore(
            organization_id=organization_id, user_id=owner, item_id=int(args["item_id"])
        )
        return
    raise actions.ActionError("This cannot be put back.")

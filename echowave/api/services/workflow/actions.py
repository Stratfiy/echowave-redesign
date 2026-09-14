"""A bot proposes to do something; a person confirms; there is time to undo.

Decisions (``decisions.py``) are the bot asking a person to choose. This is
the other direction: the bot, or Decibyl, has worked out what to do and
wants to do it -- turn a paused bot back on, ring back somebody who rang and
got nobody -- and the platform will not let it happen on a model's say-so.
So the proposal is a card, and the card is the whole safety story:

1. **Proposed.** The row is written with what would happen and why. Nothing
   has run. Confirm and Not now are the two things to press.
2. **Armed.** A person pressed Confirm. The action fires after
   ``UNDO_WINDOW_SECONDS``, not at once, so the card shows Undo and a
   mis-press costs nothing. The job that fires is queued with a delay and
   re-reads the row before doing anything: an undo that landed in the
   window turns it into a no-op.
3. **Done.** It ran. A reversible action -- a switch that can be flipped
   back -- keeps "Put it back" on the card; an irreversible one (a call
   placed) shows what happened and offers nothing, because a call cannot be
   un-rung and a button that pretended otherwise would be a lie.
4. **Undone / cancelled / declined.** The end states. Every one of them is
   written into the same row, so the card everyone sees later reads as a
   record: who confirmed, who took it back, what it did.

One catalogue, one tool, the same card, for every bot and for Decibyl. The
catalogue is deliberately short and every entry runs through the platform's
own guarded path (the live switch, the missed-call callback with its DND and
calling-window checks) rather than a bare API: a bot that could dial any
number from a chat would be a bot that skips every guard the campaigns obey.

Same two halves as ``decisions``: ``propose`` is what the model calls,
``settle`` is what a person's press does, ``run`` is the job in between.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

TOOL_NAME = "propose_action"
DESCRIPTION = (
    "Propose to do one of the things you are allowed to do: turn a bot on "
    "or off, or call back a caller the business missed. Nothing happens "
    "until a person on the team confirms on the card, and they can undo it "
    "for a few seconds after. Say in one line why. You will not know the "
    "outcome in this turn; say that you have proposed it and end your reply."
)

#: How long Confirm can be taken back before the action fires. Long enough
#: to notice a mis-press, short enough that "confirm" still means now.
UNDO_WINDOW_SECONDS = 10

TURN_BOT_ON = "turn_bot_on"
TURN_BOT_OFF = "turn_bot_off"
RETURN_MISSED_CALL = "return_missed_call"
ACTIONS = (TURN_BOT_ON, TURN_BOT_OFF, RETURN_MISSED_CALL)

#: The states a proposal moves through. Terminal ones are the last four.
PROPOSED = "proposed"
ARMED = "armed"
DONE = "done"
FAILED = "failed"
UNDONE = "undone"
CANCELLED = "cancelled"
DECLINED = "declined"

MAX_WHY_CHARS = 300


def tool_properties() -> dict[str, Any]:
    return {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "'turn_bot_on' or 'turn_bot_off' for a bot's live switch; "
                "'return_missed_call' to ring back a caller from the missed "
                "calls in your context."
            ),
        },
        "bot": {
            "type": "string",
            "description": (
                "The bot, by name or @handle, for turn_bot_on and turn_bot_off. "
                "Leave empty to mean yourself."
            ),
        },
        "missed_call_id": {
            "type": "integer",
            "description": "The id of the missed call, from your context, for return_missed_call.",
        },
        "why": {
            "type": "string",
            "description": "One line on why this should happen.",
        },
    }


def tool_schema() -> dict[str, Any]:
    """The tool in the shape the workspace assistant's model client takes."""
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": tool_properties(),
            "required": ["action", "why"],
        },
    }


class ActionError(ValueError):
    """The press cannot be honoured; the message says why, for the screen."""


# --- resolving what was proposed -------------------------------------------


def _match_bot(wanted: str, roster: list[Any]) -> Optional[Any]:
    """Case-insensitive match on handle or name. Never a guess: an ambiguous
    name resolves to nothing, and the model is told to be specific."""
    key = wanted.strip().lstrip("@").casefold()
    if not key:
        return None
    hits = [
        w
        for w in roster
        if (getattr(w, "handle", None) or "").casefold() == key
        or (getattr(w, "name", None) or "").casefold() == key
    ]
    return hits[0] if len(hits) == 1 else None


async def resolve(
    *,
    organization_id: int,
    workflow_id: Optional[int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Turn the model's arguments into a payload a card can render and a
    job can run without looking anything up again.

    Raises ActionError with a line the model is told, so it can say so.
    """
    action = str(arguments.get("action") or "").strip()
    if action not in ACTIONS:
        raise ActionError("That is not something I can propose.")
    why = str(arguments.get("why") or "").strip()[:MAX_WHY_CHARS]

    if action in (TURN_BOT_ON, TURN_BOT_OFF):
        wanted = str(arguments.get("bot") or "").strip()
        if wanted:
            roster = await db_client.get_all_workflows_for_listing(
                organization_id=organization_id
            )
            bot = _match_bot(wanted, list(roster))
            if bot is None:
                raise ActionError(f"No bot called {wanted!r} here; use its exact name.")
            target_id, target_name = bot.id, bot.name
        elif workflow_id is not None:
            bot = await db_client.get_workflow(
                workflow_id, organization_id=organization_id
            )
            if bot is None:
                raise ActionError("This bot no longer exists.")
            target_id, target_name = bot.id, bot.name
        else:
            raise ActionError("Say which bot.")
        on = action == TURN_BOT_ON
        return {
            "action": action,
            "args": {"workflow_id": target_id, "bot_name": target_name, "is_live": on},
            "label": f"Turn {target_name} {'on' if on else 'off'}",
            "why": why,
            "reversible": True,
            "state": PROPOSED,
        }

    try:
        missed_call_id = int(arguments.get("missed_call_id"))
    except (TypeError, ValueError):
        raise ActionError("Say which missed call, by its id.") from None
    row = await db_client.get_missed_call(
        missed_call_id, organization_id=organization_id
    )
    if row is None:
        raise ActionError("That missed call is not in this account.")
    if row.outcome == "called_back":
        raise ActionError("That caller has already been called back.")
    return {
        "action": action,
        "args": {"missed_call_id": row.id, "caller": row.caller},
        "label": f"Call {row.caller} back",
        "why": why,
        "reversible": False,
        "state": PROPOSED,
    }


# --- the model's half -------------------------------------------------------


async def propose(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    workflow_run_id: Optional[int],
    arguments: dict[str, Any],
    in_channel: bool = True,
) -> dict[str, Any]:
    """Record the proposal. Returns what the model is told.

    ``in_channel=False`` is Decibyl: the row has no bot and no channel, which
    is what puts it on Decibyl's own thread (see decibyl.thread_filter).
    """
    if not organization_id:
        return {"status": "not_proposed", "reason": "no organisation"}
    try:
        payload = await resolve(
            organization_id=organization_id,
            workflow_id=workflow_id,
            arguments=arguments,
        )
    except ActionError as exc:
        return {"status": "not_proposed", "reason": str(exc)}
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        summary=payload["label"],
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload=payload,
        in_channel=in_channel,
    )
    return {
        "status": "proposed",
        "note": (
            f"Proposed: {payload['label']}. A person has to confirm it on the "
            "card before it happens. Say that you have proposed it, then end "
            "your reply."
        ),
    }


# --- the person's half ------------------------------------------------------


def _stamp(user_id: int) -> dict[str, Any]:
    return {"by": user_id, "at": datetime.now(UTC).isoformat()}


async def _write(event: Any, payload: dict[str, Any]) -> None:
    if not await db_client.set_agent_event_payload(
        event.id, organization_id=event.organization_id, payload=payload
    ):
        raise ActionError("That proposal is not here any more.")


async def _proposal(organization_id: int, event_id: int) -> Any:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.ACTION_PROPOSED.value:
        raise ActionError("That proposal is not here.")
    return event


async def settle(
    *,
    organization_id: int,
    event_id: int,
    verb: str,
    user_id: int,
) -> dict[str, Any]:
    """Confirm, decline or undo, on the card. Returns the updated payload.

    ``confirm`` arms the action and queues it to fire after the undo window.
    ``decline`` ends a proposal nothing was done about.
    ``undo`` cancels an armed action, or puts back a done one that can be.
    """
    event = await _proposal(organization_id, event_id)
    payload = dict(event.payload or {})
    state = payload.get("state") or PROPOSED

    if verb == "confirm":
        if state != PROPOSED:
            raise ActionError("Already settled.")
        fires_at = datetime.now(UTC) + timedelta(seconds=UNDO_WINDOW_SECONDS)
        payload["state"] = ARMED
        payload["confirmed"] = _stamp(user_id)
        payload["fires_at"] = fires_at.isoformat()
        await _write(event, payload)
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        try:
            await enqueue_job(
                FunctionNames.RUN_PROPOSED_ACTION,
                event_id,
                organization_id,
                _defer_by=timedelta(seconds=UNDO_WINDOW_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001 - said on the card, not lost
            logger.error(
                "Action {} confirmed but could not be queued: {}", event_id, exc
            )
            payload["state"] = FAILED
            payload["error"] = "Could not be started. Try again."
            await _write(event, payload)
            raise ActionError(payload["error"]) from exc
        return payload

    if verb == "decline":
        if state != PROPOSED:
            raise ActionError("Already settled.")
        payload["state"] = DECLINED
        payload["declined"] = _stamp(user_id)
        await _write(event, payload)
        return payload

    if verb == "undo":
        if state == ARMED:
            payload["state"] = CANCELLED
            payload["cancelled"] = _stamp(user_id)
            await _write(event, payload)
            return payload
        if state == DONE and payload.get("reversible"):
            await _reverse(organization_id, payload)
            payload["state"] = UNDONE
            payload["undone"] = _stamp(user_id)
            await _write(event, payload)
            await _say(event, f"Put back: {payload['label'].lower()} undone.")
            return payload
        if state == DONE:
            raise ActionError("This cannot be put back.")
        raise ActionError("Nothing to undo.")

    raise ActionError("Not a thing to do with a proposal.")


# --- the job ---------------------------------------------------------------


async def _say(event: Any, line: str) -> None:
    """A line under the card, from whoever proposed it, so the thread reads
    what happened without opening the card."""
    from api.services.workflow import decibyl

    payload: dict[str, Any] = {"body": line, "action_event_id": event.id}
    if event.workflow_id is None:
        payload["from"] = decibyl.NAME
    await agent_timeline.record(
        organization_id=event.organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=line,
        workflow_id=event.workflow_id,
        folder_id=event.folder_id,
        payload=payload,
        in_channel=event.folder_id is not None,
    )


async def _execute(organization_id: int, payload: dict[str, Any]) -> str:
    """Do it. Returns one line on what happened; raises on refusal."""
    action = payload.get("action")
    args = payload.get("args") or {}
    if action in (TURN_BOT_ON, TURN_BOT_OFF):
        try:
            await db_client.set_workflow_live(
                workflow_id=int(args["workflow_id"]),
                is_live=bool(args["is_live"]),
                organization_id=organization_id,
            )
        except ValueError as exc:
            raise ActionError("That bot no longer exists.") from exc
        return f"{args.get('bot_name', 'The bot')} is now {'on' if args['is_live'] else 'off'}."
    if action == RETURN_MISSED_CALL:
        from api.services.telephony import missed_call

        row = await db_client.get_missed_call(
            int(args["missed_call_id"]), organization_id=organization_id
        )
        if row is None:
            raise ActionError("That missed call is no longer here.")
        if row.outcome == "called_back":
            raise ActionError("That caller has already been called back.")
        try:
            run_id = await missed_call.place_callback(row)
        except missed_call.CallbackRefused as exc:
            await db_client.resolve_missed_call(
                row.id,
                organization_id=organization_id,
                outcome="refused",
                refusal_reason=str(exc),
            )
            raise ActionError(str(exc)) from exc
        await db_client.resolve_missed_call(
            row.id,
            organization_id=organization_id,
            outcome="called_back",
            workflow_run_id=run_id,
        )
        return f"Calling {row.caller} back now."
    raise ActionError("That is not something that can be done.")


async def _reverse(organization_id: int, payload: dict[str, Any]) -> None:
    """The inverse, for the actions that have one."""
    action = payload.get("action")
    args = payload.get("args") or {}
    if action in (TURN_BOT_ON, TURN_BOT_OFF):
        try:
            await db_client.set_workflow_live(
                workflow_id=int(args["workflow_id"]),
                is_live=not bool(args["is_live"]),
                organization_id=organization_id,
            )
        except ValueError as exc:
            raise ActionError("That bot no longer exists.") from exc
        return
    raise ActionError("This cannot be put back.")


async def run(event_id: int, organization_id: int) -> None:
    """Fire a confirmed action once its window has passed.

    Re-reads the row first: an undo inside the window has already moved it
    off ``armed`` and there is nothing to do. Never raises -- a refusal is a
    state on the card and a line under it, which is what a person reads.
    """
    try:
        event = await _proposal(organization_id, event_id)
    except ActionError:
        logger.warning("Action {} vanished before it could run", event_id)
        return
    payload = dict(event.payload or {})
    if payload.get("state") != ARMED:
        return
    try:
        note = await _execute(organization_id, payload)
    except ActionError as exc:
        payload["state"] = FAILED
        payload["error"] = str(exc)
        await _write(event, payload)
        await _say(event, f"Could not: {payload['label'].lower()}. {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - the card must say something
        logger.error("Action {} failed: {}", event_id, exc)
        payload["state"] = FAILED
        payload["error"] = "Something went wrong on our side."
        await _write(event, payload)
        await _say(
            event, f"Could not: {payload['label'].lower()}. This is us, not you."
        )
        return
    payload["state"] = DONE
    payload["done"] = {"at": datetime.now(UTC).isoformat(), "note": note}
    await _write(event, payload)
    await _say(event, note)

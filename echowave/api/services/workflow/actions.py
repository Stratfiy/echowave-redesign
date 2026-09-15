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
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

TOOL_NAME = "propose_action"
DESCRIPTION = (
    "Propose to do one of the things you are allowed to do: turn a bot on "
    "or off, call back a caller the business missed, forget one of the "
    "facts in your memory when a person asks you to, or create a new bot "
    "from a template with the answers it needs. Nothing happens "
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
FORGET_FACT = "forget_fact"
#: Build a colleague (KAN-140). The card shows the template, the name and
#: the answers; Confirm creates the bot with a handle; the done card
#: offers Hear it and Try it. Missing answers are reported to the model so
#: it asks, never guessed and never a failed card.
CREATE_BOT = "create_bot"
ACTIONS = (TURN_BOT_ON, TURN_BOT_OFF, RETURN_MISSED_CALL, FORGET_FACT, CREATE_BOT)

#: Run one connected-app tool -- a Composio write such as sending a mail
#: or creating a CRM record -- that Decibyl was asked to do from the thread.
#: Not offered in propose_action's enum: Decibyl reaches an app through the
#: app's own function (see connected_tools), and a write is turned into this
#: card rather than run. Accepted by resolve/_execute like any other kind.
RUN_TOOL = "run_tool"
#: Hand somebody their own identity document (A2). Proposed by Decibyl's
#: send_document tool, never offered in propose_action's enum: the card is
#: the "are you sure" the rule requires, and a person confirms it.
SEND_DOCUMENT = "send_document"
INTERNAL_ACTIONS = (RUN_TOOL, SEND_DOCUMENT)

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
                "calls in your context; 'forget_fact' to drop one remembered "
                "fact, named by its key; 'create_bot' to build a new bot from "
                "one of the templates in your context."
            ),
        },
        "template_id": {
            "type": "string",
            "description": "For create_bot: the template id from your context.",
        },
        "name": {
            "type": "string",
            "description": (
                "For create_bot: what to call the bot, in the person's words, "
                "e.g. 'Narayani Dental front desk'."
            ),
        },
        "variables": {
            "type": "object",
            "description": (
                "For create_bot: the answers the template needs, keyed by the "
                "names listed under 'Needs' in your context. Ask for any you "
                "do not have before proposing."
            ),
            "additionalProperties": {"type": "string"},
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
        "fact": {
            "type": "string",
            "description": "The key of the fact to forget, as it appears in your memory, for forget_fact.",
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


def _match_bot(wanted: str, roster: list[Any]) -> Any | None:
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
    workflow_id: int | None,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Turn the model's arguments into a payload a card can render and a
    job can run without looking anything up again.

    Raises ActionError with a line the model is told, so it can say so.
    """
    action = str(arguments.get("action") or "").strip()
    if action not in ACTIONS and action not in INTERNAL_ACTIONS:
        raise ActionError("That is not something I can propose.")
    why = str(arguments.get("why") or "").strip()[:MAX_WHY_CHARS]

    if action == RUN_TOOL:
        from api.services.workflow import connected_tools

        tool_uuid = str(arguments.get("tool_uuid") or "").strip()
        if not tool_uuid:
            raise ActionError("Say which tool.")
        tool = await db_client.get_tool_by_uuid(
            tool_uuid, organization_id=organization_id
        )
        if tool is None or not connected_tools.is_connected(tool):
            raise ActionError("That app is not connected here.")
        app = connected_tools.toolkit_of(tool)
        return {
            "action": action,
            "args": {
                "tool_uuid": tool.tool_uuid,
                "tool_name": tool.name,
                "toolkit": app,
                "arguments": dict(arguments.get("arguments") or {}),
            },
            "label": f"{tool.name} via {app}" if app else str(tool.name),
            "why": why,
            # An email sent or a record created in somebody else's system
            # has no inverse we can promise; the undo window before it fires
            # is the safety, not a button after.
            "reversible": False,
            "state": PROPOSED,
        }

    if action == SEND_DOCUMENT:
        from api.services.workflow import documents

        name = str(arguments.get("name") or "").strip()[:200]
        file_id = str(arguments.get("file_id") or "").strip()
        if not name or not file_id:
            raise ActionError("Say which file.")
        channel = str(arguments.get("channel") or "").strip().lower()
        try:
            to = await documents.check_destination(
                organization_id,
                channel=channel,
                to=str(arguments.get("to") or ""),
                identity=documents.is_identity(name),
            )
        except documents.DocumentError as exc:
            raise ActionError(str(exc)) from exc
        return {
            "action": action,
            "args": {
                "file_id": file_id,
                "name": name,
                "channel": channel,
                "to": to,
                "note": str(arguments.get("note") or "")[:300],
            },
            "label": f"Send {name} to {documents.mask_destination(to)} on "
            f"{'WhatsApp' if channel == 'whatsapp' else 'email'}",
            "why": why,
            # A file that has left cannot be unsent.
            "reversible": False,
            "state": PROPOSED,
        }

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

    if action == CREATE_BOT:
        from api.services.agent_builder.assemble import AssemblyError, assemble
        from api.services.agent_templates import get_template

        template_id = str(arguments.get("template_id") or "").strip()
        name = str(arguments.get("name") or "").strip()[:120]
        raw = arguments.get("variables") or {}
        variables = {
            str(k): str(v) for k, v in dict(raw).items() if str(v or "").strip()
        }
        template = get_template(template_id) if template_id else None
        if template is None:
            raise ActionError(
                "Say which template, by its id from the templates in your context."
            )
        if not name:
            raise ActionError("Say what to call the bot.")
        try:
            built = assemble(template, name=name, variables=variables)
        except AssemblyError as exc:
            raise ActionError(str(exc)) from exc
        if built.missing_variables:
            asks = ", ".join(
                f"{key} ({template.template_variables.get(key, key)})"
                for key in built.missing_variables
            )
            # The ask-and-fill rule: the model is told what to ask, and no
            # card is written until it has the answers.
            raise ActionError(
                f"Ask the person for these first, then propose again: {asks}."
            )
        return {
            "action": action,
            "args": {
                "template_id": template.id,
                "template_name": template.name,
                "name": built.name,
                "variables": variables,
            },
            "label": f"Create {built.name}",
            "why": why or f"From the {template.name} template",
            "reversible": False,
            "state": PROPOSED,
        }

    if action == FORGET_FACT:
        wanted = str(arguments.get("fact") or "").strip()
        if not wanted:
            raise ActionError("Say which fact, by its key.")
        rows = await db_client.organisation_memory(
            organization_id=organization_id, workflow_id=workflow_id
        )
        live = [r for r in rows if r.status != "rejected"]
        key = wanted.casefold()
        hits = [r for r in live if (r.key or "").casefold() == key]
        if not hits:
            hits = [r for r in live if key in (r.key or "").casefold()]
        if len(hits) != 1:
            raise ActionError(
                f"No single fact called {wanted!r} in memory; use its exact key."
            )
        row = hits[0]
        return {
            "action": action,
            "args": {
                "fact_id": row.id,
                "key": row.key,
                "value": row.value,
                "was_status": row.status,
            },
            "label": f"Forget {row.key}",
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
    organization_id: int | None,
    workflow_id: int | None,
    workflow_run_id: int | None,
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
        except Exception as exc:
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
    if action == FORGET_FACT:
        # Forgetting is a status, not a delete: the row stays, out of every
        # prompt and every screen, so it can be put back and so it stays
        # dismissed if a call teaches it again.
        if not await db_client.set_organisation_fact_status(
            organization_id=organization_id,
            fact_id=int(args["fact_id"]),
            status="rejected",
        ):
            raise ActionError("That fact is no longer in memory.")
        return f"Forgotten: {args.get('key', 'that')}."
    if action == CREATE_BOT:
        from api.services.agent_builder import tools as builder_tools

        user_id = int(((payload.get("confirmed") or {}).get("by")) or 0)
        if not user_id:
            raise ActionError("Nobody confirmed this, so nobody owns the bot.")
        result = await builder_tools._create_agent(
            organization_id=organization_id,
            user_id=user_id,
            template_id=str(args.get("template_id") or ""),
            name=str(args.get("name") or ""),
            variables=dict(args.get("variables") or {}),
        )
        if not result.get("created"):
            raise ActionError(str(result.get("error") or "Could not build it."))
        workflow_id = int(result["workflow_id"])
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        handle = getattr(workflow, "handle", None) if workflow else None
        # Kept on the card, so Hear it and Try it know where to go.
        payload.setdefault("result", {}).update(
            {
                "workflow_id": workflow_id,
                "handle": handle,
                "open_url": result.get("open_url"),
            }
        )
        who = f"@{handle}" if handle else result.get("name", "the bot")
        return (
            f"Created {result.get('name', 'the bot')} ({who}). Hear it or try it "
            "from this card; it needs a number before it can take real calls."
        )
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
    if action == SEND_DOCUMENT:
        from api.services.workflow import documents

        confirmed_at = str((payload.get("confirmed") or {}).get("at") or "")
        try:
            return await documents.deliver(
                organization_id,
                file_id=str(args.get("file_id") or ""),
                name=str(args.get("name") or ""),
                channel=str(args.get("channel") or ""),
                to=str(args.get("to") or ""),
                note=str(args.get("note") or ""),
                ref_id=f"send_document:{organization_id}:{args.get('file_id')}:{confirmed_at}",
            )
        except documents.DocumentError as exc:
            raise ActionError(str(exc)) from exc
    if action == RUN_TOOL:
        from api.services.workflow import connected_tools

        tool = await db_client.get_tool_by_uuid(
            str(args.get("tool_uuid") or ""), organization_id=organization_id
        )
        if tool is None or not connected_tools.is_connected(tool):
            raise ActionError("That app is no longer connected.")
        confirmed_at = str((payload.get("confirmed") or {}).get("at") or "")
        result = await connected_tools.execute(
            organization_id=organization_id,
            tool=tool,
            arguments=dict(args.get("arguments") or {}),
            ref_id=f"run_tool:{organization_id}:{tool.tool_uuid}:{confirmed_at}",
        )
        if result.get("status") != "success":
            raise ActionError(str(result.get("error") or "It did not go through."))
        payload.setdefault("result", {})["data"] = result.get("data")
        return f"Done: {args.get('tool_name', 'the tool')}."
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
    if action == FORGET_FACT:
        if not await db_client.set_organisation_fact_status(
            organization_id=organization_id,
            fact_id=int(args["fact_id"]),
            status=str(args.get("was_status") or "confirmed"),
        ):
            raise ActionError("That fact is no longer in memory.")
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

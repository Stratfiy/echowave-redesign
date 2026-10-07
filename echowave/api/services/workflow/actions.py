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

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, approvals, audit_log

TOOL_NAME = "propose_action"
DESCRIPTION = (
    "Propose to do one of the things you are allowed to do: turn an agent on "
    "or off, call back a caller the business missed, forget one of the "
    "facts in your memory when a person asks you to, or create a new agent "
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
#: Everything: the graph partition, every remembered fact and gap. A delete,
#: How far back a duplicate is looked for. Recent rows only: a proposal from
#: last week that nobody settled must not silently swallow today's ask.
DUPLICATE_WINDOW = 20

#: not a status, and not reversible -- the one action here whose card says
#: so in as many words. A business asking to be forgotten is not asking to
#: be hidden (B8).
FORGET_EVERYTHING = "forget_everything"
#: Build a colleague (KAN-140). The card shows the template, the name and
#: the answers; Confirm creates the bot with a handle; the done card
#: offers Hear it and Try it. Missing answers are reported to the model so
#: it asks, never guessed and never a failed card.
CREATE_BOT = "create_bot"
ACTIONS = (
    TURN_BOT_ON,
    TURN_BOT_OFF,
    RETURN_MISSED_CALL,
    FORGET_FACT,
    FORGET_EVERYTHING,
    CREATE_BOT,
)

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
#: Build a bot from a written specification rather than from a template
#: (see services/workflow/bot_from_brief.py). Internal for the same reason
#: as the two above: Decibyl reaches it through its own tool, which
#: validates the spec, and propose_action's enum stays the short list of
#: things a bot may propose about the account.
BUILD_FROM_SPEC = "build_from_spec"
#: Install skills from a repository the person named (D-1b; see
#: services/skills/imports.py). Internal for the same reason: Decibyl reaches
#: it through its own tool, which has already read the repository.
INSTALL_FROM_REPOSITORY = "install_from_repository"
#: Schedule one of Decibyl's own routines (KAN-156; see
#: services/workflow/routines.py). Internal like the three above: Decibyl
#: reaches it through schedule_routine, which has already read the words.
SCHEDULE_ROUTINE = "schedule_routine"
#: Track a commitment the person approves (launch stream `agents`,
#: Follow-up; see services/helpers/commitments.py). Internal: reached
#: through track_commitment, which has already validated the fields.
TRACK_COMMITMENT = "track_commitment"
#: Create a tracker the person described (the describe-it builder; see
#: services/helpers/trackers.py). Internal for the same reason.
CREATE_TRACKER = "create_tracker"
INTERNAL_ACTIONS = (
    RUN_TOOL,
    SEND_DOCUMENT,
    BUILD_FROM_SPEC,
    INSTALL_FROM_REPOSITORY,
    SCHEDULE_ROUTINE,
    TRACK_COMMITMENT,
    CREATE_TRACKER,
)

#: The states a proposal moves through. Terminal ones are the last four.
PROPOSED = "proposed"
ARMED = "armed"
#: Claimed by the job that fires it, before it acts: a retried or duplicate
#: job finds the card already running and does nothing, so a send happens
#: once. A card left here means the job died mid-action; whether the action
#: happened is unknown, and it is never fired again blind.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
UNDONE = "undone"
CANCELLED = "cancelled"
DECLINED = "declined"
#: The job claimed the card and then lost track of it -- it died, or the
#: outside service never said whether the send happened. Never fired again
#: blind (task ledger, handoff 10): a person checks, and the card says so.
OUTCOME_UNKNOWN = "outcome_unknown"
#: A card still ``running`` this long after it was due to fire is taken to
#: have lost its job.
STALE_RUNNING_MINUTES = 10

MAX_WHY_CHARS = 300


# --- approval binding (task ledger) ------------------------------------------


def _ledger_on(organization_id: int | None) -> bool:
    from api.services.workflow import task_ledger

    return task_ledger.enabled(organization_id)


def payload_version(payload: dict[str, Any]) -> str:
    """The version of what a card would do: a hash of its action and its
    arguments, nothing else. The label and the reason are the model's words
    and do not change the act; the arguments are the act."""
    canonical = json.dumps(
        {"action": payload.get("action"), "args": payload.get("args") or {}},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _is_outbound(payload: dict[str, Any]) -> bool:
    """Whether running the card reaches somebody: a send, not a read or a
    draft. What the operational quota counts as an outbound message."""
    action = payload.get("action")
    if action == SEND_DOCUMENT:
        return True
    if action == RUN_TOOL:
        if "reaches_people" in payload:
            return bool(payload["reaches_people"])
        effect = str(payload.get("effect") or "")
        return not effect.startswith(("Reads from", "Writes a draft"))
    return False


async def _emit(name: str, event: Any, payload: dict[str, Any], user_id: Any) -> None:
    """One catalogue event about a card. Never raises (events.emit)."""
    from api.services import events

    if name.startswith("task_"):
        # A card's run is a task: the catalogue names its kind task_kind.
        properties: dict[str, Any] = {
            "task_kind": payload.get("action"),
            "status": payload.get("state"),
        }
        if name == "task_completed":
            properties["has_evidence"] = bool(payload.get("done"))
    else:
        properties = {
            "action_kind": payload.get("action"),
            "status": payload.get("state"),
        }
    await events.emit(
        name,
        user_id=user_id if isinstance(user_id, int) and user_id > 0 else None,
        organization_id=event.organization_id,
        task_id=f"card:{event.id}",
        configuration_version=payload.get("version"),
        properties=properties,
    )


def tool_properties() -> dict[str, Any]:
    return {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "'turn_bot_on' or 'turn_bot_off' for an agent's live switch; "
                "'return_missed_call' to ring back a caller from the missed "
                "calls in your context; 'forget_fact' to drop one remembered "
                "fact, named by its key; 'forget_everything' to delete all "
                "the business has taught its workers -- only when the person "
                "asks for everything to be forgotten or deleted, never for "
                "one fact; 'create_bot' to build a new agent from one of the "
                "templates in your context."
            ),
        },
        "template_id": {
            "type": "string",
            "description": "For create_bot: the template id from your context.",
        },
        "name": {
            "type": "string",
            "description": (
                "For create_bot: what to call the agent, in the person's words, "
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
                "The agent, by name or @handle, for turn_bot_on and turn_bot_off. "
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


def effect_of(tool: Any) -> str:
    """One line saying what running this tool does, in the operator's terms.

    Derived, never written by the model, and carried on the card so it sits
    beside ``why`` at the moment somebody decides. The model may still say
    whatever it likes in the chat; what it cannot do is make that the only
    account of the act. Asked for a draft, Decibyl proposed
    GMAIL_REPLY_TO_THREAD and wrote "it'll sit as a draft, nothing sends
    automatically" -- of a send, on a card already marked irreversible.

    Three cases, because there are three:a read changes nothing, a staged
    write leaves something for a person to send, and everything else reaches
    somebody the moment it fires.
    """
    from api.services.workflow import connected_tools, unattended

    app = connected_tools.toolkit_of(tool) or "the connected app"
    if connected_tools.is_read(tool):
        return f"Reads from {app}. Nothing is changed."
    if unattended.is_staged(tool):
        return f"Writes a draft in {app}. Nothing is sent until you send it."
    return f"Runs in {app} and reaches people there. It cannot be undone."


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
        args = {
            "tool_uuid": tool.tool_uuid,
            "tool_name": tool.name,
            "toolkit": app,
            "arguments": dict(arguments.get("arguments") or {}),
        }
        if arguments.get("commitment_id") is not None:
            # A follow-up for a tracked commitment (Follow-up helper): part of
            # the approved act, so it is in the version like the arguments.
            args["commitment_id"] = int(arguments["commitment_id"])
        return {
            "action": action,
            "args": args,
            "label": f"{tool.name} via {app}" if app else str(tool.name),
            "why": why,
            # What pressing Confirm does, in one derived line. Taken from the
            # tool, not from ``arguments``: an effect the model could supply
            # would be the same sentence that called a send a draft.
            "effect": effect_of(tool),
            # Whether it is a send, for the operational quota; derived from
            # the tool like the effect line, never from the model.
            "reaches_people": not connected_tools.is_read(tool)
            and not _is_staged(tool),
            # An email sent or a record created in somebody else's system
            # has no inverse we can promise; the undo window before it fires
            # is the safety, not a button after.
            "reversible": False,
            "state": PROPOSED,
        }

    if action == BUILD_FROM_SPEC:
        from api.services.workflow import bot_from_brief

        try:
            return bot_from_brief.resolve(arguments)
        except bot_from_brief.BriefError as exc:
            raise ActionError(str(exc)) from exc

    if action == TRACK_COMMITMENT:
        from api.services import acting
        from api.services.helpers import commitments

        try:
            fields = commitments.clean(arguments)
        except commitments.Invalid as exc:
            raise ActionError(str(exc)) from exc
        return {
            "action": TRACK_COMMITMENT,
            # Whose follow-up list it joins: the person who asked, recorded
            # now, not whoever later presses Confirm.
            "args": {**fields.as_args(), "owner_user_id": acting.acting_user()},
            "label": commitments.label(fields),
            "why": why,
            "effect": "Adds it to your follow-ups, private to you. Nothing is sent.",
            "reversible": False,
            "state": PROPOSED,
        }

    if action == CREATE_TRACKER:
        from api.services import acting
        from api.services.helpers import trackers

        try:
            spec = trackers.clean_spec(arguments)
        except trackers.Invalid as exc:
            raise ActionError(str(exc)) from exc
        return {
            "action": CREATE_TRACKER,
            "args": {**spec, "owner_user_id": acting.acting_user()},
            "label": trackers.label(spec),
            "why": why,
            "effect": "Creates an empty tracker, private to you. Nothing is sent.",
            "reversible": False,
            "state": PROPOSED,
        }

    if action == INSTALL_FROM_REPOSITORY:
        repository = str(arguments.get("repository") or "").strip()[:200]
        slugs = [str(s)[:64] for s in (arguments.get("slugs") or []) if str(s).strip()]
        titles = [str(t)[:200] for t in (arguments.get("titles") or [])]
        if not repository or not slugs:
            raise ActionError("Nothing to install from there.")
        count = len(slugs)
        return {
            "action": INSTALL_FROM_REPOSITORY,
            "args": {
                "repository": repository,
                "ref": str(arguments.get("ref") or "HEAD")[:120],
                "path": str(arguments.get("path") or "")[:500],
                "slugs": slugs,
                "titles": titles,
            },
            "label": (
                f"Install {count} skill{'s' if count != 1 else ''} from {repository}"
            ),
            "why": why,
            "effect": (
                "Puts them on the Skills shelf for review. No agent runs one "
                "until a person puts it on that agent."
            ),
            "reversible": True,
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
                raise ActionError(
                    f"No agent called {wanted!r} here; use its exact name."
                )
            target_id, target_name = bot.id, bot.name
        elif workflow_id is not None:
            bot = await db_client.get_workflow(
                workflow_id, organization_id=organization_id
            )
            if bot is None:
                raise ActionError("This agent no longer exists.")
            target_id, target_name = bot.id, bot.name
        else:
            raise ActionError("Say which agent.")
        on = action == TURN_BOT_ON
        return {
            "action": action,
            "args": {"workflow_id": target_id, "bot_name": target_name, "is_live": on},
            "label": f"Turn {target_name} {'on' if on else 'off'}",
            "why": why,
            "reversible": True,
            "state": PROPOSED,
        }

    if action == SCHEDULE_ROUTINE:
        name = str(arguments.get("name") or "").strip()[:120]
        instruction = str(arguments.get("instruction") or "").strip()
        said = str(arguments.get("said") or "").strip()
        if not name or not instruction or not said:
            raise ActionError("A routine needs a name, an instruction and a schedule.")
        return {
            "action": action,
            "args": {
                "workflow_id": None,
                "name": name,
                "instruction": instruction,
                "cadence": str(arguments.get("cadence") or "daily"),
                "anchor": str(arguments.get("anchor") or "opening"),
                "at_minute": int(arguments.get("at_minute") or 0),
                "offset_minutes": int(arguments.get("offset_minutes") or 0),
                "weekday": int(arguments.get("weekday") or 0),
                "said": said,
            },
            "label": f"Schedule {name}: {said}",
            "why": why,
            # Saved off and untested; deleting it is the undo.
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
            raise ActionError("Say what to call the agent.")
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

    if action == FORGET_EVERYTHING:
        return {
            "action": action,
            "args": {},
            "label": "Delete everything the business has taught its workers",
            "why": why or "Asked to forget everything.",
            # There is no undo for a memory that is gone; the card says so
            # and a person confirms it knowing that.
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


def _is_staged(tool: Any) -> bool:
    from api.services.workflow import unattended

    return bool(unattended.is_staged(tool))


def _is_same_proposal(row: Any, payload: dict[str, Any]) -> bool:
    """Whether this row is the same act, still waiting.

    The same action with the same arguments. Not the label or the reason --
    the model writes those and they can differ word for word for one act,
    which would let a second card through on nothing but phrasing.

    Settled cards never match. Confirmed or declined, the thing is finished,
    and asking again is a new request; otherwise one decline would bar the
    act for good.
    """
    existing = getattr(row, "payload", None)
    if not isinstance(existing, dict):
        return False
    if existing.get("state") != PROPOSED:
        return False
    if existing.get("action") != payload.get("action"):
        return False
    return (existing.get("args") or {}) == (payload.get("args") or {})


async def _already_proposed(
    *,
    organization_id: int,
    workflow_id: int | None,
    payload: dict[str, Any],
    in_channel: bool = True,
) -> Optional[Any]:
    """The card already waiting for this exact act, if there is one.

    Scoped to the recent end of the thread rather than all of history: a
    proposal from last week that nobody ever settled should not silently
    swallow today's ask.

    And to the thread it would be written on. "One ask, one card" is about
    one conversation: a card waiting in another chat must not swallow the
    ask made here, which is a card somebody asked for and never got.
    """
    assistant = workflow_id is None and not in_channel
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kinds=[AgentEventKind.ACTION_PROPOSED.value],
        limit=DUPLICATE_WINDOW,
        assistant_thread=assistant,
        thread_id=agent_timeline.current_thread() if assistant else None,
    )
    for row in rows or []:
        if _is_same_proposal(row, payload):
            return row
    return None


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

    # One ask, one card. The dispatch loop proposes for every tool call the
    # model emits, and a model that emits the same call twice in a round put
    # two identical cards on the thread -- two confirmations for one act,
    # which on a send is two emails. Never raises: a card lost because this
    # lookup broke is somebody who asked for something and got nothing, and
    # the duplicate is the smaller harm.
    try:
        waiting = await _already_proposed(
            organization_id=organization_id,
            workflow_id=workflow_id,
            payload=payload,
            in_channel=in_channel,
        )
    except Exception as exc:  # noqa: BLE001 - see above
        logger.warning("Could not check for a duplicate proposal: {}", exc)
        waiting = None
    if waiting is not None:
        return {
            "status": "already_proposed",
            "note": (
                f"{payload['label']} is already proposed and waiting on the "
                "thread. Do not propose it again. Say you have already "
                "proposed it, then end your reply."
            ),
        }

    # Which helper asked, when one did (launch stream `agents`): every card
    # carries organisation, member and helper. Not part of the version.
    from api.services.helpers import turn as helper_turn

    if helper_turn.current():
        payload["helper"] = helper_turn.current()
    if _ledger_on(organization_id):
        # The exact act a Confirm will approve (design, "Approval state").
        payload["version"] = payload_version(payload)
    recorded = await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        summary=payload["label"],
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload=payload,
        in_channel=in_channel,
    )
    if recorded is not None:
        from types import SimpleNamespace

        from api.services import acting

        await _emit(
            "approval_requested",
            SimpleNamespace(id=recorded, organization_id=organization_id),
            payload,
            acting.acting_user(),
        )
    return {
        "status": "proposed",
        "card_id": recorded,
        "note": (
            f"Proposed: {payload['label']}. A person has to confirm it on the "
            "card before it happens. Say that you have proposed it, then end "
            "your reply."
        ),
    }


# --- the person's half ------------------------------------------------------


def _stamp(user_id: int) -> dict[str, Any]:
    return {"by": user_id, "at": datetime.now(UTC).isoformat()}


def _stamp_ledger_state(organization_id: int, payload: dict[str, Any]) -> None:
    if _ledger_on(organization_id):
        # The card's place in the shared task vocabulary, kept on the row so
        # every channel reads the same state without mapping it again.
        from api.services.workflow import task_ledger

        payload["ledger_state"] = task_ledger.card_state(payload)


async def _write(event: Any, payload: dict[str, Any]) -> None:
    _stamp_ledger_state(event.organization_id, payload)
    if not await db_client.set_agent_event_payload(
        event.id, organization_id=event.organization_id, payload=payload
    ):
        raise ActionError("That proposal is not here any more.")


async def _move(event: Any, from_state: str, payload: dict[str, Any]) -> None:
    """Write ``payload`` only if the card is still in ``from_state``."""
    _stamp_ledger_state(event.organization_id, payload)
    if not await db_client.transition_agent_event_payload(
        event.id,
        organization_id=event.organization_id,
        from_state=from_state,
        payload=payload,
    ):
        raise ActionError("Already settled.")


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
    version: str | None = None,
) -> dict[str, Any]:
    """Confirm, decline or undo, on the card. Returns the updated payload.

    ``confirm`` arms the action and queues it to fire after the undo window.
    ``decline`` ends a proposal nothing was done about.
    ``undo`` cancels an armed action, or puts back a done one that can be.

    With the task ledger on, ``confirm`` approves one version of the card:
    ``version`` must be the one the person was shown. A card edited since
    (``revise``) has a new version, and the old Confirm is refused rather
    than applied to words nobody read. The same press from two channels
    arms it once: the move from proposed is a compare-and-swap.
    """
    event = await _proposal(organization_id, event_id)
    payload = dict(event.payload or {})
    state = payload.get("state") or PROPOSED

    if verb == "confirm":
        if state != PROPOSED:
            raise ActionError("Already settled.")
        if _ledger_on(organization_id):
            current = payload_version(payload)
            stored = payload.get("version")
            if stored is not None and stored != current:
                # The arguments changed under a stored version: not
                # something any screen showed.
                raise ActionError("This card changed since it was proposed. Ask again.")
            if stored is not None and version != stored:
                raise ActionError(
                    "This changed since you looked at it. Review the new "
                    "version and confirm again."
                )
            # A card proposed before versions existed is stamped now.
            payload["version"] = current
            payload["idempotency_key"] = f"card:{event.id}:{current}"
        # The approval matrix (KAN-160): a card is a "card" subject with no
        # amount. Raises ApprovalRequired, naming who must, before anything
        # is armed; a no-op while the switch is off.
        await approvals.check(
            organization_id, subject=approvals.CARD, amount_paise=None, user_id=user_id
        )
        fires_at = datetime.now(UTC) + timedelta(seconds=UNDO_WINDOW_SECONDS)
        payload["state"] = ARMED
        payload["confirmed"] = _stamp(user_id)
        if payload.get("version"):
            payload["confirmed"]["version"] = payload["version"]
        payload["fires_at"] = fires_at.isoformat()
        await _move(event, PROPOSED, payload)
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
            await _move(event, ARMED, payload)
            raise ActionError(payload["error"]) from exc
        await _audit(event, payload, audit_log.CARD_CONFIRMED, user_id, state)
        await _emit("approval_granted", event, payload, user_id)
        return payload

    if verb == "decline":
        if state != PROPOSED:
            raise ActionError("Already settled.")
        payload["state"] = DECLINED
        payload["declined"] = _stamp(user_id)
        await _move(event, PROPOSED, payload)
        await _audit(event, payload, audit_log.CARD_DECLINED, user_id, state)
        await _emit("approval_rejected", event, payload, user_id)
        if payload.get("action") == RUN_TOOL:
            # A declined send is an outcome too (OP-4): the prospect is
            # marked, and the next run does not propose them as new.
            from api.services.workflow import send_approval

            await send_approval.note_declined(
                organization_id,
                dict((payload.get("args") or {}).get("arguments") or {}),
            )
        return payload

    if verb == "undo":
        if state == ARMED:
            payload["state"] = CANCELLED
            payload["cancelled"] = _stamp(user_id)
            await _move(event, ARMED, payload)
            await _audit(event, payload, audit_log.CARD_UNDONE, user_id, state)
            return payload
        if state == DONE and payload.get("reversible"):
            # Claim the undo first, so two presses put it back once.
            payload["state"] = UNDONE
            payload["undone"] = _stamp(user_id)
            await _move(event, DONE, payload)
            try:
                await _reverse(organization_id, payload)
            except Exception:
                # Not put back after all: the card says done again, never a
                # false "undone".
                payload["state"] = DONE
                payload.pop("undone", None)
                await _write(event, payload)
                raise
            await _audit(event, payload, audit_log.CARD_UNDONE, user_id, state)
            await _say(event, f"Put back: {payload['label'].lower()} undone.")
            return payload
        if state == DONE:
            raise ActionError("This cannot be put back.")
        raise ActionError("Nothing to undo.")

    raise ActionError("Not a thing to do with a proposal.")


#: The actions whose arguments a person may edit on the card before
#: confirming. Each edit is a new version (``payload_version``).
REVISABLE = (RUN_TOOL, SEND_DOCUMENT)
#: The fields of a SEND_DOCUMENT card a person may change.
_DOCUMENT_FIELDS = ("note", "to", "channel")


async def revise(
    *,
    organization_id: int,
    event_id: int,
    arguments: dict[str, Any],
    user_id: int,
) -> dict[str, Any]:
    """Edit what a waiting card will do. Returns the updated payload.

    Editing invalidates approval (design, "Approval state"): the card goes
    back to proposed with a new version, an armed one is disarmed (its job
    finds it no longer armed and does nothing), and only a Confirm naming
    the new version can run it. The old versions are listed on the card.
    Task ledger only.
    """
    if not _ledger_on(organization_id):
        raise ActionError("Cards cannot be edited here yet.")
    if not isinstance(arguments, dict) or not arguments:
        raise ActionError("Say what to change.")
    event = await _proposal(organization_id, event_id)
    payload = dict(event.payload or {})
    state = payload.get("state") or PROPOSED
    if state not in (PROPOSED, ARMED):
        raise ActionError("This has already run or been settled.")
    action = payload.get("action")
    if action not in REVISABLE:
        raise ActionError(
            "This one cannot be edited. Decline it and ask for what you want."
        )
    args = dict(payload.get("args") or {})
    if action == RUN_TOOL:
        args["arguments"] = dict(arguments)
    else:
        unknown = set(arguments) - set(_DOCUMENT_FIELDS)
        if unknown:
            raise ActionError(f"Cannot change {', '.join(sorted(unknown))}.")
        for key in _DOCUMENT_FIELDS:
            if key in arguments:
                args[key] = str(arguments[key] or "")[:2_000]
    before = payload.get("version") or payload_version(payload)
    payload["args"] = args
    after = payload_version(payload)
    if after == before:
        return payload
    payload["version"] = after
    payload["revisions"] = [
        *list(payload.get("revisions") or [])[-9:],
        {"version": before, **_stamp(user_id)},
    ]
    payload["state"] = PROPOSED
    for key in ("confirmed", "fires_at", "idempotency_key"):
        payload.pop(key, None)
    await _move(event, state, payload)
    await audit_log.record(
        event.organization_id,
        action=audit_log.CARD_REVISED,
        subject_kind="card",
        subject_id=event.id,
        subject=str(payload.get("label") or action or "")[:255],
        actor_user_id=user_id,
        before={"state": state, "version": before},
        after={"state": PROPOSED, "version": after},
    )
    return payload


async def _audit(
    event: Any, payload: dict[str, Any], action: str, user_id: int, was: str
) -> None:
    """One audit row per press on a card (KAN-160). Never raises."""
    await audit_log.record(
        event.organization_id,
        action=action,
        subject_kind="card",
        subject_id=event.id,
        subject=str(payload.get("label") or payload.get("action") or "")[:255],
        actor_user_id=user_id,
        before={"state": was},
        after={"state": payload.get("state"), "action": payload.get("action")},
    )


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


async def _execute(
    organization_id: int, payload: dict[str, Any], *, event_id: int | None = None
) -> str:
    """Do it. Returns one line on what happened; raises on refusal."""
    action = payload.get("action")
    args = payload.get("args") or {}
    if action in (TRACK_COMMITMENT, CREATE_TRACKER):
        return await _execute_helper_card(organization_id, payload, event_id)
    if action in (TURN_BOT_ON, TURN_BOT_OFF):
        try:
            await db_client.set_workflow_live(
                workflow_id=int(args["workflow_id"]),
                is_live=bool(args["is_live"]),
                organization_id=organization_id,
            )
        except ValueError as exc:
            raise ActionError("That agent no longer exists.") from exc
        return f"{args.get('bot_name', 'The bot')} is now {'on' if args['is_live'] else 'off'}."
    if action == FORGET_EVERYTHING:
        from api.services.knowledge_graph import export

        counts = await export.forget_everything(organization_id)
        return (
            f"Forgotten everything: {counts['records']} remembered, "
            f"{counts['entities']} people and things, "
            f"{counts['episodes']} conversations."
        )
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
    if action == SCHEDULE_ROUTINE:
        routine = await db_client.create_routine(
            organization_id=organization_id,
            workflow_id=None,
            name=str(args.get("name") or "")[:120],
            instruction=str(args.get("instruction") or ""),
            cadence=str(args.get("cadence") or "daily"),
            anchor=str(args.get("anchor") or "opening"),
            at_minute=int(args.get("at_minute") or 0),
            offset_minutes=int(args.get("offset_minutes") or 0),
            weekday=int(args.get("weekday") or 0),
            is_active=False,
        )
        return (
            f"Saved {routine.name} ({args.get('said')}), switched off. Test it "
            "from Schedules, then switch it on."
        )
    if action == CREATE_BOT:
        from api.services.agent_builder import tools as builder_tools

        user_id = int(((payload.get("confirmed") or {}).get("by")) or 0)
        if not user_id:
            raise ActionError("Nobody confirmed this, so nobody owns the agent.")
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
        who = f"@{handle}" if handle else result.get("name", "the agent")
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
    if action == BUILD_FROM_SPEC:
        from api.services.workflow import bot_from_brief

        user_id = int(((payload.get("confirmed") or {}).get("by")) or 0)
        if not user_id:
            raise ActionError("Nobody confirmed this, so nobody owns the agent.")
        try:
            built = await bot_from_brief.build(
                organization_id=organization_id, user_id=user_id, args=args
            )
        except ActionError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Generation reaches an outside service. A failure is said on the
            # card rather than raised at somebody waiting on a reply.
            logger.warning("Could not build an agent from the spec: {}", exc)
            raise ActionError(
                "The agent could not be built from that spec just now."
            ) from exc
        payload.setdefault("result", {}).update(
            {"workflow_id": built["workflow_id"], "handle": built.get("handle")}
        )
        return str(built["note"])

    if action == INSTALL_FROM_REPOSITORY:
        from api.services.skills import imports

        user_id = int(((payload.get("confirmed") or {}).get("by")) or 0) or None
        try:
            owner, _, repo = str(args.get("repository") or "").partition("/")
            link = imports.Link(
                owner=owner,
                repo=repo,
                ref=str(args.get("ref") or "HEAD"),
                path=str(args.get("path") or ""),
            )
            found = imports.recognise(await imports.fetch(link))
            done = await imports.install(
                organization_id=organization_id,
                user_id=user_id,
                link=link,
                found=found,
                only=list(args.get("slugs") or []),
            )
        except imports.ImportError_ as exc:
            raise ActionError(str(exc)) from exc
        except ActionError:
            raise
        except Exception as exc:  # noqa: BLE001 - said on the card, not raised at a person
            logger.warning("Could not install from {}: {}", args.get("repository"), exc)
            raise ActionError(
                "The repository could not be installed from just now."
            ) from exc
        payload.setdefault("result", {}).update({"installed": done})
        return (
            f"Installed {len(done)} skill{'s' if len(done) != 1 else ''} from "
            f"{args.get('repository')}. They are on the Skills shelf for review."
        )

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
        confirmed = payload.get("confirmed") or {}
        confirmed_at = str(confirmed.get("at") or "")
        result = await connected_tools.execute(
            organization_id=organization_id,
            tool=tool,
            arguments=dict(args.get("arguments") or {}),
            ref_id=f"run_tool:{organization_id}:{tool.tool_uuid}:{confirmed_at}",
            # The send goes from the mailbox of whoever pressed Confirm (WS-1).
            user_id=int(confirmed.get("by") or 0) or None,
        )
        if result.get("status") != "success":
            raise ActionError(str(result.get("error") or "It did not go through."))
        payload.setdefault("result", {})["data"] = result.get("data")
        # Outcome recorded (OP-4): the prospect the mail went to is stamped
        # with when and what, so the next run reads it before it writes.
        from api.services.workflow import send_approval

        await send_approval.note_sent(
            organization_id, dict(args.get("arguments") or {})
        )
        return f"Done: {args.get('tool_name', 'the tool')}."
    raise ActionError("That is not something that can be done.")


async def _execute_helper_card(
    organization_id: int, payload: dict[str, Any], event_id: int | None
) -> str:
    """The launch-helper cards: track a commitment, create a tracker."""
    args = payload.get("args") or {}
    owner = args.get("owner_user_id") or (payload.get("confirmed") or {}).get("by")
    if not owner:
        raise ActionError("Nobody asked for this, so nobody owns it.")
    if payload.get("action") == TRACK_COMMITMENT:
        from api.services.helpers import commitments

        try:
            fields = commitments.clean(args)
        except commitments.Invalid as exc:
            raise ActionError(str(exc)) from exc
        await commitments.create(
            fields,
            organization_id=organization_id,
            user_id=int(owner),
            approved_card_id=event_id,
        )
        return f"Tracking it: {commitments.label(fields)[len('Track: ') :]}."
    from api.services.helpers import trackers

    try:
        spec = trackers.clean_spec(args)
    except trackers.Invalid as exc:
        raise ActionError(str(exc)) from exc
    row = await trackers.create(
        spec, organization_id=organization_id, user_id=int(owner)
    )
    return f"Created the tracker {row.name}. Tell me a row to add."


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
            raise ActionError("That agent no longer exists.") from exc
        return
    if action == FORGET_FACT:
        if not await db_client.set_organisation_fact_status(
            organization_id=organization_id,
            fact_id=int(args["fact_id"]),
            status=str(args.get("was_status") or "confirmed"),
        ):
            raise ActionError("That fact is no longer in memory.")
        return
    if action == INSTALL_FROM_REPOSITORY:
        from api.services.skills import imports

        installed = list(
            ((payload.get("result") or {}).get("installed")) or args.get("slugs") or []
        )
        await imports.uninstall(organization_id=organization_id, slugs=installed)
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
    # Claim it before acting. A retried job, or a second one, loses here and
    # does nothing: the action is fired once or not at all.
    payload["state"] = RUNNING
    try:
        await _move(event, ARMED, payload)
    except ActionError:
        logger.info("Action {} was already claimed; not firing it again", event_id)
        return
    ledger = _ledger_on(organization_id)
    confirmer = (payload.get("confirmed") or {}).get("by")
    if ledger:
        # Revalidate at execution (handoff 9, "Approvals"): what runs is the
        # version that was approved, or nothing.
        approved = (payload.get("confirmed") or {}).get("version")
        if approved and (
            approved != payload.get("version") or approved != payload_version(payload)
        ):
            payload["state"] = FAILED
            payload["error"] = (
                "It changed after it was approved. Confirm the new version."
            )
            await _write(event, payload)
            await _say(
                event, f"Could not: {payload['label'].lower()}. {payload['error']}"
            )
            await _emit("task_failed", event, payload, confirmer)
            return
    await _emit("task_started", event, payload, confirmer)
    if _is_outbound(payload) and isinstance(confirmer, int):
        # A send spends the confirming person's daily outbound allowance
        # (operational quotas), whatever plan or free mode says.
        from api.services import quotas

        try:
            await quotas.consume(confirmer, quotas.OUTBOUND_MESSAGES)
        except quotas.QuotaExceeded as exc:
            payload["state"] = FAILED
            payload["error"] = str(exc)
            payload["reason_code"] = "quota_outbound_messages"
            await _write(event, payload)
            await _say(event, f"Not sent: {payload['label'].lower()}. {exc}")
            await _emit("task_failed", event, payload, confirmer)
            return
    try:
        note = await _execute(organization_id, payload, event_id=event.id)
    except ActionError as exc:
        payload["state"] = FAILED
        payload["error"] = str(exc)
        await _write(event, payload)
        await _say(event, f"Could not: {payload['label'].lower()}. {exc}")
        await _emit("task_failed", event, payload, confirmer)
        return
    except Exception as exc:  # noqa: BLE001 - the card must say something
        logger.error("Action {} failed: {}", event_id, exc)
        if ledger and (
            _is_outbound(payload) or payload.get("action") == RETURN_MISSED_CALL
        ):
            # Something reached the outside service and broke: whether it
            # went is not known. Said so, and never retried blind.
            from api.services.workflow import task_ledger

            payload["state"] = OUTCOME_UNKNOWN
            payload["error"] = task_ledger.UNKNOWN_COPY
            await _write(event, payload)
            await _say(event, f"{payload['label']}: {task_ledger.UNKNOWN_COPY}")
            return
        payload["state"] = FAILED
        payload["error"] = "Something went wrong on our side."
        await _write(event, payload)
        await _say(
            event, f"Could not: {payload['label'].lower()}. This is us, not you."
        )
        await _emit("task_failed", event, payload, confirmer)
        return
    payload["state"] = DONE
    payload["done"] = {"at": datetime.now(UTC).isoformat(), "note": note}
    await _write(event, payload)
    await _say(event, note)
    await _emit("task_completed", event, payload, confirmer)


async def sweep_stale_running(now: datetime | None = None) -> int:
    """Cards claimed and never finished become ``outcome_unknown``.

    A worker that died between claiming a card and writing its outcome
    leaves it ``running`` forever, which reads as "still going". After
    ``STALE_RUNNING_MINUTES`` past its firing time it is marked unknown --
    a person checks; it is never fired again. Task ledger only. Returns how
    many it marked.
    """
    from sqlalchemy import text as sql

    from api.services import features
    from api.services.workflow import task_ledger

    if not features.on_anywhere(task_ledger.FLAG):
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                sql(
                    "SELECT id, organization_id FROM agent_events "
                    "WHERE kind = :kind AND payload->>'state' = :state "
                    "ORDER BY id LIMIT 500"
                ),
                {"kind": AgentEventKind.ACTION_PROPOSED.value, "state": RUNNING},
            )
        ).all()
    marked = 0
    for event_id, organization_id in rows:
        if not features.is_on(task_ledger.FLAG, organization_id):
            continue
        event = await db_client.get_agent_event(
            event_id, organization_id=organization_id
        )
        if event is None:
            continue
        payload = dict(event.payload or {})
        fires = payload.get("fires_at")
        try:
            due = datetime.fromisoformat(fires) if fires else None
        except ValueError:
            due = None
        if due is None or due > cutoff:
            continue
        payload["state"] = OUTCOME_UNKNOWN
        payload["error"] = task_ledger.UNKNOWN_COPY
        payload["reason_code"] = "worker_lost"
        try:
            await _move(event, RUNNING, payload)
        except ActionError:
            continue
        await _say(event, f"{payload['label']}: {task_ledger.UNKNOWN_COPY}")
        marked += 1
    return marked

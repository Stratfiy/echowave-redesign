"""Build a bot from a written spec, not from a template.

A customer sends a document -- "here is the flow we want the voice agent to
follow" -- and the answer used to be a conversation about it. Decibyl's only
way to create a bot was ``create_bot``: one of eight templates plus named
answers. A clinic front desk, a property lead qualifier, a loan reminder.
Anything a vendor actually specified -- device key presses, a backend status
check before the next step, consent before a sensitive action, escalation
carrying the call's context -- is not any of those templates and cannot be
bent into one. So the person was offered the nearest template, which is not
the thing they asked for.

The engine to do it properly was already here and unreachable: the create
wizard hands a written brief to ``generate_workflow_definition``, which
builds the whole graph (and falls back to a local starter when generation is
unconfigured). One endpoint called it and no tool did. This is the wire.

**Generation happens on confirm, never on the proposal.** It is slow and it
costs money, and a card that spent both before anybody pressed anything
would let a chatty model bill an account for bots nobody asked for. The
proposal carries the brief; pressing Confirm is what builds.

The card is ``actions.py``'s, so this inherits the whole safety story: an
undo window before it fires, one row recording who confirmed, and the done
card offering Hear it and Try it on the bot that came out.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import BotChannel, CallType
from api.services.workflow import (
    brief_apps,
    connected_tools,
    schedule_from_words,
    task_graph,
)
from api.services.workflow.agent_brief import (
    AgentBrief,
    apply_brief,
    compose_activity_description,
    workflow_name,
)
from api.services.workflow.template_generation import generate_workflow_definition
from api.services.workflow.trigger_paths import (
    extract_trigger_paths,
    regenerate_trigger_uuids,
)

TOOL_NAME = "build_bot_from_spec"

#: The two directions the generator understands, for a bot on the phone.
CALL_TYPES = (CallType.INBOUND.value, CallType.OUTBOUND.value)

#: What the model picks between. "chat" is its own value rather than a third
#: call type because a chat bot has no direction: somebody opens a window and
#: types, which is neither ringing us nor being rung. Making the model choose
#: inbound-or-outbound for a bot with no phone is how every bot ends up
#: greeting people for calling.
CHANNELS = (
    BotChannel.VOICE.value,
    BotChannel.CHAT.value,
)

#: How much of a spec is handed to generation. The attachment block already
#: clips a file at 12,000 characters, and a brief longer than this is a
#: document that should be knowledge the bot reads, not instructions baked
#: into its graph.
MAX_BRIEF_CHARS = 12_000
MIN_BRIEF_CHARS = 80
MAX_NAME_CHARS = 80

DESCRIPTION = (
    "Build a bot from a written specification -- a document somebody "
    "attached, or a flow they described in detail: the steps, what the bot "
    "asks, what it checks, when it escalates. Use this INSTEAD of "
    "propose_action/create_bot whenever what they want is not one of the "
    "templates in your context, which is most real specs. Pass the spec "
    "itself as `spec`, in their words, including every step and rule -- do "
    "not summarise it into a sentence. Nothing is built until a person "
    "confirms on the card, and building takes a moment. Say you have "
    "proposed it and end your reply."
)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "What to call the bot, as the team would say it.",
                },
                "channel": {
                    "type": "string",
                    "enum": list(CHANNELS),
                    "description": (
                        "'voice' for a bot on the phone, 'chat' for one that "
                        "answers in writing. Default 'voice'."
                    ),
                },
                "call_type": {
                    "type": "string",
                    "enum": list(CALL_TYPES),
                    "description": (
                        "Only for a voice bot: 'inbound' when people call the "
                        "business, 'outbound' when the bot rings them first. "
                        "Leave it out for a chat bot -- it has no direction."
                    ),
                },
                "use_case": {
                    "type": "string",
                    "description": "The job in a few words, e.g. 'Elock support'.",
                },
                "spec": {
                    "type": "string",
                    "description": (
                        "The specification itself: every step, what is said, "
                        "what is checked, what happens when it fails. Copy it "
                        "from the document rather than condensing it."
                    ),
                },
                "why": {
                    "type": "string",
                    "description": (
                        "One line on why this bot, for the person confirming "
                        "it: what they said or sent that this is built from. "
                        "They are approving something that cannot be switched "
                        "back off, so say what it is for."
                    ),
                },
            },
            # `call_type` is not required: it is meaningless for a chat bot,
            # and a required field the model must invent for half the cases
            # is a field that gets invented for all of them.
            "required": ["name", "spec"],
        },
    }


class BriefError(Exception):
    """Something the model is told, so it can ask rather than fail a card."""


def resolve(arguments: dict[str, Any]) -> dict[str, Any]:
    """Turn the model's arguments into a payload the card renders and the
    job builds from. Nothing is generated here -- see the module docstring."""
    name = str(arguments.get("name") or "").strip()[:MAX_NAME_CHARS]
    if not name:
        raise BriefError("Say what to call the bot.")
    channel = str(arguments.get("channel") or BotChannel.VOICE.value).strip().lower()
    if channel not in CHANNELS:
        raise BriefError(
            f"Say whether it is {' or '.join(CHANNELS)} -- the phone, or writing."
        )
    if channel == BotChannel.CHAT.value:
        # Carried anyway, because the generator's signature takes one and a
        # chat conversation is opened by the person: inbound is the true
        # answer to a question nothing will ask.
        call_type = CallType.INBOUND.value
    else:
        call_type = str(arguments.get("call_type") or "").strip().lower()
        if call_type not in CALL_TYPES:
            raise BriefError(
                f"Say whether it is {' or '.join(CALL_TYPES)} -- who rings whom."
            )
    spec = str(arguments.get("spec") or "").strip()
    if len(spec) < MIN_BRIEF_CHARS:
        raise BriefError(
            "That is not enough of a spec to build from. Give the steps the "
            "bot should follow, in their words."
        )
    spec = spec[:MAX_BRIEF_CHARS]
    use_case = str(arguments.get("use_case") or "").strip()[:120] or name
    # Imported here, not at module scope: actions reaches into this module
    # the same way, and a pair of top-level imports would not resolve.
    from api.services.workflow import actions

    why = str(arguments.get("why") or "").strip()[: actions.MAX_WHY_CHARS]
    return {
        "action": ACTION,
        "args": {
            "name": name,
            "channel": channel,
            "call_type": call_type,
            "use_case": use_case,
            "spec": spec,
        },
        "label": f"Build {name} from the spec",
        # Every other card carries a reason and this one carried an empty
        # string, because its tool never asked for one. A person was shown
        # an irreversible build with a blank reason line -- of the cards we
        # put in front of people, the one that most needs to say why. The
        # fallback is the honest answer when the model omits it: this is
        # being built because somebody described it.
        "why": why or f"Built from what you described: {use_case}.",
        # A bot that has been built is not un-built by a switch; the card
        # offers Hear it and Try it instead, as create_bot's does.
        "reversible": False,
        "state": "proposed",
    }


#: The action kind, registered with actions.py's internal set.
ACTION = "build_from_spec"


async def build(
    *, organization_id: int, user_id: int, args: dict[str, Any]
) -> dict[str, Any]:
    """Generate the graph and create the bot. Returns what the done card shows.

    Mirrors POST /workflow/create/template, which is the point: a bot built
    from the thread and a bot built from the wizard should be the same bot,
    with the same guardrails written in and the same trigger paths claimed.
    """
    brief = AgentBrief(
        call_type=args["call_type"],
        use_case=args.get("use_case") or "",
        objective=args.get("name") or "",
        conversation_flow=args["spec"],
    )
    description = compose_activity_description(brief) or args["spec"]

    # Defaulted rather than required: a card proposed before this field
    # existed, and confirmed after, still builds the bot it promised.
    channel = BotChannel(args.get("channel") or BotChannel.VOICE.value)
    spec = str(args.get("spec") or "")

    if task_graph.wanted(spec):
        # A brief that names when it runs is a task, and a task bot built as
        # a call bot spends its run saying hello. One was: greeting, "find
        # out what they need", "this is an inbound call: they rang you", and
        # a persona telling it to ask one question per turn -- fired by a
        # cron tick, with nobody to answer. Written rather than generated;
        # see task_graph for why, and why the clock is what decides.
        definition = task_graph.build(name=args["name"], spec=spec)
    else:
        generated = await generate_workflow_definition(
            call_type=str(args["call_type"]).upper(),
            use_case=brief.use_case,
            activity_description=description,
            organization_id=organization_id,
            channel=channel,
        )
        definition = (
            regenerate_trigger_uuids(generated.get("workflow_definition", {})) or {}
        )
        definition = apply_brief(definition, brief)

    # The apps the brief named, so a bot whose job is reading Gmail is built
    # holding Gmail. Without this the generator produced a bot with no tools
    # on any node: the founder's inbox-summary bot fired on schedule and
    # asked him to paste his emails in. Writes come too; whether one may run
    # is decided per run, not per bot -- see unattended.
    definition = await _attach_named_apps(
        definition, organization_id=organization_id, spec=str(args.get("spec") or "")
    )

    paths = extract_trigger_paths(definition)
    if paths:
        try:
            await db_client.assert_trigger_paths_available(trigger_paths=paths)
        except Exception as exc:  # noqa: BLE001
            # A clash is somebody else's live trigger, not a reason to lose
            # the bot: it is created without them and the paths are said.
            logger.warning("Trigger paths unavailable for the built bot: {}", exc)
            paths = []

    workflow = await db_client.create_workflow(
        name=args["name"] or generated.get("name") or workflow_name(brief),
        workflow_definition=definition,
        user_id=user_id,
        organization_id=organization_id,
        # Only for a chat bot. Passing {"channel": "voice"} for the other
        # case would overwrite the opinionated defaults create_workflow
        # writes for a new agent with a block containing one key.
        workflow_configurations=(
            {"channel": channel.value} if channel is BotChannel.CHAT else None
        ),
    )
    if paths:
        try:
            await db_client.sync_triggers_for_workflow(
                workflow_id=workflow.id,
                organization_id=organization_id,
                trigger_paths=paths,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not claim trigger paths for the built bot: {}", exc)

    steps = len((definition or {}).get("nodes") or [])
    note = f"Built {workflow.name} — {steps} step{'' if steps == 1 else 's'}."

    # The schedule the person said, if they said one. Without this a bot asked
    # for "every morning at 8am" was built live with an empty triggers list:
    # the eight o'clock survived as prose in the spec, where no clock reads it,
    # and nothing anywhere said the bot would never run.
    scheduled = await _schedule_it(
        organization_id=organization_id,
        workflow_id=workflow.id,
        name=workflow.name,
        spec=str(args.get("spec") or ""),
    )
    if scheduled:
        note = f"{note} It runs {scheduled}, once you test it."
    waiting_on = await _waiting_on(
        organization_id=organization_id, spec=str(args.get("spec") or "")
    )
    if waiting_on:
        one = len(waiting_on) == 1
        note = (
            f"{note} Waiting on: {', '.join(waiting_on)} -- connect "
            f"{'it' if one else 'them'} from the card{'' if one else 's'} "
            f"above and the bot has {'its' if one else 'their'} tools."
        )

    return {
        "workflow_id": workflow.id,
        "handle": getattr(workflow, "handle", None),
        "steps": steps,
        "runs": scheduled,
        "waiting_on": waiting_on,
        "note": note,
    }


#: How many connect cards one build puts on the thread. A brief naming six
#: apps gets three cards and a sentence naming the rest.
MAX_WAITING_CARDS = 3


async def _waiting_on(*, organization_id: int, spec: str) -> list[str]:
    """The apps the brief names that this workspace has not connected (D-1b).

    The bot is built now, with the tools of the apps that *are* connected;
    the rest are the "waiting on" list, and each gets a connect card on the
    thread so the person connects it where they are rather than being sent
    to a screen. Matched on whole words against the connector catalogue, so
    "shopify" in the brief finds Shopify and "gmail" does not find
    "notgmailish". Never raises: a list that cannot be read is an empty
    list, and the bot is the deliverable.
    """
    text = (spec or "").lower()
    if not text:
        return []
    try:
        from api.services.integrations.composio import catalogue
        from api.services.workflow import connector_offer

        rows = await catalogue.connectors()
        connected = {
            connected_tools.toolkit_of(t)
            for t in await connected_tools.list_for_organization(organization_id)
        }
        missing = [
            row.slug
            for row in rows
            if row.slug
            and row.slug not in connected
            and re.search(rf"(?<![a-z0-9]){re.escape(row.slug)}(?![a-z0-9])", text)
        ]
        for slug in missing[:MAX_WAITING_CARDS]:
            await connector_offer.offer(
                organization_id=organization_id,
                arguments={"app": slug, "why": "The bot you just built uses it."},
            )
        return missing
    except Exception as exc:  # noqa: BLE001 - the bot is the deliverable
        logger.warning("Could not work out what the built bot is waiting on: {}", exc)
        return []


async def _attach_named_apps(
    definition: dict[str, Any], *, organization_id: int, spec: str
) -> dict[str, Any]:
    """Give the bot the tools of every connected app its brief names.

    Never raises. An account whose tools cannot be read is an account whose
    bot is built without them -- which is the bot it would have had anyway,
    and losing the bot over a tool list would be the worse trade.
    """
    try:
        # Both kinds: Composio, and the apps connected through their own MCP
        # server. Only the first was passed here, so a brief naming Zerodha
        # -- which is not in Composio's catalogue at all -- attached nothing
        # and the tool had to be put on the node by hand.
        tools = list(await connected_tools.list_for_organization(organization_id))
        tools += await connected_tools.mcp_for_organization(organization_id)
        uuids = brief_apps.tool_uuids(spec, tools)
        if uuids:
            logger.info("Built bot gets {} tool(s) from its brief", len(uuids))
        return brief_apps.attach(definition, uuids)
    except Exception as exc:  # noqa: BLE001 - the bot is the deliverable
        logger.warning("Could not give the built bot the apps its brief named: {}", exc)
        return definition


async def _schedule_it(
    *, organization_id: int, workflow_id: int, name: str, spec: str
) -> Optional[str]:
    """Give the built bot the routine its brief described, if it described one.

    Returns how to say the schedule back, or ``None`` when the brief named no
    schedule -- in which case the bot is built exactly as it was before this
    existed.

    Created inactive, which is not a limitation but the existing gate: a
    routine cannot arm until it has been test-run, so a schedule read out of
    prose gets the same proving somebody typing it into the form would get.
    The note says so rather than leaving a person to wonder why 8am came and
    went.

    Never raises. A routine that could not be written is a bot without a
    schedule, which is the bot they would have had anyway; losing the bot over
    it would be a worse trade.
    """
    schedule = schedule_from_words.parse(spec)
    if schedule is None:
        return None
    try:
        await db_client.create_routine(
            organization_id=organization_id,
            workflow_id=workflow_id,
            name=name,
            instruction=spec,
            cadence=schedule.cadence.value,
            anchor=schedule.anchor.value,
            at_minute=schedule.at_minute,
            offset_minutes=schedule.offset_minutes,
            weekday=schedule.weekday,
        )
    except Exception as exc:  # noqa: BLE001 - the bot is the deliverable
        logger.warning(
            "Built {} but could not give it the schedule it asked for: {}", name, exc
        )
        return None
    return schedule.said

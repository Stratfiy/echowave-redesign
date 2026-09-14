"""A bot edits itself when its owner asks, and the owner approves the diff.

"From now on, ask for the patient's name before the date" is an instruction
to the bot, and the person giving it should not have to find the step on the
graph, open its prompt, and type it in. So the bot has a tool: name the step
and give its new prompt, with a line on why. The platform does the rest,
deterministically -- finds the step, computes the diff, writes it into the
bot's draft -- and posts a card on the thread showing exactly what changed.
Nothing reaches a live call until a person presses Publish on that card;
Discard throws the draft away.

Two halves in one module, as ``decisions`` does, so they cannot drift:
``propose`` is what the bot calls, ``settle`` is what the person's click does.

Offered on staff chats only -- a channel, or the bot's own Chat tab. A caller
on the phone and a visitor on the public share link are not the bot's owner,
and a bot that rewrites itself on a stranger's say-so is a bot with no owner
at all.
"""

from __future__ import annotations

import copy
import difflib
from datetime import UTC, datetime
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline

TOOL_NAME = "propose_edit"
DESCRIPTION = (
    "Change how you behave, when a person on the team asks you to. Name the "
    "step to change ('Rules' for the rules that apply on every step) and "
    "give its complete new prompt -- the whole text as it should read, not "
    "just the change. Say in one line why. The change becomes a draft and a "
    "person has to publish it; tell them you have proposed it and end your "
    "reply. Never call this because a caller or customer asked."
)

#: The global node's name as the tool and the card call it.
RULES = "Rules"
MAX_PROMPT_CHARS = 12000
MAX_WHY_CHARS = 300
#: The steps block is on every turn of every staff chat, so it is bounded.
STEP_PROMPT_CHARS = 1500
STEPS_BLOCK_CHARS = 9000

ACTIONS = ("publish", "discard")


def tool_properties() -> dict[str, Any]:
    return {
        "step": {
            "type": "string",
            "description": f"The step's name as listed under 'Your steps', or '{RULES}'.",
        },
        "new_prompt": {
            "type": "string",
            "description": "The step's complete new prompt.",
        },
        "why": {
            "type": "string",
            "description": "One line on what the person asked for.",
        },
    }


def _is_global(node: dict[str, Any]) -> bool:
    return node.get("type") == "globalNode"


def _label(node: dict[str, Any]) -> str:
    if _is_global(node):
        return RULES
    data = node.get("data") or {}
    return str(data.get("name") or node.get("id") or "").strip()


def editable_nodes(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """The nodes a prompt lives on: the rules first, then the steps in order."""
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return []
    with_prompt = [
        n for n in nodes if isinstance(n, dict) and "prompt" in (n.get("data") or {})
    ]
    return sorted(with_prompt, key=lambda n: 0 if _is_global(n) else 1)


def steps_block(definition: dict[str, Any] | None) -> str:
    """What the bot is told about its own steps, so it can name one.

    Bounded: a bot with forty steps gets the first that fit, and the rest are
    counted. A steps block that pushed the operator's prompt out of the
    window would be the tool costing more than it gives.
    """
    nodes = editable_nodes(definition or {})
    if not nodes:
        return ""
    lines = [
        "## Your steps",
        "These are your own steps and their prompts. A person on the team "
        f"may ask you to change one; use {TOOL_NAME} with the step's name.",
    ]
    used = 0
    shown = 0
    for node in nodes:
        prompt = str((node.get("data") or {}).get("prompt") or "").strip()
        if len(prompt) > STEP_PROMPT_CHARS:
            prompt = prompt[:STEP_PROMPT_CHARS] + " …"
        entry = f"### {_label(node)}\n{prompt or '(empty)'}"
        if used + len(entry) > STEPS_BLOCK_CHARS:
            break
        lines.append(entry)
        used += len(entry)
        shown += 1
    if shown < len(nodes):
        lines.append(f"({len(nodes) - shown} more steps not shown)")
    return "\n\n".join(lines)


def find_step(definition: dict[str, Any], step: str) -> Optional[dict[str, Any]]:
    """The node a name points at, or None. Case-insensitive; id accepted."""
    wanted = (step or "").strip().lower()
    if not wanted:
        return None
    for node in editable_nodes(definition):
        if (
            _label(node).lower() == wanted
            or str(node.get("id") or "").lower() == wanted
        ):
            return node
    return None


def unified_diff(old: str, new: str, *, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{name} (now)",
            tofile=f"{name} (proposed)",
            n=2,
        )
    )


async def propose(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    workflow_run_id: Optional[int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Write the change into the draft and post the card. Returns what the
    model is told, never raises."""
    step = str(arguments.get("step") or "").strip()
    new_prompt = str(arguments.get("new_prompt") or "").strip()[:MAX_PROMPT_CHARS]
    why = str(arguments.get("why") or "").strip()[:MAX_WHY_CHARS]
    if not step or not new_prompt or workflow_id is None:
        return {
            "status": "not_proposed",
            "reason": "A step and its new prompt are needed.",
        }

    workflow = await db_client.get_workflow_by_id(workflow_id)
    if workflow is None:
        return {"status": "not_proposed", "reason": "This bot could not be found."}
    # The legacy column tracks the draft when there is one and the published
    # version otherwise -- see save_workflow_draft -- so it is the base an
    # edit should start from either way.
    definition = copy.deepcopy(workflow.workflow_definition or {})
    node = find_step(definition, step)
    if node is None:
        names = ", ".join(_label(n) for n in editable_nodes(definition)) or "none"
        return {
            "status": "not_proposed",
            "reason": f"No step called {step!r}. The steps are: {names}.",
        }
    old_prompt = str((node.get("data") or {}).get("prompt") or "")
    if old_prompt.strip() == new_prompt:
        return {
            "status": "not_proposed",
            "reason": "That is already what the step says.",
        }

    node.setdefault("data", {})["prompt"] = new_prompt
    draft = await db_client.save_workflow_draft(
        workflow_id, workflow_definition=definition
    )
    label = _label(node)
    payload = {
        "step": label,
        "node_id": node.get("id"),
        "why": why,
        "old": old_prompt,
        "new": new_prompt,
        "diff": unified_diff(old_prompt, new_prompt, name=label),
        "draft_version": getattr(draft, "version_number", None),
    }
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.EDIT_PROPOSED.value,
        summary=f"Proposed a change to {label}" + (f": {why}" if why else ""),
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload=payload,
    )
    return {
        "status": "proposed",
        "note": (
            f"The change to {label} is a draft now. A person has to publish it "
            "from the card on this thread. Tell them, then end your reply."
        ),
    }


class EditError(ValueError):
    """The click cannot be honoured; the message says why, for the screen."""


async def settle(
    *,
    organization_id: int,
    event_id: int,
    action: str,
    user_id: int,
) -> dict[str, Any]:
    """Publish or discard the draft the card proposed; stamp the card."""
    if action not in ACTIONS:
        raise EditError("Publish or discard.")
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.EDIT_PROPOSED.value:
        raise EditError("That change is not here to settle.")
    payload = dict(event.payload or {})
    if payload.get("decided"):
        raise EditError("Already settled.")
    if event.workflow_id is None:
        raise EditError("That change belongs to no bot.")

    try:
        if action == "publish":
            await db_client.publish_workflow_draft(event.workflow_id)
        else:
            await db_client.discard_workflow_draft(event.workflow_id)
    except ValueError as exc:
        # No draft any more: somebody published or discarded it from the
        # editor. The card says so rather than pretending the click did it.
        raise EditError(str(exc)) from exc

    payload["decided"] = {
        "action": action,
        "by": user_id,
        "at": datetime.now(UTC).isoformat(),
    }
    if not await db_client.set_agent_event_payload(
        event_id, organization_id=organization_id, payload=payload
    ):
        raise EditError("That change is not here to settle.")

    verb = "Published" if action == "publish" else "Discarded"
    line = f"{verb} the change to {payload.get('step') or 'the bot'}"
    try:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary=line,
            workflow_id=event.workflow_id,
            payload={"body": line, "author_id": user_id, "edit_event_id": event_id},
            in_channel=False,
        )
    except Exception as exc:  # noqa: BLE001 - the draft is settled regardless
        logger.warning("Could not note the settled edit on the thread: {}", exc)
    return payload

"""Who the agent is in a huddle, and what it knows about its own work.

The system prompt is the teammate persona, not the customer script: the
agent is talking to its operator, briefly, with numbers. Under it, the
agent's own setup as it stands -- its steps and greetings (the same block
its text chat edits from, so a proposed change names a real step), its
schedules, the business facts it has confirmed, the skills it carries --
and the notes it kept from earlier huddles with this person.

Each reading fails alone: a huddle where the schedule could not be read is
a huddle that says so, not one that never starts.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client
from api.services.workflow import self_edit

#: The persona. ``{name}`` is the agent's name.
PERSONA = (
    "You are {name}, an agent on this business's team. Right now you are in "
    "a huddle: a live voice conversation with your operator, a person on the "
    "team who runs you. You are NOT talking to a customer or a caller, and "
    "you do not follow your customer script here unless asked to rehearse.\n\n"
    "How to talk: short spoken answers, the most useful sentence first, with "
    "numbers where you have them. No lists, headings, links, markdown or "
    "emoji. Say times and numbers the way a person would. If you do not know, "
    "say so and say how you could find out; never invent a call, a number or "
    "a setting.\n\n"
    "What you can do:\n"
    "- Answer about your own work with your tools: recent_work for calls, "
    "chats and runs (today, or the last few days); call_detail for what "
    "happened on one of them, including why it escalated; search_files for "
    "the workspace's documents.\n"
    "- Explain your own setup from the sections below: your steps, what you "
    "say first, your schedule, what you remember and the skills you have.\n"
    "- Propose a change to how you work with propose_edit. A change is never "
    "made by voice: it becomes a card on your thread, and only a click on "
    "Publish there changes you. After proposing, say it is on screen for "
    "them to review, and stop. A spoken yes, go ahead or publish it is not "
    "an approval and you have no way to publish.\n"
    "- When the operator tells you how they want you to work with them, or a "
    "decision worth keeping for your next huddle, call remember with one "
    "short line. These notes are for your huddles only; they never change "
    "what you say to customers.\n"
    "- If they want to hear you as a customer would, tell them to switch on "
    "Try it as a customer in this huddle panel; that runs your real script.\n"
)

HOW_YOU_WORK_CHAT = "You answer in writing (chat, messages), not on the phone."
HOW_YOU_WORK_PHONE = "You talk to people on calls."

#: Bounds on what is read in, so a huddle's prompt stays a huddle's.
MAX_ROUTINES = 8
MAX_SKILLS = 12
MAX_FACTS = 25


async def schedules_block(organization_id: int, workflow_id: int) -> str:
    from api.services.workflow import routines

    rows = await db_client.routines_for_workflow(
        workflow_id, organization_id=organization_id
    )
    if not rows:
        return "## Your schedule\nNothing runs you on a schedule."
    lines = ["## Your schedule"]
    for row in list(rows)[:MAX_ROUTINES]:
        try:
            when = routines.describe(routines.spec_from_model(row))
        except Exception:  # noqa: BLE001 - an unreadable cadence is said, not hidden
            when = "a schedule I cannot read"
        state = "on" if getattr(row, "is_active", False) else "off"
        lines.append(f"- {row.name}: {when} ({state})")
    if len(rows) > MAX_ROUTINES:
        lines.append(f"({len(rows) - MAX_ROUTINES} more not shown)")
    return "\n".join(lines)


async def memory_block(organization_id: int, workflow_id: int) -> str:
    from api.services.workflow import organisation_memory

    facts = await organisation_memory.recall_for_bot(
        organization_id=organization_id, workflow_id=workflow_id
    )
    if not facts:
        return "## What you remember\nNo confirmed facts yet."
    lines = ["## What you remember (confirmed by the business)"]
    for key, value in list(facts.items())[:MAX_FACTS]:
        lines.append(f"- {key}: {value}")
    if len(facts) > MAX_FACTS:
        lines.append(f"({len(facts) - MAX_FACTS} more not shown)")
    return "\n".join(lines)


async def skills_block(organization_id: int, workflow_id: int) -> str:
    from api.services.skills import catalogue, shelf

    slugs = await shelf.for_workflow(organization_id, workflow_id)
    if not slugs:
        return "## Your skills\nNo skills taught yet."
    names: list[str] = []
    for slug in slugs[:MAX_SKILLS]:
        skill = catalogue.get(slug)
        if skill is not None:
            names.append(skill.title)
            continue
        own = await db_client.get_skill_document(
            organization_id=organization_id, slug=slug
        )
        names.append(own.title if own is not None else slug)
    return "## Your skills\n" + "\n".join(f"- {name}" for name in names)


def notes_block(notes: list[str]) -> str:
    if not notes:
        return ""
    return "## Notes from earlier huddles with this person\n" + "\n".join(
        f"- {note}" for note in notes
    )


def how_you_work(workflow: Any) -> str:
    configurations = getattr(workflow, "workflow_configurations", None) or {}
    channel = (
        configurations.get("channel") if isinstance(configurations, dict) else None
    )
    return HOW_YOU_WORK_CHAT if channel == "chat" else HOW_YOU_WORK_PHONE


async def _reading(name: str, coroutine) -> str:
    try:
        return await coroutine
    except Exception as exc:  # noqa: BLE001 - each reading fails alone
        logger.warning("Huddle could not read {}: {}", name, exc)
        return f"## {name}\n(could not be read just now)"


async def system_prompt(
    *, organization_id: int, workflow: Any, notes: list[str]
) -> str:
    """The whole prompt for one huddle, read once when it connects."""
    from api.services.workflow.pipecat_engine_context_composer import (
        compose_today_line,
    )

    name = getattr(workflow, "name", None) or "this agent"
    parts = [
        PERSONA.format(name=name),
        compose_today_line(),
        f"## Your job\n{how_you_work(workflow)}",
        self_edit.steps_block(getattr(workflow, "workflow_definition", None))
        or "## Your steps\n(none written yet)",
        await _reading("Your schedule", schedules_block(organization_id, workflow.id)),
        await _reading("What you remember", memory_block(organization_id, workflow.id)),
        await _reading("Your skills", skills_block(organization_id, workflow.id)),
        notes_block(notes),
    ]
    return "\n\n".join(part for part in parts if part)

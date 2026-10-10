"""Skills by chat: use one, put one on an agent, remember a way, note a fix.

skills-and-context.md: people can say "Use this skill", "Add it to this
agent" or "Remember this as my way of doing it". Four of Decibyl's own tools,
offered only while ``evolve_skills`` is on for the workspace:

* ``use_skill`` -- a read: the skill's procedure as this workspace's active
  version has it, for this turn.
* ``add_skill_to_agent`` -- a card, never a silent change: "Add X to Y?"
  with Add and Not now.
* ``remember_my_way`` -- the editable draft from this conversation
  (``remember``).
* ``note_correction`` -- "no, do it like this": recorded against the
  person's own message as the evidence (``experience.note_correction``).
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services import evolve
from api.services.evolve import experience, remember, versions

USE_TOOL = "use_skill"
ADD_TOOL = "add_skill_to_agent"
REMEMBER_TOOL = "remember_my_way"
CORRECTION_TOOL = "note_correction"

NAMES = frozenset({USE_TOOL, ADD_TOOL, REMEMBER_TOOL, CORRECTION_TOOL})
#: Answered in the turn; the model keeps its tools afterwards.
READS = frozenset({USE_TOOL, CORRECTION_TOOL})

#: How much of a skill's procedure one turn carries.
MAX_PROCEDURE_CHARS = 6_000

RULES = (
    f"- {USE_TOOL}: when the person says to use one of the workspace's skills "
    "for this, read its procedure and follow it in your answer.\n"
    f"- {ADD_TOOL}: when they ask to add a skill to an agent; it puts a card "
    "on the thread and nothing changes until they press Add.\n"
    f"- {REMEMBER_TOOL}: when they say 'remember this as my way of doing it' "
    "after something went well; it drafts a skill they can edit and save.\n"
    f"- {CORRECTION_TOOL}: when they correct how something was done ('no, do "
    "it like this'), record the correction in one line in their words; name "
    "the skill if it was one. Never for your own view of how it went.\n"
)


def enabled(organization_id: int | None) -> bool:
    return evolve.enabled(organization_id)


def rules(organization_id: int | None) -> str:
    return RULES if enabled(organization_id) else ""


def schemas(organization_id: int | None) -> list[dict[str, Any]]:
    if not enabled(organization_id):
        return []
    return [
        {
            "name": USE_TOOL,
            "description": "Read one of this workspace's skills to follow it now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "description": "The skill's name."}
                },
                "required": ["skill"],
            },
        },
        {
            "name": ADD_TOOL,
            "description": "Propose putting a skill on one agent, as a card.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "description": "The skill's name."},
                    "agent": {"type": "string", "description": "The agent's name."},
                },
                "required": ["skill", "agent"],
            },
        },
        {
            "name": REMEMBER_TOOL,
            "description": (
                "Draft a reusable skill from this conversation, as the person's "
                "way of doing it. They edit and save it from the card."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "A short name, if they gave one.",
                    }
                },
            },
        },
        {
            "name": CORRECTION_TOOL,
            "description": "Record a correction the person just gave.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "What should be done differently, in one line.",
                    },
                    "skill": {"type": "string", "description": "The skill, if any."},
                },
                "required": ["instruction"],
            },
        },
    ]


async def _find_skill(organization_id: int, name: str) -> Any | None:
    """An installed skill by slug or title, case-insensitive."""
    from api.services.workflow.skill_context import installed_for

    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for entry in await installed_for(organization_id):
        if wanted in (entry.slug.lower(), (entry.title or "").lower()):
            return entry
    for entry in await installed_for(organization_id):
        if wanted in (entry.title or "").lower():
            return entry
    return None


async def _find_agent(organization_id: int, name: str) -> Any | None:
    wanted = (name or "").strip().lower().lstrip("@")
    if not wanted:
        return None
    rows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    for row in rows:
        handle = (getattr(row, "handle", None) or "").lower()
        if wanted in (row.name.lower(), handle):
            return row
    return None


async def run(
    name: str,
    *,
    organization_id: int,
    user_id: int | None,
    arguments: dict[str, Any],
    thread_id: str | None = None,
) -> dict[str, Any]:
    if not enabled(organization_id):
        return {"status": "unavailable", "reason": "no such tool"}
    if name == USE_TOOL:
        entry = await _find_skill(organization_id, str(arguments.get("skill") or ""))
        if entry is None:
            return {"status": "not_found", "reason": "No installed skill by that name."}
        body = (entry.skill.body or "")[:MAX_PROCEDURE_CHARS]
        return {"status": "success", "skill": entry.title, "procedure": body}
    if user_id is None:
        return {"status": "unavailable", "reason": "Only a person can ask for this."}
    if name == ADD_TOOL:
        entry = await _find_skill(organization_id, str(arguments.get("skill") or ""))
        if entry is None:
            from api.services.skills import catalogue

            entry = catalogue.get(str(arguments.get("skill") or ""))
        agent = await _find_agent(organization_id, str(arguments.get("agent") or ""))
        if entry is None or agent is None:
            return {
                "status": "not_proposed",
                "reason": "Name a skill and an agent in this workspace.",
            }
        await _attach_card(organization_id, entry, agent, thread_id)
        return {
            "status": "proposed",
            "note": (
                f"A card to add {entry.title} to {agent.name} is on the thread. "
                "Tell them, then end your reply."
            ),
        }
    if name == REMEMBER_TOOL:
        return await remember.draft(
            organization_id=organization_id,
            user_id=user_id,
            thread_id=thread_id,
            title_hint=str(arguments.get("title") or "")[:120],
        )
    if name == CORRECTION_TOOL:
        slug = None
        if arguments.get("skill"):
            entry = await _find_skill(organization_id, str(arguments["skill"]))
            slug = entry.slug if entry is not None else None
        return await experience.note_correction(
            organization_id=organization_id,
            user_id=user_id,
            instruction=str(arguments.get("instruction") or ""),
            skill_slug=slug,
            thread_id=thread_id,
        )
    return {"status": "unavailable", "reason": "no such tool"}


async def _attach_card(
    organization_id: int, entry: Any, agent: Any, thread_id: str | None
) -> int | None:
    from api.services.workflow import agent_timeline

    return await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.SKILL_LESSON.value,
        actor=AgentEventActor.AGENT.value,
        summary=f"Add {entry.title} to {agent.name}?",
        payload={
            "type": versions.CARD_ATTACH,
            "slug": entry.slug,
            "title": entry.title,
            "workflow_id": agent.id,
            "agent_name": agent.name,
        },
        in_channel=False,
        thread_id=thread_id,
    )


__all__ = [
    "ADD_TOOL",
    "CORRECTION_TOOL",
    "NAMES",
    "READS",
    "REMEMBER_TOOL",
    "RULES",
    "USE_TOOL",
    "enabled",
    "rules",
    "run",
    "schemas",
]

"""Does the caller know a call may be monitored?

Listening in on a call is monitoring. Whether a business must say so
depends on where it and its callers are; what Decibyl can do is notice
when the opening of the call -- the AI line, the recording line and the
greeting, in the order a caller hears them -- says nothing about it, and
show one line in the listen panel with a fix beside it.

The fix is a proposal, never an edit: it drafts the start step's greeting
with a monitoring sentence added and posts the same publish-or-discard card
an agent's own edits use (``self_edit``), which the panel shows in place.
Nothing reaches a caller until a person presses Publish.

The check reads the definition the call is running, which is what this
caller heard; the proposal starts from the agent's draft, which is what the
next publish ships.
"""

from __future__ import annotations

import re
from typing import Any

from api import constants
from api.db import db_client
from api.enums import AgentEventKind
from api.services.workflow import self_edit

#: Words that tell a caller somebody may be listening. "Recorded for
#: quality and training" does not: a recording is not a person on the line.
MONITORING_WORDS = re.compile(
    r"\b(monitor\w*|listen\w*|supervis\w*|overhear\w*)\b", re.IGNORECASE
)
#: What the proposal adds to the greeting.
MONITORING_SENTENCE = "This call may be monitored by our team."
WARNING = "Callers aren't told calls may be monitored — add it to your greeting"
START = "startCall"


def _start_data(definition: dict[str, Any] | None) -> dict[str, Any] | None:
    for node in (definition or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == START:
            return node.get("data") or {}
    return None


def opening_text(definition: dict[str, Any] | None) -> str:
    """What a caller hears first, as text: the same three parts, with the
    same platform defaults, ``PipecatEngine`` speaks."""
    data = _start_data(definition)
    if data is None:
        return ""
    parts: list[str] = []
    ai_on = data.get("ai_disclosure_enabled")
    if ai_on is None:
        ai_on = constants.AI_DISCLOSURE_ENABLED
    if ai_on:
        parts.append(
            str(data.get("ai_disclosure") or "").strip() or constants.AI_DISCLOSURE_TEXT
        )
    rec_on = data.get("recording_disclosure_enabled")
    if rec_on is None:
        rec_on = constants.RECORDING_DISCLOSURE_ENABLED
    if rec_on:
        parts.append(
            str(data.get("recording_disclosure") or "").strip()
            or constants.RECORDING_DISCLOSURE_TEXT
        )
    if data.get("greeting_type") != "audio":
        parts.append(str(data.get("greeting") or "").strip())
    return " ".join(p for p in parts if p)


def mentions_monitoring(definition: dict[str, Any] | None) -> bool:
    return bool(MONITORING_WORDS.search(opening_text(definition)))


def notice(definition: dict[str, Any] | None) -> dict[str, Any]:
    """What the listen panel shows: nothing, or the warning and its fix."""
    if mentions_monitoring(definition):
        return {"mentions_monitoring": True, "warning": None}
    return {"mentions_monitoring": False, "warning": WARNING}


def proposed_greeting(current: str | None) -> str:
    current = (current or "").strip()
    if not current:
        return MONITORING_SENTENCE
    joiner = "" if current.endswith((".", "!", "?", "।")) else "."
    return f"{current}{joiner} {MONITORING_SENTENCE}"


async def propose_fix(*, organization_id: int, workflow_id: int) -> dict[str, Any]:
    """Draft the greeting with the sentence and post the card. Returns
    ``{"status": "proposed", "event_id": ...}`` or ``{"status":
    "not_proposed", "reason": ...}``; never changes what callers hear."""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        return {"status": "not_proposed", "reason": "This agent could not be found."}
    definition = workflow.workflow_definition or {}
    start = next(
        (
            n
            for n in definition.get("nodes") or []
            if isinstance(n, dict) and n.get("type") == START
        ),
        None,
    )
    if start is None:
        return {
            "status": "not_proposed",
            "reason": "This agent has no start step to add the sentence to.",
        }
    data = start.get("data") or {}
    if data.get("greeting_type") == "audio":
        return {
            "status": "not_proposed",
            "reason": (
                "This agent's greeting is a recording, so the sentence cannot "
                "be added to it as text. Say it in the recording instead."
            ),
        }
    result = await self_edit.propose(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_run_id=None,
        arguments={
            "step": str(start.get("id") or ""),
            "new_greeting": proposed_greeting(data.get("greeting")),
            "why": "Tell callers the call may be monitored, before anyone listens in.",
        },
    )
    if result.get("status") != "proposed":
        return result
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kinds=[AgentEventKind.EDIT_PROPOSED.value],
        limit=1,
    )
    return {"status": "proposed", "event_id": rows[0].id if rows else None}

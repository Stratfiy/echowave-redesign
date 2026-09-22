"""Telling the person on the phone that they are speaking with an AI (FD-1).

The same shape as the recording disclosure, deliberately: a line spoken in
the agent's first turn, per agent, on by default, where *omission cannot
switch it off*. A workflow built before this existed, or by someone who
never scrolled to the setting, still discloses. Only a deliberate ``False``
opts out -- and opting out is a decision with a name on it.

Two rules carry this module.

**Switching it off names the law.** EU AI Act Article 50 (in force since
2 August 2026) requires that a person interacting with an AI system is told
so; the FCC ruled in 2024 that AI-generated voices are "artificial" under
the TCPA, which brings consent and disclosure duties on calls into the US.
An agent may still be run without the line -- a business whose callers
are all its own staff, a jurisdiction with no such duty -- but the person
switching it off acknowledges those jurisdictions, and the switch is a
row in Activity with their name on it.

**The evidence is the call.** Like the recording disclosure, no separate
consent record is written: the line is spoken into the call, so it is in
the recording and the transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.services.workflow.errors import ItemKind, WorkflowError

ENABLED_FIELD = "ai_disclosure_enabled"
TEXT_FIELD = "ai_disclosure"
ACKNOWLEDGED_FIELD = "ai_disclosure_opt_out_acknowledged"
START_NODE_TYPE = "startCall"


@dataclass(frozen=True)
class Jurisdiction:
    name: str
    rule: str


#: Where a call from an AI without saying so is unlawful, as of 21 Sept 2026.
#: Named to the person switching the line off, and written into the
#: Activity row that records it.
JURISDICTIONS: tuple[Jurisdiction, ...] = (
    Jurisdiction(
        "European Union", "AI Act, Article 50 (transparency; in force 2 August 2026)"
    ),
    Jurisdiction(
        "United States",
        "TCPA as read by the FCC's 2024 ruling on AI-generated voices; "
        "state bot-disclosure laws such as California's",
    ),
)


def jurisdictions_line() -> str:
    return "; ".join(f"{j.name}: {j.rule}" for j in JURISDICTIONS)


def _start_nodes(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
    nodes = (definition or {}).get("nodes") or []
    return [
        n for n in nodes if isinstance(n, dict) and n.get("type") == START_NODE_TYPE
    ]


def is_off(data: dict[str, Any] | None) -> bool:
    """A deliberate opt-out. None is the platform default, which is on."""
    return (data or {}).get(ENABLED_FIELD) is False


def is_acknowledged(data: dict[str, Any] | None) -> bool:
    return (data or {}).get(ACKNOWLEDGED_FIELD) is True


def problems(definition: dict[str, Any] | None) -> list[WorkflowError]:
    """The start nodes that switch the line off without acknowledging where
    that is unlawful. Empty when there is nothing to say."""
    out: list[WorkflowError] = []
    for node in _start_nodes(definition):
        data = node.get("data") or {}
        if is_off(data) and not is_acknowledged(data):
            out.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=str(node.get("id") or ""),
                    field=f"data.{ACKNOWLEDGED_FIELD}",
                    message=(
                        "Switching off the AI-identity line needs an "
                        "acknowledgement that a call from an AI without saying "
                        f"so is unlawful in: {jurisdictions_line()}. Tick the "
                        "acknowledgement, or leave the line on."
                    ),
                )
            )
    return out


def _state(definition: dict[str, Any] | None) -> dict[str, bool | None]:
    """Per start node id: True on, False off, None never set."""
    return {
        str(n.get("id") or ""): (n.get("data") or {}).get(ENABLED_FIELD)
        for n in _start_nodes(definition)
    }


async def note_change(
    *,
    organization_id: int,
    workflow_id: int,
    workflow_name: str,
    user_id: int | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> list[str]:
    """Write the switch to Activity, with the person's name on it.

    A row when the line goes off (the one a regulator asks about) and a row
    when it comes back on. Never raises: a save must not fail over its own
    audit line, and the definition already carries the state.
    """
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline

    was, now = _state(before), _state(after)
    lines: list[str] = []
    for node_id, state in now.items():
        previous = was.get(node_id)
        if state is False and previous is not False:
            lines.append(
                f"AI-identity disclosure switched off on {workflow_name}, "
                f"acknowledging it is unlawful in: {jurisdictions_line()}"
            )
        elif state is not False and previous is False:
            lines.append(f"AI-identity disclosure switched back on for {workflow_name}")
    for line in lines:
        try:
            await agent_timeline.record(
                organization_id=organization_id,
                kind=AgentEventKind.ACTIVITY.value,
                actor=AgentEventActor.HUMAN.value,
                summary=line,
                workflow_id=workflow_id,
                payload={
                    "by": user_id,
                    "setting": ENABLED_FIELD,
                    "jurisdictions": [
                        {"name": j.name, "rule": j.rule} for j in JURISDICTIONS
                    ],
                },
                in_channel=False,
            )
        except Exception as exc:  # noqa: BLE001 - the definition carries the state
            logger.warning("Could not record the AI-disclosure switch: {}", exc)
    return lines

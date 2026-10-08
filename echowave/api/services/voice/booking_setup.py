"""Let an agent book appointments, from Chat, on one card (flag ``call_appointment``).

Found building a clinic receptionist end to end (October 2026): the owner
built the receptionist in Chat and asked Decibyl to "let it book
appointments, 30 minutes, Monday to Saturday 10 to 7". Decibyl had no way to
do it. Booking is three settings on two screens -- the booking policy
(Settings, Voice, Calls), the agent's hours (the agent's own settings) and
the booking tool on the agent's steps -- and the product's own rule is that
nobody is sent to another screen to finish something (AGENTS.md).

``set_up_booking`` proposes one card that shows exactly what Confirm sets:
which agent, book or only suggest, the hours by day, the appointment length,
the services, and who callers are handed to. Confirm (by a workspace admin,
as on the Settings screen) saves the policy with its revision, puts the
booking tool on the agent's talking steps and the hours in the agent's
draft. A draft, like every change made for an owner: the tester uses it at
once, live calls once the agent is published -- and the card says so.
"""

from __future__ import annotations

import re
from typing import Any

from api.db import db_client
from api.services.voice import appointments

TOOL_NAME = "set_up_booking"

RULES = (
    "- set_up_booking: when the person wants an agent to book appointments "
    "(or only to suggest times), propose this one card with the agent, the "
    "opening hours by day, the appointment length, the services and who to "
    "hand callers to. Ask for the hours if you do not have them; never "
    "invent them. Confirm saves all of it; say the tester can use it now "
    "and live calls once the agent is published.\n"
)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_WINDOWS = 21


class BookingSetupError(ValueError):
    """Words for the model: what is missing or wrong."""


def enabled(organization_id: int | None) -> bool:
    return appointments.enabled(organization_id)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Propose one card that lets an agent book appointments on calls: "
            "the booking setting, the agent's opening hours and the booking "
            "tool on its steps. Nothing changes until a person confirms."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "The agent, by @handle or name.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["book", "suggest"],
                    "description": "'book' confirms a time on the call; 'suggest' only offers times.",
                },
                "hours": {
                    "type": "array",
                    "description": "When appointments can be booked, as the person said it.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "days": {
                                "type": "array",
                                "items": {"type": "string", "enum": list(DAYS)},
                            },
                            "open": {
                                "type": "string",
                                "description": "HH:MM, 24-hour.",
                            },
                            "close": {
                                "type": "string",
                                "description": "HH:MM, 24-hour.",
                            },
                        },
                        "required": ["days", "open", "close"],
                    },
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "10 to 240, in fives.",
                },
                "services": {"type": "array", "items": {"type": "string"}},
                "escalate_to": {
                    "type": "string",
                    "description": "A phone number callers are handed to, if the person gave one.",
                },
                "why": {"type": "string", "description": "One line, for the card."},
            },
            "required": ["agent", "hours"],
        },
    }


def _windows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise BookingSetupError(
            "Ask for the opening hours (which days, from when to when) first."
        )
    slots: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start, end = str(item.get("open") or ""), str(item.get("close") or "")
        if not _TIME.match(start) or not _TIME.match(end) or start >= end:
            raise BookingSetupError(
                f"Hours must be HH:MM, opening before closing (got {start}-{end})."
            )
        days = [str(d).lower()[:3] for d in item.get("days") or []]
        if not days or any(d not in DAYS for d in days):
            raise BookingSetupError("Say which days, Monday to Sunday.")
        for d in dict.fromkeys(days):
            slots.append(
                {
                    "day_of_week": DAYS.index(d),
                    "start_time": start.zfill(5),
                    "end_time": end.zfill(5),
                }
            )
    if not slots or len(slots) > MAX_WINDOWS:
        raise BookingSetupError("Give the opening hours, at most three windows a day.")
    return sorted(slots, key=lambda s: (s["day_of_week"], s["start_time"]))


def describe_hours(slots: list[dict[str, Any]]) -> str:
    lines = []
    for slot in slots:
        lines.append(
            f"{DAY_NAMES[slot['day_of_week']]} {slot['start_time']}-{slot['end_time']}"
        )
    return "; ".join(lines)


async def resolve(*, organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The card: exactly what Confirm sets. Raises BookingSetupError."""
    from api.services.workflow import office

    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    wanted = str(arguments.get("agent") or "")
    agent = office._match_bot(wanted, list(roster))
    if agent is None:
        raise BookingSetupError(
            f"No agent called {wanted!r} here; use its exact @handle."
        )
    mode = str(arguments.get("mode") or "book")
    if mode not in ("book", "suggest"):
        raise BookingSetupError("Mode is 'book' or 'suggest'.")
    slots = _windows(arguments.get("hours"))
    policy: dict[str, Any] = {"booking": mode, "call_workflow_id": int(agent.id)}
    if arguments.get("duration_minutes") is not None:
        policy["duration_minutes"] = int(arguments["duration_minutes"])
    if arguments.get("services"):
        policy["services"] = [
            str(s).strip() for s in arguments["services"] if str(s).strip()
        ]
    if arguments.get("escalate_to"):
        policy["escalate_to"] = str(arguments["escalate_to"]).strip()
    # Checked now, so a card that could not be saved is never shown.
    try:
        policy = appointments.validate_policy(policy)
    except appointments.PolicyInvalid as exc:
        raise BookingSetupError(str(exc)) from exc
    lines = [
        f"Agent: {agent.name}",
        "Calls: "
        + ("book the appointment" if mode == "book" else "suggest times only"),
        f"Hours: {describe_hours(slots)}",
    ]
    if "duration_minutes" in policy:
        lines.append(f"Each appointment: {policy['duration_minutes']} minutes")
    if policy.get("services"):
        lines.append("Services: " + ", ".join(policy["services"]))
    lines.append(
        f"Hand callers to: {policy['escalate_to']}"
        if policy.get("escalate_to")
        else "Hand callers to: the team is told on this thread and calls back"
    )
    return {
        "args": {
            "workflow_id": int(agent.id),
            "policy": policy,
            "schedule": {"enabled": True, "timezone": "Asia/Kolkata", "slots": slots},
        },
        "label": f"Let {agent.name} {'book' if mode == 'book' else 'suggest'} appointments",
        "preview": "\n".join(lines),
        "effect": (
            "Saves the booking setting, the agent's hours and its booking tool. "
            "The tester uses them at once; live calls once the agent is published."
        ),
        "reversible": False,
    }


async def execute(*, organization_id: int, payload: dict[str, Any]) -> str:
    """Do what the card showed. Only a workspace admin may (as on Settings)."""
    from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole

    args = payload.get("args") or {}
    confirmer = int((payload.get("confirmed") or {}).get("by") or 0)
    membership = (
        await db_client.get_membership(confirmer, organization_id)
        if confirmer
        else None
    )
    rank = ORGANIZATION_ROLE_RANK.get(getattr(membership, "role", "") or "", -1)
    if rank < ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]:
        raise BookingSetupError("Only a workspace admin can switch booking on.")
    workflow_id = int(args["workflow_id"])
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise BookingSetupError("That agent is not here any more.")
    current = await appointments.get_policy(organization_id)
    await appointments.save_policy(
        organization_id,
        dict(args.get("policy") or {}),
        revision=current["revision"],
        user_id=confirmer,
    )
    tool_uuid = await appointments.ensure_tool(
        organization_id=organization_id, user_id=confirmer
    )
    if tool_uuid:
        await appointments.attach_to_agent(
            organization_id=organization_id,
            workflow_id=workflow_id,
            tool_uuid=tool_uuid,
        )
    # The hours into the agent's draft, beside whatever else it holds.
    from api.services.workflow import agent_hours

    draft = await db_client.get_draft_version(workflow_id)
    configurations = dict(
        (draft.workflow_configurations if draft is not None else None)
        or await db_client.get_released_configurations(workflow)
        or workflow.workflow_configurations
        or {}
    )
    configurations[agent_hours.CONFIG_KEY] = args["schedule"]
    await db_client.save_workflow_draft(
        workflow_id, workflow_configurations=configurations
    )
    return (
        f"{workflow.name} can now {'book' if args['policy'].get('booking') == 'book' else 'suggest'} "
        "appointments. Try it in the tester now; publish it for live calls."
    )

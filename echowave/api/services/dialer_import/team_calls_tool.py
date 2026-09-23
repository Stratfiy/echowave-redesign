"""The telecaller coach's one tool: read the team's imported calls (CR-3).

A built-in tool in the web tool's pattern -- one row per workspace, made on
hire, dispatched by category in ``CustomToolManager`` -- and read-only: it
lists transcribed calls from ``imported_calls`` for the run's own
organization, grouped by who made them. It offers nothing while
DIALER_IMPORT_ENABLED is off, so a row made while it was on goes quiet
rather than failing when it is switched off.

Bounded, because a transcript is long and a model's context is not: the
most recent calls first, each transcript cut to a length that still holds a
greeting, the questions and the close.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from api.enums import ToolCategory, ToolStatus
from api.services import features

TOOL_NAME = "read_team_calls"
DISPLAY_NAME = "Team calls"
DESCRIPTION = (
    "Read the calls your team made and took on your connected dialer, "
    "transcribed, grouped by caller."
)
MAX_DAYS = 7
MAX_CALLS = 80
MAX_TRANSCRIPT_CHARS = 6000


def enabled() -> bool:
    return features.is_on("dialer_import")


def is_team_calls_tool(tool: Any) -> bool:
    return getattr(tool, "category", None) == ToolCategory.TEAM_CALLS.value


def function_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Read the team's recorded calls from the connected dialer, "
                "transcribed and grouped by the telecaller who made or took "
                "them. Transcripts have no speaker labels: tell the "
                "telecaller from the customer by what is said."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": (
                            "How many days back to read, 1 for today's "
                            f"evening notes, 7 for the weekly board (max {MAX_DAYS})."
                        ),
                    }
                },
                "required": [],
            },
        },
    }


async def ensure_tool(*, organization_id: int, user_id: int) -> str | None:
    if not enabled():
        return None
    from api.db import db_client

    existing = await db_client.get_tools_for_organization(
        organization_id,
        status=ToolStatus.ACTIVE.value,
        category=ToolCategory.TEAM_CALLS.value,
    )
    for row in existing:
        return str(row.tool_uuid)
    created = await db_client.create_tool(
        organization_id=organization_id,
        user_id=user_id,
        name=DISPLAY_NAME,
        definition={"schema_version": 1, "type": "team_calls"},
        category=ToolCategory.TEAM_CALLS.value,
        description=DESCRIPTION,
        icon="phone-call",
        icon_color="#0F766E",
    )
    return str(created.tool_uuid)


def caller_of(call) -> str:
    return call.agent_name or call.agent_number or "Unknown caller"


async def read(
    session,
    *,
    organization_id: int,
    arguments: dict | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The calls, grouped by caller, for one organization only."""
    from api.services.dialer_import.importer import calls_for

    try:
        days = int((arguments or {}).get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(days, MAX_DAYS))
    now = now or datetime.now(timezone.utc)
    calls = await calls_for(
        session, organization_id=organization_id, since=now - timedelta(days=days)
    )
    calls = calls[-MAX_CALLS:]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        grouped[caller_of(call)].append(
            {
                "call_id": call.id,
                "started_at": call.started_at.isoformat() if call.started_at else None,
                "duration_seconds": call.duration_seconds,
                "direction": call.direction,
                "caller_number": call.agent_number,
                "transcript": (call.transcript or "")[:MAX_TRANSCRIPT_CHARS],
            }
        )
    if not grouped:
        return {
            "days": days,
            "callers": [],
            "note": (
                "No transcribed calls in this window. Say so; never invent a "
                "call or a score."
            ),
        }
    return {
        "days": days,
        "callers": [
            {"caller": caller, "call_count": len(items), "calls": items}
            for caller, items in grouped.items()
        ],
    }

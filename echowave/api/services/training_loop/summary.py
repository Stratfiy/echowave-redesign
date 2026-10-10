"""The counts the agent page's "Learning" line shows.

Counts only: how many of this agent's suggestions the owner approved and how
many they rejected this week, and whether the workspace's setting is on. No
text, no prices. "This week" runs from Monday 00:00 India time, the same
calendar the rest of the product keeps its days on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from api.db import db_client
from api.services import training_loop as loop
from api.services.training_loop import consent

IST = ZoneInfo("Asia/Kolkata")


def week_start(now: datetime | None = None) -> datetime:
    """Monday 00:00 IST of the week ``now`` falls in, as a UTC time."""
    local = (now or datetime.now(UTC)).astimezone(IST)
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.astimezone(UTC)


async def agent_summary(
    *, organization_id: int, workflow_id: int, now: datetime | None = None
) -> dict[str, int | bool]:
    counts = await db_client.count_learning_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        event_types=(*loop.ACCEPTED_TYPES, loop.REJECTED),
        since=week_start(now),
    )
    return {
        "approved_this_week": sum(counts.get(t, 0) for t in loop.ACCEPTED_TYPES),
        "rejected_this_week": counts.get(loop.REJECTED, 0),
        "use_feedback": await consent.use_feedback(organization_id),
    }

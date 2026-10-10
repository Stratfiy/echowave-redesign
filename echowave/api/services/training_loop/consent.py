""" "Use my feedback to improve my agents": the workspace's choice.

On by default, because the data is the workspace's own and goes nowhere else:
it is never pooled with another workspace's, and nothing trained on it is
used outside it. A workspace that does not want its owners' approvals and
edits kept as training data switches it off, and from then on:

* a new event is written with no words in it (what happened, to which agent,
  when, which model: the facts an agent's history needs to be true), and
* the words already kept are cleared from every row the workspace has, so
  "off" means off for the past as well as the future.

Turning it back on keeps new events from then on; the cleared words do not
come back, because they are gone.

The change is written to the workspace's audit log with the person's name.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.training_loop import DECLINED, GRANTED
from api.services.workflow import audit_log

KEY = OrganizationConfigurationKey.TRAINING_LOOP.value
#: What a workspace that has never chosen gets.
DEFAULT_USE_FEEDBACK = True


async def use_feedback(organization_id: int) -> bool:
    value = await db_client.get_configuration_value(organization_id, KEY, None)
    if isinstance(value, dict) and isinstance(value.get("use_feedback"), bool):
        return value["use_feedback"]
    return DEFAULT_USE_FEEDBACK


async def state(organization_id: int) -> str:
    return GRANTED if await use_feedback(organization_id) else DECLINED


async def set_use_feedback(
    *, organization_id: int, user: Any, allowed: bool
) -> dict[str, Any]:
    """Make the choice. Returns ``{"use_feedback", "cleared"}``: the setting
    now, and how many rows had their words taken out by this change."""
    allowed = bool(allowed)
    before = await use_feedback(organization_id)
    # The setting first, then the clearing, so an event written in between
    # sees "off" and keeps no words. An event that read "on" a moment before
    # this change can still land just after the clearing; switching off
    # again clears it.
    await db_client.upsert_configuration(
        organization_id, KEY, {"use_feedback": allowed}
    )
    cleared = 0
    if not allowed:
        cleared = await db_client.clear_learning_event_text(organization_id)
    if before != allowed:
        name = str(getattr(user, "email", "") or "").split("@")[0].strip()
        await audit_log.record(
            organization_id,
            action="training_loop_consent",
            subject_kind="workspace",
            subject_id=organization_id,
            subject="Use my feedback to improve my agents",
            actor_user_id=getattr(user, "id", None),
            actor=name or f"Member {getattr(user, 'id', '')}",
            before={"use_feedback": before},
            after={"use_feedback": allowed, "cleared": cleared},
        )
    return {"use_feedback": allowed, "cleared": cleared}

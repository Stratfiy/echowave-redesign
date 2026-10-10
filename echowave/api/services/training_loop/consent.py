""" "Use my feedback to improve my agents": the workspace's choice.

On by default, because the data is the workspace's own and goes nowhere else:
it is never pooled with another workspace's, and nothing trained on it is
used outside it. A workspace that does not want its owners' approvals and
edits kept as training data has two ways to say so, and they are different:

* **Stop collecting.** Nothing new is written from then on. What was already
  kept stays, exportable and usable, exactly as it was.
* **Stop and delete.** The same, and every row already kept is *archived*
  (``archived_at``): excluded from export, counts and training. Archived rows
  are not hard-deleted; they are kept only as long as the law needs
  (``training_loop.ARCHIVE_RETENTION_DAYS``) and are not read for anything.

Turning it back on collects from then on. Archived rows stay archived.

The change is written to the workspace's audit log with the person's name.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.training_loop import (
    ARCHIVE_RETENTION_DAYS,
    DECLINED,
    GRANTED,
)
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
    *, organization_id: int, user: Any, allowed: bool, delete_past: bool = False
) -> dict[str, Any]:
    """Make the choice. Returns ``{"use_feedback", "archived"}``: the setting
    now, and how many rows this change archived.

    ``delete_past`` is "Stop and delete" and only means something when
    ``allowed`` is False; with it on, asking to archive is a mistake the
    caller should hear about, not a quiet no-op.
    """
    allowed = bool(allowed)
    if allowed and delete_past:
        raise ValueError("Only a workspace that stops collecting can delete.")
    before = await use_feedback(organization_id)
    # The setting first, then the archiving, so an event written in between
    # sees "off" and writes nothing. An event that read "on" a moment before
    # this change can still land just after the archiving; choosing "Stop and
    # delete" again archives it.
    await db_client.upsert_configuration(
        organization_id, KEY, {"use_feedback": allowed}
    )
    archived = 0
    if delete_past:
        archived = await db_client.archive_learning_events(organization_id)
    if before != allowed or archived:
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
            after={
                "use_feedback": allowed,
                "choice": (
                    "stop_and_delete"
                    if delete_past
                    else ("stop_collecting" if not allowed else "on")
                ),
                "archived": archived,
                "retention_days": ARCHIVE_RETENTION_DAYS if delete_past else None,
            },
        )
    return {"use_feedback": allowed, "archived": archived}

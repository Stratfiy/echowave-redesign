"""Tell a person when a task lands on them (KAN-157).

A task assigned to a member, an agent's report on a task they hold, or a
task of theirs going blocked showed on the board and on the bell -- and
nowhere a person who is not in the product would see it. So each of those
is one notice: a line in the inbox (the bell) and, when it is new there,
one email to the member. The inbox's own dedupe rule -- one row per
(organisation, kind, key) -- is what makes a re-save, a retried job or a
double click post once.

**Posting never raises.** Every caller has already filed, moved or
commented on the task; a mail server that is down must not unwind that.
Same rule as the inbox itself.

**Nobody is told about their own doing.** Assigning a task to yourself,
moving your own task to blocked, commenting on your own card: the board
shows it and the bell stays quiet.
"""

from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.messaging import email
from api.services.notifications import inbox

ASSIGNED = "task_assigned"
REPORTED = "task_reported"
BLOCKED = "task_blocked"
KINDS = (ASSIGNED, REPORTED, BLOCKED)


def _link(task: Any) -> str:
    return f"/tasks/{task.id}"


def _holder(task: Any) -> int | None:
    """The person to tell: whoever holds the task, else whoever filed it."""
    user_id = getattr(task, "assignee_user_id", None)
    if user_id:
        return int(user_id)
    created_by = getattr(task, "created_by", None)
    return int(created_by) if created_by else None


async def _notify(
    *,
    organization_id: int,
    user_id: int,
    kind: str,
    dedupe_key: str,
    title: str,
    body: str,
    link: str,
) -> bool:
    """One notice to one member: the bell, then the mail if the bell was new.
    Returns whether anything was posted. Never raises."""
    try:
        is_new = await inbox.post(
            organization_id=organization_id,
            kind=kind,
            dedupe_key=dedupe_key,
            title=title,
            body=body,
            link=link,
        )
        if not is_new:
            return False
        user = await db_client.get_user_by_id(user_id)
        address = getattr(user, "email", None) if user else None
        if address:
            await email.send_email(
                to=address,
                subject=title,
                body_text=f"{body}\n\nOpen it: {link}",
            )
        return True
    except Exception as exc:  # noqa: BLE001 - the task is already filed; a notice is best-effort
        logger.warning(
            "Could not send a {} notice for org {}: {}", kind, organization_id, exc
        )
        return False


async def assigned(
    task: Any, *, organization_id: int, by_user_id: int | None = None
) -> bool:
    """A task now sits with a person. Once per task and person."""
    user_id = getattr(task, "assignee_user_id", None)
    if not user_id or int(user_id) == (by_user_id or 0):
        return False
    return await _notify(
        organization_id=organization_id,
        user_id=int(user_id),
        kind=ASSIGNED,
        dedupe_key=f"task:{task.id}:assigned:{int(user_id)}",
        title=f"Task for you: {task.title}",
        body=(getattr(task, "brief", None) or "").strip()
        or "Open the board for what is wanted.",
        link=_link(task),
    )


async def reported(
    task: Any, *, organization_id: int, body: str, agent_name: str | None
) -> bool:
    """An agent wrote on a card a person holds or filed. Once per report."""
    user_id = _holder(task)
    if not user_id:
        return False
    digest = hashlib.sha1(body.strip().encode("utf-8")).hexdigest()[:12]
    who = agent_name or "An agent"
    return await _notify(
        organization_id=organization_id,
        user_id=user_id,
        kind=REPORTED,
        dedupe_key=f"task:{task.id}:reported:{digest}",
        title=f"{who} on {task.title}",
        body=body.strip(),
        link=_link(task),
    )


async def blocked(
    task: Any,
    *,
    organization_id: int,
    by_user_id: int | None = None,
    reason: str | None = None,
) -> bool:
    """A task went to Blocked and somebody must unblock it. Once per task and
    person; a second block after an unblock is a new notice."""
    user_id = _holder(task)
    if not user_id or user_id == (by_user_id or 0):
        return False
    stamp = hashlib.sha1((reason or "").strip().encode("utf-8")).hexdigest()[:8]
    return await _notify(
        organization_id=organization_id,
        user_id=user_id,
        kind=BLOCKED,
        dedupe_key=f"task:{task.id}:blocked:{user_id}:{stamp}",
        title=f"Blocked: {task.title}",
        body=(reason or "").strip()
        or "The task is waiting on something only a person can do.",
        link=_link(task),
    )

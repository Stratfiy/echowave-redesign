"""What the Learning Guide agent may read (the `agents` stream's side).

The `agents` stream owns the Learning Guide's configuration -- its
instructions, tools and voice. This stream owns the learning record. The
seam between them is this module: read-only, one person at a time, short.

* ``context_for`` -- a few lines for the agent's prompt: the person's active
  goals, where each one stands, and what is due. Plain facts from evaluated
  practice; never a level or a percentage the record does not hold.
* ``resume_link`` -- where "Continue practice" opens: the lesson inside Chat.

Writes go through ``core`` (start a goal, submit an attempt), which the
agent reaches through the same routes the screen uses, so a lesson started
by voice or by the agent is the same record as one started on screen. The
agent never marks practice itself: only ``core.submit_attempt`` moves a
skill.

Personal and scoped: a work agent in another workspace gets nothing, because
every read names this organisation and this person (handoff 8: a work agent
must not inherit learning history).
"""

from __future__ import annotations

from datetime import datetime

from api.services.learning import core

#: At most this many goals in a prompt: a context, not a report.
MAX_GOALS = 3


def resume_link(goal_id: str) -> str:
    return f"/overview?learn={goal_id}"


async def context_for(
    organization_id: int, user_id: int, *, now: datetime | None = None
) -> str:
    """A short block for the Learning Guide's prompt, or "" when learning is
    off here or the person has no goals."""
    if not core.enabled(organization_id):
        return ""
    goals = await core.list_goals(organization_id, user_id)
    if not goals:
        return ""
    due = await core.reviews_due(organization_id, user_id, now=now, limit=5)
    lines = ["Learning goals (from evaluated practice only):"]
    for goal in goals[:MAX_GOALS]:
        progress = await core.progress(
            organization_id, user_id, goal["goal_id"], now=now
        )
        practised = sum(1 for s in progress["skills"] if s["status"] == core.PRACTISED)
        again = sum(1 for s in progress["skills"] if s["status"] == core.NEEDS_ANOTHER)
        lines.append(
            f"- {goal['title']} (explain in {goal['explanation_language']}): "
            f"{progress['practice_count']} practice answers marked, "
            f"{practised} skills practised, {again} need another attempt. "
            f"Next: {progress['next_step']['text']} "
            f"Resume: {resume_link(goal['goal_id'])}"
        )
    if due:
        lines.append(
            "Reviews due: "
            + ", ".join(f"{d['skill_name']} ({d['goal_title']})" for d in due)
        )
    return "\n".join(lines)

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


def start_link(topic: str) -> str:
    """Where a course opens when it cannot be started yet: the start of a
    lesson in Chat, with what they named filled in."""
    from urllib.parse import urlencode

    return f"/overview?{urlencode({'learn': 'new', 'topic': topic})}"


async def start_course(
    organization_id: int,
    user_id: int,
    arguments: dict,
    *,
    thread_id: str | None = None,
) -> dict:
    """``start_course`` from Chat: start the person's own learning record and
    put the lesson on the thread they asked in.

    The same ``core.start_goal`` the lesson screen calls, so a course started
    by describing it is the same record -- plan, marked practice, reviews,
    streak, Today -- as one started on screen. Two things only the person
    can say (that they are 18 or older; yes to saving something that looks
    personal) are asked on the lesson itself: the card then opens the start
    with the course filled in, and nothing is saved before they answer.
    """
    from api.enums import AgentEventKind
    from api.services.workflow import agent_timeline

    title = str(arguments.get("title") or "").strip()[: core.MAX_TITLE]
    if not title:
        return {"status": "not_proposed", "reason": "Say which course or skill."}

    async def card(goal_id: str | None, href: str) -> None:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.ACTIVITY.value,
            summary=f"Course: {title}"[:500],
            payload={
                "learning_course": {"goal_id": goal_id, "title": title, "href": href}
            },
            in_channel=False,
        )

    # A course they already have is resumed, not started twice: the card is
    # how a person gets back to it from the conversation.
    for goal in await core.list_goals(organization_id, user_id):
        if goal["title"].casefold() == title.casefold():
            await card(goal["goal_id"], resume_link(goal["goal_id"]))
            return {
                "status": "resumed",
                "goal_id": goal["goal_id"],
                "note": (
                    "They already have this course; the card to continue it is "
                    "on the thread. Say so in one line."
                ),
            }

    try:
        started = await core.start_goal(
            organization_id,
            user_id,
            title=title,
            studying_for=arguments.get("studying_for"),
            material=arguments.get("material"),
            thread_id=thread_id,
        )
    except (core.ProfileNeeded, core.SensitiveDetails):
        await card(None, start_link(title))
        return {
            "status": "needs_confirmation",
            "note": (
                "The course card is on the thread. The person confirms one "
                "thing on it first (that they are 18 or older, or that a "
                "personal detail may be saved), then the course starts. Say so "
                "in one line."
            ),
        }
    except core.LearningError as exc:
        return {"status": "not_proposed", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - said, never raised into the turn
        from api.services.learning import teacher

        if isinstance(exc, teacher.NeedsSetup):
            return {
                "status": "unavailable",
                "reason": "Lessons need a model key on this deployment.",
            }
        raise
    goal_id = started["goal"]["goal_id"]
    await card(goal_id, resume_link(goal_id))
    return {
        "status": "started",
        "goal_id": goal_id,
        "baseline_question": started.get("baseline_question"),
        "note": (
            "Started. The lesson is on the thread as a card: the person answers "
            "this first question there, which sets their level and writes "
            "their plan. Ask them the question and say to answer it on the "
            "card; do not mark answers yourself."
        ),
    }

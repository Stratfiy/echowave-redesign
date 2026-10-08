"""Asking to be called: from the chat, the chip, or Decibyl's tool.

``opt_in`` writes one pending callback for what the person means by "it":

1. a task or a bot named outright (the chip on a task, the tool's
   ``task_id``) -- read with the person's workspace id, refused if it is not
   there;
2. otherwise the work in flight on this conversation: the person's running
   browser session, Decibyl's background task, or a board task they filed in
   the last hour;
3. otherwise the next thing to finish on this conversation.

Then it answers on the thread, where the person is: when they will be
called, or -- with no confirmed number -- a card showing the number to
confirm once, or -- where the workspace cannot place calls -- that they will
be told in the app instead. Never a dead end, never another screen.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from api.db import db_client
from api.db.call_when_done_models import DoneCallbackModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import call_when_done as cwd
from api.services.call_when_done import CallWhenDoneError, NotHere, calls, completion
from api.services.call_when_done import number as numbers

TOOL_NAME = "call_me_when_done"
CHIP_TEXT = "Call me when done"
#: A board task filed this recently by the person is "it" when nothing else is.
RECENT_TASK = timedelta(hours=1)
#: How far back Decibyl's own background tasks are looked for.
LOOKBACK = timedelta(days=1)

RULES = (
    "- call_me_when_done: when the person asks to be rung (called, phoned) "
    "when a task finishes, call it once. Pass `phone_number` only if they "
    "gave one in this conversation, and `task_id` only if they named a "
    "task. It answers on the thread itself; do not repeat what it says. "
    "Never claim a call was made.\n"
)

#: The whole message is the ask: "call me when it's done", "ring me once
#: that's finished", the chip's own words. Anything longer goes to the model,
#: which has the tool, so "research X and call me when done" still does X.
_ASK = re.compile(
    r"^\s*(?:please\s+|pls\s+|ok(?:ay)?[,\s]+)?(?:call|ring|phone)\s+me\s+(?:back\s+)?"
    r"(?:when|once|after)\s+(?:it'?s|it\s+is|that'?s|that\s+is|this\s+is|you'?re|"
    r"you\s+are|it'?s\s+all|everything\s+is)?\s*(?:done|finished|ready|complete)"
    r"(?:\s+please)?\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_the_ask(text: str) -> bool:
    return bool(_ASK.match((text or "").replace("’", "'")))


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Ring the person who asked when the task they handed over "
            "finishes: within calling hours, announced as Decibyl, with the "
            "result. Answers on the thread itself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "The number to ring, only if they gave one.",
                },
                "task_id": {
                    "type": "integer",
                    "description": "The board task they mean, if they named one.",
                },
            },
        },
    }


async def run_tool(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    user_id: int | None,
    thread_id: str | None,
) -> dict[str, Any]:
    if not user_id:
        return {
            "status": "not_done",
            "reason": "Only a signed-in person can be called.",
        }
    try:
        task_id = arguments.get("task_id")
        made = await opt_in(
            organization_id,
            int(user_id),
            thread_id=thread_id,
            task_id=int(task_id) if task_id not in (None, "") else None,
            phone=str(arguments.get("phone_number") or "") or None,
        )
    except (CallWhenDoneError, TypeError, ValueError) as exc:
        return {"status": "not_done", "reason": str(exc)}
    return {
        "status": "ok",
        "note": (
            f"Said on the thread: {made['line']} Do not repeat it; end your reply."
        ),
    }


# --- what "it" is -------------------------------------------------------------


async def _named_task(organization_id: int, task_id: int) -> tuple[str, str]:
    from api.db.models import AgentTaskModel

    async with db_client.async_session() as session:
        row = await session.scalar(
            select(AgentTaskModel).where(
                AgentTaskModel.id == task_id,
                AgentTaskModel.organization_id == organization_id,
            )
        )
    if row is None:
        raise NotHere("That task is not here.")
    return f"task:{row.id}", row.title


async def _named_workflow(organization_id: int, workflow_id: int) -> tuple[str, str]:
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise NotHere("That agent is not here.")
    return f"workflow:{workflow.id}", f"{workflow.name}'s run"


async def in_flight(
    organization_id: int, user_id: int, thread_id: str | None
) -> tuple[str, str] | None:
    """``(subject, title)`` of the work running for this person on this
    conversation, newest first, or None."""
    from api.db.browser_models import BrowserSessionModel
    from api.db.models import AgentTaskModel
    from api.services.browser.session import LIVE
    from api.services.workflow import tasks_board

    open_columns = (tasks_board.BACKLOG, tasks_board.TODO, tasks_board.IN_PROGRESS)
    async with db_client.async_session() as session:
        browsing = await session.scalar(
            select(BrowserSessionModel)
            .where(
                BrowserSessionModel.organization_id == organization_id,
                BrowserSessionModel.user_id == user_id,
                BrowserSessionModel.state.in_(LIVE),
                (
                    BrowserSessionModel.thread_id == thread_id
                    if thread_id
                    else BrowserSessionModel.thread_id.is_(None)
                ),
            )
            .order_by(BrowserSessionModel.id.desc())
            .limit(1)
        )
        if browsing is not None:
            return f"browser:{browsing.session_uuid}", browsing.task
        tasks = (
            await session.scalars(
                select(AgentTaskModel)
                .where(
                    AgentTaskModel.organization_id == organization_id,
                    AgentTaskModel.status.in_(open_columns),
                    AgentTaskModel.created_at >= datetime.now(UTC) - LOOKBACK,
                )
                .order_by(AgentTaskModel.id.desc())
                .limit(50)
            )
        ).all()
    recent = datetime.now(UTC) - RECENT_TASK
    for task in tasks:
        state = dict(task.continuation or {})
        if state:
            if state.get("thread_id") == thread_id and state.get("author_id") in (
                None,
                user_id,
            ):
                return f"task:{task.id}", task.title
            continue
        if task.created_by == user_id and task.created_at and task.created_at >= recent:
            return f"task:{task.id}", task.title
    return None


async def _subject(
    organization_id: int,
    user_id: int,
    *,
    thread_id: str | None,
    task_id: int | None,
    workflow_id: int | None,
) -> tuple[str, str]:
    if task_id is not None:
        return await _named_task(organization_id, task_id)
    if workflow_id is not None:
        return await _named_workflow(organization_id, workflow_id)
    running = await in_flight(organization_id, user_id, thread_id)
    if running is not None:
        return running
    return completion.thread_key(thread_id), ""


# --- the ask ------------------------------------------------------------------


async def opt_in(
    organization_id: int,
    user_id: int,
    *,
    thread_id: str | None,
    task_id: int | None = None,
    workflow_id: int | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Write the pending callback and answer on the thread. Raises
    CallWhenDoneError (off, a task that is not here, a bad number)."""
    if not cwd.enabled(organization_id):
        raise CallWhenDoneError("Calls when tasks finish are not switched on here.")
    clean_phone = numbers.clean(phone) if phone else None
    subject, title = await _subject(
        organization_id,
        user_id,
        thread_id=thread_id,
        task_id=task_id,
        workflow_id=workflow_id,
    )
    async with db_client.async_session() as session:
        existing = await session.scalar(
            select(DoneCallbackModel).where(
                DoneCallbackModel.organization_id == organization_id,
                DoneCallbackModel.user_id == user_id,
                DoneCallbackModel.subject == subject,
                DoneCallbackModel.state == cwd.PENDING,
            )
        )
        if existing is None:
            existing = DoneCallbackModel(
                organization_id=organization_id,
                user_id=user_id,
                thread_id=thread_id,
                subject=subject,
                state=cwd.PENDING,
                title=(title or None) and title[:200],
                created_at=datetime.now(UTC),
            )
            session.add(existing)
        await session.commit()
        await session.refresh(existing)
        callback_id = existing.id

    reason, confirmed = await calls.readiness(organization_id, user_id)
    what = f"“{title}”" if title else "it"
    card_event_id = None
    window = numbers.window()
    if reason == "needs_setup":
        line = (
            f"I'll let you know when {what} is done. This workspace has no "
            "phone line for calling out yet, so I'll tell you here and as a "
            "notification instead."
        )
    elif clean_phone and clean_phone != confirmed:
        card_event_id = await numbers.propose(
            organization_id, user_id, clean_phone, thread_id=thread_id
        )
        line = (
            f"I'll call you when {what} is done, between {window}. Confirm the "
            "number on the card and I'll use it from now on."
        )
    elif confirmed:
        line = (
            f"I'll call you on {numbers.masked(confirmed)} when {what} is done, "
            f"between {window}."
        )
    else:
        line = (
            f"I'll call you when {what} is done, between {window}. Which number "
            "should I ring? Type it here and I'll show it on a card for you to "
            "confirm once."
        )
    await _say(
        organization_id,
        user_id,
        thread_id,
        line,
        callback_id=callback_id,
        needs_number=not confirmed and reason != "needs_setup" and not card_event_id,
    )
    return {
        "callback_id": callback_id,
        "subject": subject,
        "title": title,
        "state": cwd.PENDING,
        "line": line,
        "can_call": reason is None or reason == "no_number",
        "reason": reason,
        "number": numbers.masked(confirmed) if confirmed else None,
        "card_event_id": card_event_id,
    }


async def _say(
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    line: str,
    *,
    callback_id: int,
    needs_number: bool,
) -> None:
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=line[:500],
        payload={
            "body": line,
            "from": "Decibyl",
            "private_to": user_id,
            "call_when_done": {
                "callback_id": callback_id,
                "state": cwd.PENDING,
                "needs_number": needs_number,
            },
        },
        in_channel=False,
        thread_id=thread_id,
    )


async def cancel(organization_id: int, user_id: int, callback_id: int) -> bool:
    """Stop waiting. Only the person's own pending callback in this
    workspace; anything else is not here."""
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(DoneCallbackModel)
                .where(
                    DoneCallbackModel.id == callback_id,
                    DoneCallbackModel.organization_id == organization_id,
                    DoneCallbackModel.user_id == user_id,
                    DoneCallbackModel.state == cwd.PENDING,
                )
                .values(state=cwd.CANCELLED)
                .returning(DoneCallbackModel.id)
            )
        ).first()
        await session.commit()
    return bool(moved)


async def status(
    organization_id: int, user_id: int, thread_id: str | None
) -> dict[str, Any]:
    """What the thread shows: the person's pending callbacks here, the
    number on file, whether calls can be placed, and whether to offer the
    chip (work in flight that nobody asked to be called about yet)."""
    async with db_client.async_session() as session:
        pending = (
            await session.scalars(
                select(DoneCallbackModel)
                .where(
                    DoneCallbackModel.organization_id == organization_id,
                    DoneCallbackModel.user_id == user_id,
                    DoneCallbackModel.state == cwd.PENDING,
                    (
                        DoneCallbackModel.thread_id == thread_id
                        if thread_id
                        else DoneCallbackModel.thread_id.is_(None)
                    ),
                )
                .order_by(DoneCallbackModel.id)
            )
        ).all()
    reason, confirmed = await calls.readiness(organization_id, user_id)
    running = await in_flight(organization_id, user_id, thread_id)
    watched = {p.subject for p in pending}
    return {
        "pending": [
            {"id": p.id, "subject": p.subject, "title": p.title or None}
            for p in pending
        ],
        "number": numbers.masked(confirmed) if confirmed else None,
        "can_call": reason is None or reason == "no_number",
        "reason": reason,
        "offer": running is not None and running[0] not in watched,
        "in_flight": running[1] if running else None,
    }


async def chip(
    organization_id: int, user_id: int, thread_id: str | None
) -> dict[str, str] | None:
    """The "Call me when done" chip, while work is running on this
    conversation that the person has not asked to be called about. Its text
    is the ask itself, so tapping it is the same as typing it. Never raises."""
    if not cwd.enabled(organization_id):
        return None
    try:
        offered = (await status(organization_id, user_id, thread_id))["offer"]
    except Exception:  # noqa: BLE001 - a thread with no chip is still a thread
        return None
    return {"kind": "call_when_done", "text": CHIP_TEXT} if offered else None

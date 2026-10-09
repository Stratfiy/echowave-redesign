"""Hearing that something finished, in the one place it is already said.

Every job that finishes later -- a routine run, a board task, Decibyl's own
background continuation, the private browser, a document job, a trigger run
-- already writes its finish to the timeline through
``agent_timeline.record``. That function hands every row it wrote to
:func:`on_recorded`; nothing here is called from a job directly, so a job
type added next month is heard the day it records its finish.

What counts as a finish (``classify``) is read from the row's kind and
payload:

* ``deliverable`` / ``could_not`` -- what a job hands a person, or says it
  could not do (routine runs, board tasks, trigger runs, documents);
* a ``message`` that carries ``task_id`` (Decibyl's background answer) or
  ``browser_session`` (the browser's receipt);
* an ``activity`` that carries a board task in a finished column.

One finish writes more than one row (a board task writes a deliverable and
an activity line); ``finished_key`` names the finish, and a person's
callback is settled by it once (a partial unique index backs that).

Org scoping: a finish only ever settles callbacks in its own workspace, and
the task or browser row it names is read with that workspace's id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.call_when_done_models import DoneCallbackModel
from api.enums import AgentEventKind
from api.services import call_when_done as cwd

#: Kinds that can carry a finish. Anything else leaves before any work.
_KINDS = frozenset(
    {
        AgentEventKind.DELIVERABLE.value,
        AgentEventKind.COULD_NOT.value,
        AgentEventKind.MESSAGE.value,
        AgentEventKind.ACTIVITY.value,
    }
)
#: Board columns that mean the work is back with a person.
_FINISHED_COLUMNS = ("done", "blocked", "in_review")
_NEEDS_A_PERSON = ("blocked", "in_review")

MAX_TITLE = 200
MAX_SUMMARY = 280
MAX_OUTPUT = 4000
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


@dataclass
class Completion:
    #: What finished: ``task:12``, ``browser:<uuid>``, ``run:44``, ``event:9``.
    key: str
    title: str
    summary: str
    output: str
    needs_you: bool
    #: Subjects a callback may be watching for this finish.
    subjects: list[str] = field(default_factory=list)
    #: Conversation subjects (``thread:<id>``): the next thing to finish there.
    threads: list[str] = field(default_factory=list)
    #: Who handed it over, when that is on record.
    requester: int | None = None
    thread_id: str | None = None


def thread_key(thread_id: str | None) -> str:
    return f"thread:{thread_id or 'main'}"


def one_or_two_sentences(text: str) -> str:
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    short = " ".join(_SENTENCE.split(flat)[:2])
    if len(short) > MAX_SUMMARY:
        short = short[: MAX_SUMMARY - 1].rstrip() + "…"
    return short


def _title(text: str) -> str:
    line = " ".join((text or "").split("\n")[0].split())
    return (line[: MAX_TITLE - 1] + "…") if len(line) > MAX_TITLE else line


def classify(
    *,
    kind: str,
    summary: str,
    payload: dict[str, Any],
    workflow_id: int | None,
    workflow_run_id: int | None,
    thread_id: str | None,
    event_id: int | None,
) -> Completion | None:
    """The finish this row records, or None when it records no finish."""
    if kind not in _KINDS or payload.get("call_when_done"):
        # Our own notices are never a finish of their own.
        return None
    task = payload.get("task") if isinstance(payload.get("task"), dict) else None
    decibyl_row = workflow_id is None
    threads = [thread_key(thread_id)] if decibyl_row else []

    if kind == AgentEventKind.MESSAGE.value:
        body = str(payload.get("body") or summary or "")
        if payload.get("browser_session"):
            uuid = str(payload["browser_session"])
            return Completion(
                key=f"browser:{uuid}",
                title="Your browser task",
                summary=one_or_two_sentences(body),
                output=body[:MAX_OUTPUT],
                needs_you=not body.startswith("The browser finished"),
                subjects=[f"browser:{uuid}"],
                threads=threads,
                thread_id=thread_id,
            )
        if payload.get("task_id") and payload.get("from"):
            task_id = int(payload["task_id"])
            return Completion(
                key=f"task:{task_id}",
                title="",
                summary=one_or_two_sentences(body),
                output=body[:MAX_OUTPUT],
                needs_you=False,
                subjects=[f"task:{task_id}"],
                threads=threads,
                thread_id=thread_id,
            )
        return None

    if kind == AgentEventKind.ACTIVITY.value:
        if not task or task.get("status") not in _FINISHED_COLUMNS:
            return None
        result = str(task.get("result") or payload.get("result") or summary)
        return Completion(
            key=f"task:{int(task['id'])}",
            title=_title(str(task.get("title") or "")),
            summary=one_or_two_sentences(result),
            output=result[:MAX_OUTPUT],
            needs_you=task.get("status") in _NEEDS_A_PERSON,
            subjects=[f"task:{int(task['id'])}"],
            threads=threads,
            requester=task.get("created_by"),
            thread_id=thread_id,
        )

    # deliverable / could_not
    could_not = kind == AgentEventKind.COULD_NOT.value
    text = str(payload.get("result") or payload.get("body") or summary or "")
    subjects: list[str] = []
    if task and task.get("id"):
        key = f"task:{int(task['id'])}"
        subjects.append(key)
        title = _title(str(task.get("title") or summary))
        requester = task.get("created_by")
        needs = could_not or task.get("status") in _NEEDS_A_PERSON
    else:
        key = (
            f"run:{workflow_run_id}"
            if workflow_run_id
            else f"event:{event_id}"
            if event_id
            else f"line:{abs(hash((kind, summary))) % 10**12}"
        )
        title = _title(summary)
        requester = None
        needs = could_not
    if workflow_id is not None:
        subjects.append(f"workflow:{workflow_id}")
    return Completion(
        key=key,
        title=title,
        summary=one_or_two_sentences(text),
        output=text[:MAX_OUTPUT],
        needs_you=needs,
        subjects=subjects,
        threads=threads,
        requester=requester,
        thread_id=thread_id if decibyl_row else None,
    )


async def _enrich(organization_id: int, done: Completion) -> None:
    """Who asked, and on which conversation, from the task or browser row
    the finish names -- read with the finish's own workspace id."""
    kind, _, ident = done.key.partition(":")
    try:
        if kind == "task":
            from api.db.models import AgentTaskModel

            async with db_client.async_session() as session:
                row = await session.scalar(
                    select(AgentTaskModel).where(
                        AgentTaskModel.id == int(ident),
                        AgentTaskModel.organization_id == organization_id,
                    )
                )
            if row is None:
                return
            state = dict(row.continuation or {})
            done.title = done.title or _title(row.title)
            done.requester = done.requester or row.created_by or state.get("author_id")
            origin = state.get("thread_id")
            if state:
                # Decibyl's own background task: its conversation is where
                # it was asked, whatever context recorded the finish.
                done.thread_id = origin
                if thread_key(origin) not in done.threads:
                    done.threads.append(thread_key(origin))
            if not done.summary and row.result:
                done.summary = one_or_two_sentences(row.result)
                done.output = row.result[:MAX_OUTPUT]
        elif kind == "browser":
            from api.db.browser_models import BrowserSessionModel

            async with db_client.async_session() as session:
                row = await session.scalar(
                    select(BrowserSessionModel).where(
                        BrowserSessionModel.session_uuid == ident,
                        BrowserSessionModel.organization_id == organization_id,
                    )
                )
            if row is None:
                return
            done.title = _title(row.task) or done.title
            done.requester = row.user_id
            done.thread_id = row.thread_id
            if thread_key(row.thread_id) not in done.threads:
                done.threads.append(thread_key(row.thread_id))
    except Exception as exc:  # noqa: BLE001 - the finish still settles by subject
        logger.warning("call_when_done: could not read {}: {}", done.key, exc)
    done.title = done.title or "Your task"


async def on_recorded(
    *,
    organization_id: int,
    kind: str,
    summary: str,
    payload: dict[str, Any] | None,
    workflow_id: int | None,
    workflow_run_id: int | None,
    thread_id: str | None,
    event_id: int | None,
) -> None:
    """Called by ``agent_timeline.record`` for every row it wrote. Never
    raises: a timeline write must never fail because of a call."""
    if kind not in _KINDS or not cwd.enabled(organization_id):
        return
    try:
        done = classify(
            kind=kind,
            summary=summary,
            payload=dict(payload or {}),
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            event_id=event_id,
        )
        if done is None:
            return
        await settle(organization_id, done)
    except Exception as exc:  # noqa: BLE001 - see above
        logger.warning("call_when_done: could not handle a finish: {}", exc)


async def settle(organization_id: int, done: Completion) -> list[int]:
    """Settle every callback this finish answers and queue their calls.
    Returns the people a call (or notice) was queued for."""
    from api.services.call_when_done import calls

    await _enrich(organization_id, done)
    now = datetime.now(UTC)
    finished = {
        "state": cwd.FINISHED,
        "title": done.title[:MAX_TITLE],
        "summary": done.summary,
        "output": done.output,
        "needs_you": done.needs_you,
        "finished_at": now,
    }
    by_user: dict[int, list[int]] = {}
    threads: dict[int, str | None] = {}
    async with db_client.async_session() as session:
        candidates = (
            await session.execute(
                select(
                    DoneCallbackModel.id,
                    DoneCallbackModel.user_id,
                    DoneCallbackModel.subject,
                    DoneCallbackModel.thread_id,
                )
                .where(
                    DoneCallbackModel.organization_id == organization_id,
                    DoneCallbackModel.state == cwd.PENDING,
                    DoneCallbackModel.subject.in_(done.subjects + done.threads),
                )
                .order_by(DoneCallbackModel.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        already = set(
            (
                await session.scalars(
                    select(DoneCallbackModel.user_id).where(
                        DoneCallbackModel.organization_id == organization_id,
                        DoneCallbackModel.finished_key == done.key,
                    )
                )
            ).all()
        )
        for cb_id, user_id, subject, cb_thread in candidates:
            if (
                subject in done.threads
                and subject not in done.subjects
                and done.requester is not None
                and int(done.requester) != int(user_id)
            ):
                # "The next thing to finish here" is this person's next
                # thing; a colleague's task finishing is not it.
                continue
            primary = user_id not in by_user and user_id not in already
            await session.execute(
                update(DoneCallbackModel)
                .where(
                    DoneCallbackModel.id == cb_id,
                    DoneCallbackModel.organization_id == organization_id,
                )
                .values(finished_key=done.key if primary else None, **finished)
            )
            if user_id in already:
                continue
            by_user.setdefault(user_id, []).append(cb_id)
            threads.setdefault(user_id, cb_thread)
        await session.commit()

    if done.requester and int(done.requester) not in by_user:
        standing_id = await _standing(organization_id, int(done.requester), done, now)
        if standing_id is not None:
            by_user[int(done.requester)] = [standing_id]
            threads[int(done.requester)] = done.thread_id

    for user_id, ids in by_user.items():
        try:
            await calls.queue(
                organization_id, user_id, ids, thread_id=threads.get(user_id)
            )
        except Exception as exc:  # noqa: BLE001 - one person's queue is not all
            # The callbacks are finished but no call holds them: said loudly,
            # never dropped quietly.
            logger.error(
                "call_when_done: could not queue a call for user {} (callbacks {}): {}",
                user_id,
                ids,
                exc,
            )
    return list(by_user)


async def _standing(
    organization_id: int, user_id: int, done: Completion, now: datetime
) -> int | None:
    """The standing preference: a finished callback written for the person
    who handed the task over, once per finish."""
    from api.services import member_preferences

    try:
        prefs = await member_preferences.get(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("call_when_done: could not read preferences: {}", exc)
        return None
    if not prefs.get(member_preferences.CALL_WHEN_DONE):
        return None
    # The person must belong to the workspace whose task finished.
    if await db_client.get_membership(user_id, organization_id) is None:
        return None
    async with db_client.async_session() as session:
        made = (
            await session.execute(
                insert(DoneCallbackModel)
                .values(
                    organization_id=organization_id,
                    user_id=user_id,
                    thread_id=done.thread_id,
                    subject=done.key,
                    standing=True,
                    finished_key=done.key,
                    created_at=now,
                    state=cwd.FINISHED,
                    title=done.title[:MAX_TITLE],
                    summary=done.summary,
                    output=done.output,
                    needs_you=done.needs_you,
                    finished_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "user_id", "finished_key"],
                    index_where=DoneCallbackModel.finished_key.is_not(None),
                )
                .returning(DoneCallbackModel.id)
            )
        ).first()
        await session.commit()
    return made[0] if made else None

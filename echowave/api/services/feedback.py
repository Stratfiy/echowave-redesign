"""Was this useful? Yes / Not quite, on Decibyl's replies and finished tasks.

Handoff 6 ("Useful feedback") and 2: Yes / Not quite, with optional reasons
-- wrong, irrelevant, too late, too long, wrong language -- dismissible, and
stored against the output, model and task versions. It informs evaluation;
it never grants permission to act and never trains on private content.

What is stored is about the judgement, not the content: the output's version
is a hash of the words shown, so an answer edited or regenerated afterwards
is a different output and the old verdict does not follow it. The words
themselves are never copied here, and analytics gets the verdict and reason
codes only (``feedback_submitted``).

Scope: a person can only judge an output in the workspace they are in, and
only one they could see -- a Decibyl reply on a thread that is theirs, or a
task on their workspace's board.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.controls_models import OutputFeedbackModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import events, features

FLAG = "reply_feedback"

YES = "yes"
NOT_QUITE = "not_quite"
VERDICTS = (YES, NOT_QUITE)
REASONS = ("wrong", "irrelevant", "too_late", "too_long", "wrong_language")
REPLY = "reply"
TASK = "task"
SUBJECTS = (REPLY, TASK)


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class FeedbackRefused(ValueError):
    pass


class NotFound(LookupError):
    pass


def version_of(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:32]


def _clean(verdict: str, reasons: list[str] | None) -> list[str]:
    if verdict not in VERDICTS:
        raise FeedbackRefused("Yes or Not quite.")
    reasons = list(dict.fromkeys(reasons or []))
    unknown = [r for r in reasons if r not in REASONS]
    if unknown:
        raise FeedbackRefused("That is not one of the reasons offered.")
    if verdict == YES and reasons:
        raise FeedbackRefused("Reasons go with Not quite.")
    return reasons


async def _reply_subject(
    organization_id: int, event_id: int, viewer_id: int | None
) -> dict[str, Any]:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if (
        event is None
        or event.kind != AgentEventKind.MESSAGE.value
        or event.actor != AgentEventActor.AGENT.value
    ):
        raise NotFound("That reply is not here.")
    if (
        constants.DECIBYL_PRIVATE_THREADS_ENABLED
        and event.workflow_id is None
        and event.folder_id is None
    ):
        # A private thread is its author's: a reply on somebody else's is
        # not found, the way the thread itself is (routes/agent_timeline).
        author = await db_client.thread_author(
            organization_id=organization_id, thread_id=getattr(event, "thread_id", None)
        )
        if author is not None and author != viewer_id:
            raise NotFound("That reply is not here.")
    payload = dict(event.payload or {})
    body = str(payload.get("body") or event.summary or "")
    return {
        "output_version": version_of(body),
        "model": (str(payload.get("model") or payload.get("preset") or "") or None),
        "task_version": None,
        "thread_id": getattr(event, "thread_id", None),
        "workflow_id": event.workflow_id,
    }


async def _task_subject(organization_id: int, task_id: int) -> dict[str, Any]:
    from api.services.workflow import task_ledger

    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise NotFound("That task is not here.")
    if task_ledger.state_of(task) != task_ledger.COMPLETED:
        raise FeedbackRefused("Feedback is for finished tasks.")
    return {
        "output_version": version_of(str(task.result or task.outcome_evidence or "")),
        "model": None,
        "task_version": int(task.state_version or 0),
        "thread_id": None,
        "workflow_id": None,
    }


async def subject(
    organization_id: int, kind: str, subject_id: int, viewer_id: int | None = None
) -> dict[str, Any]:
    if kind == REPLY:
        return await _reply_subject(organization_id, subject_id, viewer_id)
    if kind == TASK:
        return await _task_subject(organization_id, subject_id)
    raise FeedbackRefused("Feedback is on a reply or a task.")


async def submit(
    *,
    organization_id: int,
    user_id: int,
    subject_kind: str,
    subject_id: int,
    verdict: str,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Store one person's verdict on one output; again replaces it."""
    reasons = _clean(verdict, reasons)
    found = await subject(organization_id, subject_kind, subject_id, user_id)
    now = datetime.now(UTC)
    values = {
        "organization_id": organization_id,
        "user_id": user_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "verdict": verdict,
        "reasons": reasons,
        "output_version": found["output_version"],
        "model": (found["model"] or None) and found["model"][:128],
        "task_version": found["task_version"],
        "created_at": now,
        "updated_at": now,
    }
    async with db_client.async_session() as session:
        stmt = insert(OutputFeedbackModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_output_feedback_once",
            set_={
                "verdict": verdict,
                "reasons": reasons,
                "output_version": values["output_version"],
                "model": values["model"],
                "task_version": values["task_version"],
                "updated_at": now,
                # A person's feedback on an output stays in the workspace it
                # was given in.
                "organization_id": organization_id,
            },
        ).returning(OutputFeedbackModel.id)
        row_id = (await session.execute(stmt)).scalar_one()
        await events.emit(
            "feedback_submitted",
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            task_id=subject_id if subject_kind == TASK else None,
            configuration_version=values["model"] and _code(values["model"]),
            properties={
                "verdict": verdict,
                "reasons": reasons,
                "subject_kind": subject_kind,
            },
        )
        await session.commit()
    return {
        "id": row_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "verdict": verdict,
        "reasons": reasons,
        "output_version": values["output_version"],
    }


def _code(value: str) -> str:
    """A model id as an envelope code: letters, digits and ``_.:-`` only."""
    return "".join(c if c.isalnum() or c in "_.:-" else "_" for c in value)[:64]


async def mine(
    *, organization_id: int, user_id: int, subject_kind: str, subject_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """This person's answers on these outputs, so the screen can show what
    they already said instead of asking again."""
    if not subject_ids:
        return {}
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(OutputFeedbackModel).where(
                        OutputFeedbackModel.organization_id == organization_id,
                        OutputFeedbackModel.user_id == user_id,
                        OutputFeedbackModel.subject_kind == subject_kind,
                        OutputFeedbackModel.subject_id.in_(subject_ids[:200]),
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        r.subject_id: {"verdict": r.verdict, "reasons": list(r.reasons or [])}
        for r in rows
    }


async def summary(organization_id: int | None = None) -> dict[str, Any]:
    """Counts for staff: answered, yes, not quite, and each reason. The
    exposure count (how many were asked) is the staff stream's to join;
    this table only holds answers, per handoff 36."""
    async with db_client.async_session() as session:
        query = select(
            OutputFeedbackModel.verdict, func.count(OutputFeedbackModel.id)
        ).group_by(OutputFeedbackModel.verdict)
        if organization_id is not None:
            query = query.where(OutputFeedbackModel.organization_id == organization_id)
        verdicts = dict((await session.execute(query)).all())
        reason_query = select(OutputFeedbackModel.reasons).where(
            OutputFeedbackModel.verdict == NOT_QUITE
        )
        if organization_id is not None:
            reason_query = reason_query.where(
                OutputFeedbackModel.organization_id == organization_id
            )
        reasons: dict[str, int] = {r: 0 for r in REASONS}
        for (listed,) in (await session.execute(reason_query)).all():
            for reason in listed or []:
                reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "answered": sum(verdicts.values()),
        "yes": verdicts.get(YES, 0),
        "not_quite": verdicts.get(NOT_QUITE, 0),
        "reasons": reasons,
    }

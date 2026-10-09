"""Saving, cancelling and reading a person's reminder-call schedules.

``save`` is the only writer of a schedule, and only from a confirmed card
(``cards.execute``): the schedule's ``version`` is the version that card
showed, so what rings is what the person read. Every read and write here is
scoped by organisation **and** person: a colleague's schedule is not here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select, update

from api.db import db_client
from api.db.reminder_call_models import (
    ReminderCallDispatchModel,
    ReminderCallOccurrenceModel,
    ReminderCallScheduleModel,
)
from api.services import reminder_calls as rc
from api.services.reminder_calls import (
    NotHere,
    ReminderCallError,
    draft,
    number,
    policy,
)
from api.services.today.scope import full_local


def _now() -> datetime:
    return datetime.now(UTC)


async def save(
    organization_id: int,
    user_id: int,
    args: dict[str, Any],
    *,
    version: str,
    card_event_id: int | None,
    thread_id: str | None,
    now: datetime | None = None,
) -> ReminderCallScheduleModel:
    """A confirmed card becomes a schedule. Raises ReminderCallError when it
    can no longer be honoured as shown (its one time has passed, or the
    number on it is no longer the confirmed one)."""
    now = now or _now()
    phone = number.clean(str(args.get("phone") or ""))
    if await number.ready(organization_id, user_id) != phone:
        raise ReminderCallError(
            "The number on this card is not confirmed for reminder calls. "
            "Confirm the number card first."
        )
    # The first ring the card showed, while it is still ahead; for a
    # repeating reminder confirmed after it, the next one from now. A
    # one-off whose time has passed is refused rather than moved.
    shown = args.get("first_due_at")
    first = datetime.fromisoformat(str(shown)) if shown else None
    if first is not None and first > now:
        due = first
    elif args.get("recurrence", "once") == "once":
        due = None
    else:
        due = draft.first_due(args, after=now)
    if due is None:
        raise ReminderCallError("That time has already passed. Ask me again.")
    replaces = args.get("replaces")
    async with db_client.async_session() as session:
        if replaces:
            await _stop(session, organization_id, user_id, int(replaces), now)
        row = ReminderCallScheduleModel(
            organization_id=organization_id,
            user_id=user_id,
            title=str(args["title"])[: draft.MAX_TITLE],
            language=str(args.get("language") or "en"),
            phone=phone,
            timezone=str(args["timezone"]),
            local_time=str(args["local_time"]),
            recurrence=str(args.get("recurrence") or "once"),
            weekday=args.get("weekday"),
            date=(
                datetime.fromisoformat(str(args["date"])).date()
                if args.get("date")
                else None
            ),
            retry_policy=policy.retry_policy(),
            fallback="push",
            quiet_exception=None,
            card_event_id=card_event_id,
            thread_id=thread_id,
            version=version,
            state=rc.ACTIVE,
            next_due_at=due,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def _stop_unrung(
    session: Any,
    organization_id: int,
    occurrence_ids: Any,
    reason: str,
    now: datetime,
) -> int:
    """Inside the caller's transaction: every ring attempt of these
    occurrences that the provider has not been asked for -- ``queued``, or
    ``dispatching`` with no run recorded yet -- becomes ``skipped`` with
    ``reason``, and gives back the cap slot it held. Rows are locked first;
    an attempt whose run was recorded before this lock was granted is past
    the point of no return (``calls._claim_to_dial``) and is left alone."""
    from api.services.call_when_done import allowance

    rows = (
        await session.scalars(
            select(ReminderCallDispatchModel)
            .where(
                ReminderCallDispatchModel.occurrence_id.in_(occurrence_ids),
                ReminderCallDispatchModel.organization_id == organization_id,
                or_(
                    ReminderCallDispatchModel.state == rc.QUEUED,
                    and_(
                        ReminderCallDispatchModel.state == rc.DISPATCHING,
                        ReminderCallDispatchModel.workflow_run_id.is_(None),
                    ),
                ),
            )
            .order_by(ReminderCallDispatchModel.id)
            .with_for_update()
        )
    ).all()
    for row in rows:
        if row.allowance_day is not None:
            await allowance.give_back_locked(
                session, user_id=row.user_id, day=row.allowance_day
            )
            row.allowance_day = None
        row.outcome_history = [
            *(row.outcome_history or []),
            {
                "at": now.isoformat(),
                "from": row.state,
                "to": rc.SKIPPED,
                "reason": reason,
                "source": "person",
            },
        ]
        row.state = rc.SKIPPED
        row.reason = reason
        row.settled_at = now
    return len(rows)


async def _stop(
    session: Any, organization_id: int, user_id: int, schedule_id: int, now: datetime
) -> bool:
    """Inside the caller's transaction, in lock order (schedule, then its
    occurrences, then their attempts -- the order ``calls._claim_to_dial``
    takes them in): the schedule stops, its open occurrences are cancelled
    tasks, and every attempt not yet handed to the provider is skipped."""
    moved = (
        await session.execute(
            update(ReminderCallScheduleModel)
            .where(
                ReminderCallScheduleModel.id == schedule_id,
                ReminderCallScheduleModel.organization_id == organization_id,
                ReminderCallScheduleModel.user_id == user_id,
                ReminderCallScheduleModel.state == rc.ACTIVE,
            )
            .values(state=rc.CANCELLED, next_due_at=None, updated_at=now)
            .returning(ReminderCallScheduleModel.id)
        )
    ).first()
    if not moved:
        return False
    await session.execute(
        update(ReminderCallOccurrenceModel)
        .where(
            ReminderCallOccurrenceModel.schedule_id == schedule_id,
            ReminderCallOccurrenceModel.organization_id == organization_id,
            ReminderCallOccurrenceModel.task_state.in_((rc.OPEN, rc.SNOOZED)),
        )
        .values(task_state=rc.TASK_CANCELLED, updated_at=now)
    )
    await _stop_unrung(
        session,
        organization_id,
        select(ReminderCallOccurrenceModel.id).where(
            ReminderCallOccurrenceModel.schedule_id == schedule_id,
            ReminderCallOccurrenceModel.organization_id == organization_id,
        ),
        "cancelled",
        now,
    )
    return True


async def cancel(organization_id: int, user_id: int, schedule_id: int) -> bool:
    """Stop a schedule, in one transaction: nothing more is made for it, its
    open occurrences are cancelled, and every ring attempt not yet handed
    to the provider is skipped (its slot given back). A dial already past
    the gate re-checks all of this under lock before the provider is asked
    (``calls._claim_to_dial``), so a cancelled occurrence never rings.
    Returns False when it is not this person's in this workspace, or
    already stopped."""
    now = _now()
    async with db_client.async_session() as session:
        moved = await _stop(session, organization_id, user_id, schedule_id, now)
        await session.commit()
    return moved


async def cancel_for_card(
    organization_id: int, user_id: int, payload: dict[str, Any]
) -> bool:
    """Put back a confirmed card: the schedule saved at that card's version."""
    version = (payload.get("confirmed") or {}).get("version") or payload.get("version")
    if not version:
        return False
    async with db_client.async_session() as session:
        found = await session.scalar(
            select(ReminderCallScheduleModel.id)
            .where(
                ReminderCallScheduleModel.organization_id == organization_id,
                ReminderCallScheduleModel.user_id == user_id,
                ReminderCallScheduleModel.version == str(version),
                ReminderCallScheduleModel.state == rc.ACTIVE,
            )
            .order_by(ReminderCallScheduleModel.id.desc())
        )
    return await cancel(organization_id, user_id, found) if found else False


async def mark_done(organization_id: int, user_id: int, occurrence_id: int) -> bool:
    """The person says, in the app, they have dealt with it. Moves the task;
    the outcomes of calls that rang are untouched, and an attempt not yet
    handed to the provider (a queued retry, say) is skipped, never rung."""
    now = _now()
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(ReminderCallOccurrenceModel)
                .where(
                    ReminderCallOccurrenceModel.id == occurrence_id,
                    ReminderCallOccurrenceModel.organization_id == organization_id,
                    ReminderCallOccurrenceModel.user_id == user_id,
                    ReminderCallOccurrenceModel.task_state.in_((rc.OPEN, rc.SNOOZED)),
                )
                .values(task_state=rc.USER_REPORTED_DONE, updated_at=now)
                .returning(ReminderCallOccurrenceModel.id)
            )
        ).first()
        if moved is not None:
            await _stop_unrung(session, organization_id, [occurrence_id], "done", now)
        await session.commit()
    if moved is None:
        async with db_client.async_session() as session:
            exists = await session.scalar(
                select(ReminderCallOccurrenceModel.id).where(
                    ReminderCallOccurrenceModel.id == occurrence_id,
                    ReminderCallOccurrenceModel.organization_id == organization_id,
                    ReminderCallOccurrenceModel.user_id == user_id,
                )
            )
        if exists is None:
            raise NotHere("That reminder is not here.")
    return moved is not None


def _view(
    row: ReminderCallScheduleModel, recent: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "language": row.language,
        "number": number.masked(row.phone),
        "timezone": row.timezone,
        "local_time": row.local_time,
        "recurrence": row.recurrence,
        "weekday": row.weekday,
        "state": row.state,
        "next_due_at": row.next_due_at.isoformat() if row.next_due_at else None,
        # The next ring as the card said it: "Tue 13 Oct 2026, 09:00 IST
        # (Asia/Kolkata)".
        "next_due_label": (
            full_local(row.next_due_at, row.timezone) if row.next_due_at else None
        ),
        "recent": recent,
    }


async def list_for(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    """The person's schedules here, newest first, each with its latest
    occurrences: the task state and every ring attempt's delivery state,
    side by side and never folded into one."""
    async with db_client.async_session() as session:
        schedules = (
            await session.scalars(
                select(ReminderCallScheduleModel)
                .where(
                    ReminderCallScheduleModel.organization_id == organization_id,
                    ReminderCallScheduleModel.user_id == user_id,
                )
                .order_by(ReminderCallScheduleModel.id.desc())
                .limit(50)
            )
        ).all()
        ids = [s.id for s in schedules]
        occurrences = (
            (
                await session.scalars(
                    select(ReminderCallOccurrenceModel)
                    .where(
                        ReminderCallOccurrenceModel.schedule_id.in_(ids),
                        ReminderCallOccurrenceModel.organization_id == organization_id,
                    )
                    .order_by(ReminderCallOccurrenceModel.due_at.desc())
                )
            ).all()
            if ids
            else []
        )
        occurrence_ids = [o.id for o in occurrences]
        dispatches = (
            (
                await session.scalars(
                    select(ReminderCallDispatchModel)
                    .where(
                        ReminderCallDispatchModel.occurrence_id.in_(occurrence_ids),
                        ReminderCallDispatchModel.organization_id == organization_id,
                    )
                    .order_by(ReminderCallDispatchModel.attempt)
                )
            ).all()
            if occurrence_ids
            else []
        )
    rings: dict[int, list[dict[str, Any]]] = {}
    for d in dispatches:
        rings.setdefault(d.occurrence_id, []).append(
            {"attempt": d.attempt, "state": d.state, "reason": d.reason}
        )
    recent: dict[int, list[dict[str, Any]]] = {}
    for o in occurrences:
        bucket = recent.setdefault(o.schedule_id, [])
        if len(bucket) < 5:
            bucket.append(
                {
                    "id": o.id,
                    "due_at": o.due_at.isoformat(),
                    "task_state": o.task_state,
                    "calls": rings.get(o.id, []),
                }
            )
    return [_view(s, recent.get(s.id, [])) for s in schedules]

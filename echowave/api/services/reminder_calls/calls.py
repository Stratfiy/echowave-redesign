"""Reminder calls, from the minute tick to what the person is told.

The flow (docs/plans/reminder-calls.md, sections 2, 3 and 5):

* **Occurrences** (``_materialise``, every tick). A schedule whose
  ``next_due_at`` has come is advanced by a compare-and-swap from the value
  the tick read, and only the tick that advanced it writes the occurrence
  (unique ``occurrence_key``: schedule, version, local due time) and its
  first dispatch (``queued``). Two ticks, two workers: one occurrence.
* **The claim** (``_claim``): ``queued`` -> ``dispatching`` by a
  compare-and-swap. Only the claimant rings.
* **The gate** (``gate.may_dial``), immediately before the dial: still
  wanted, still a member, line and adult-confirmed number, quiet hours asked
  directly, the do-not-call list, the shared daily cap reserved. A refusal
  is ``skipped`` with its reason; the slot (if any) is given back; the
  person gets the reminder as a notification and a line on the thread.
* **The conditional claim** (``_claim_to_dial``), right after the gate and
  again as the run is recorded: under row locks taken in a cancel's order,
  the schedule is still active, the occurrence still open at its version,
  the attempt still this worker's. A cancel that committed first is seen;
  one that comes later finds the run recorded and leaves the ring alone.
  A cancelled occurrence never rings.
* **The dial** (``dial_workflow``) records the run on the dispatch before
  the provider is asked (``on_run_created``). A dial that raises before
  that is a verified non-dispatch (``skipped``, slot released); after it,
  ``unknown``: never re-dialled, reconciled.
* **What came of it**, on evidence only: the post-call report
  (``record_run_outcome``), the run's carrier status (``reconcile``, from
  the sweep). ``no_answer`` needs a run (a claim that never dialled cannot
  be "not answered"); late truth moves ``unknown`` and corrects
  ``no_answer``; a duplicate report is a no-op; every change is appended to
  ``outcome_history``.
* **No answer**: one retry ``RETRY_GAP_MINUTES`` after the first ring,
  inside the window, while the task is open, holding its own cap slot
  (D1); never from ``unknown`` or ``failed``. Then the notification.
* **The task** moves only by what the person said (on the call: done,
  snooze, cancel; in the app: done). Delivery outcomes never move it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.reminder_call_models import (
    ReminderCallDispatchModel,
    ReminderCallOccurrenceModel,
    ReminderCallScheduleModel,
)
from api.enums import AgentEventActor, AgentEventKind
from api.services import features
from api.services import reminder_calls as rc
from api.services.compliance import dnd
from api.services.reminder_calls import draft, gate, policy
from api.services.telephony import call_evidence

#: Reasons where the person stopped it themselves, or is no longer in the
#: workspace: nothing is sent about them.
QUIET_REASONS = frozenset(
    {"cancelled", "done", "superseded", "not_member", "answered_earlier"}
)


def _now() -> datetime:
    return datetime.now(UTC)


def why(reason: str | None) -> str:
    """Why no call reached the person, as the thread says it."""
    cap = constants.CALL_WHEN_DONE_DAILY_CAP
    return {
        "no_line": "this workspace has no phone line for calling out",
        "no_number": "the number for reminder calls is not confirmed",
        "not_adult": f"the number card's “{policy.ADULT_ATTESTATION}” was not confirmed",
        "quiet_hours": f"it was outside calling hours ({policy.window_words()})",
        "dnd": "your number is on this workspace's do-not-call list",
        "cap": f"the day's call limit is reached ({cap} call{'s' if cap != 1 else ''} a day)",
        "late": "the call could not go out on time",
        "quota": "the workspace could not pay for the call",
        "line_busy": "every line was busy",
        "not_connected": "the call could not be connected",
        "rejected": "the phone provider would not place a call to that number",
    }.get(reason or "", "something went wrong on our side")


# --- reading -------------------------------------------------------------------


async def _rows(dispatch_id: int) -> tuple[Any, Any, Any]:
    async with db_client.async_session() as session:
        dispatch = await session.get(ReminderCallDispatchModel, dispatch_id)
        occurrence = (
            await session.get(ReminderCallOccurrenceModel, dispatch.occurrence_id)
            if dispatch
            else None
        )
        schedule = (
            await session.get(ReminderCallScheduleModel, occurrence.schedule_id)
            if occurrence
            else None
        )
    return dispatch, occurrence, schedule


def _local(moment: datetime, zone_name: str) -> str:
    return f"{moment.astimezone(ZoneInfo(zone_name)):%H:%M}"


# --- occurrences ---------------------------------------------------------------


def occurrence_key(schedule: Any, due: datetime) -> str:
    local = due.astimezone(ZoneInfo(schedule.timezone)).isoformat()
    return f"{schedule.id}:{schedule.version}:{local}"


def _as_draft(schedule: Any) -> dict[str, Any]:
    return {
        "timezone": schedule.timezone,
        "local_time": schedule.local_time,
        "recurrence": schedule.recurrence,
        "weekday": schedule.weekday,
        "date": schedule.date.isoformat() if schedule.date else None,
    }


def next_after(schedule: Any, after: datetime) -> datetime | None:
    if schedule.recurrence == "once":
        return None
    return draft.first_due(_as_draft(schedule), after=after)


async def _new_occurrence(
    session, schedule: Any, due: datetime, now: datetime
) -> int | None:
    """The occurrence and its first ring attempt, once per key."""
    made = (
        await session.execute(
            insert(ReminderCallOccurrenceModel)
            .values(
                schedule_id=schedule.id,
                organization_id=schedule.organization_id,
                user_id=schedule.user_id,
                schedule_version=schedule.version,
                due_at=due,
                occurrence_key=occurrence_key(schedule, due),
                task_state=rc.OPEN,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["occurrence_key"])
            .returning(ReminderCallOccurrenceModel.id)
        )
    ).first()
    if made is None:
        return None
    await session.execute(
        insert(ReminderCallDispatchModel)
        .values(
            occurrence_id=made[0],
            organization_id=schedule.organization_id,
            user_id=schedule.user_id,
            attempt=1,
            state=rc.QUEUED,
            due_at=due,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_reminder_call_attempt")
    )
    return made[0]


async def _materialise(now: datetime) -> int:
    """Every active schedule that has come due gets its occurrence, made by
    the one tick that advanced the schedule from the value it read."""
    async with db_client.async_session() as session:
        due = (
            await session.scalars(
                select(ReminderCallScheduleModel)
                .where(
                    ReminderCallScheduleModel.state == rc.ACTIVE,
                    ReminderCallScheduleModel.next_due_at <= now,
                )
                .order_by(ReminderCallScheduleModel.next_due_at)
                .limit(500)
            )
        ).all()
    made = 0
    for schedule in due:
        if not rc.enabled(schedule.organization_id):
            continue
        read = schedule.next_due_at
        following = next_after(schedule, read)
        async with db_client.async_session() as session:
            advanced = (
                await session.execute(
                    update(ReminderCallScheduleModel)
                    .where(
                        ReminderCallScheduleModel.id == schedule.id,
                        ReminderCallScheduleModel.state == rc.ACTIVE,
                        ReminderCallScheduleModel.next_due_at == read,
                    )
                    .values(next_due_at=following, updated_at=now)
                    .returning(ReminderCallScheduleModel.id)
                )
            ).first()
            if advanced is None:
                await session.rollback()
                continue
            if await _new_occurrence(session, schedule, read, now) is not None:
                made += 1
            await session.commit()
    return made


# --- the scheduler -------------------------------------------------------------


async def tick(now: datetime | None = None) -> int:
    """Make due occurrences, and ring every attempt that has come due.
    Returns the attempts handled."""
    if not features.on_anywhere(rc.FLAG):
        return 0
    now = now or _now()
    await _materialise(now)
    async with db_client.async_session() as session:
        due = (
            await session.execute(
                select(
                    ReminderCallDispatchModel.id,
                    ReminderCallDispatchModel.organization_id,
                )
                .where(
                    ReminderCallDispatchModel.state == rc.QUEUED,
                    ReminderCallDispatchModel.due_at <= now,
                )
                .order_by(ReminderCallDispatchModel.due_at)
                .limit(200)
            )
        ).all()
    handled = 0
    for dispatch_id, organization_id in due:
        if not rc.enabled(organization_id):
            continue
        if not await _claim(dispatch_id, now):
            continue
        await place(dispatch_id, now=now)
        handled += 1
    return handled


async def _claim(dispatch_id: int, now: datetime) -> bool:
    """queued -> dispatching, once: the tick that moved it rings it."""
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(ReminderCallDispatchModel)
                .where(
                    ReminderCallDispatchModel.id == dispatch_id,
                    ReminderCallDispatchModel.state == rc.QUEUED,
                )
                .values(state=rc.DISPATCHING, reserved_at=now)
                .returning(ReminderCallDispatchModel.id)
            )
        ).first()
        await session.commit()
    return bool(moved)


class _Superseded(Exception):
    """The claim was settled by somebody else before the provider was asked."""


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def place(dispatch_id: int, *, now: datetime | None = None) -> None:
    """Ring one claimed attempt. Never raises: a refusal is the attempt's
    state, and the person is told."""
    now = now or _now()
    dispatch, _occurrence, _schedule = await _rows(dispatch_id)
    if dispatch is None or dispatch.state != rc.DISPATCHING:
        return
    if now - dispatch.due_at > timedelta(minutes=policy.LATE_MINUTES):
        # A tick that was down: a reminder long past is not rung now.
        await _skip(dispatch_id, "late")
        return
    verdict = await gate.may_dial(dispatch_id, now)
    if not verdict.ok:
        await _skip(dispatch_id, verdict.reason or "call_error")
        return
    organization_id = dispatch.organization_id

    async def link(run_id: int) -> None:
        # The conditional claim, the last thing before the provider is
        # asked: raising here stops dial_workflow before anything rings.
        await _claim_to_dial(dispatch_id, organization_id, run_id)

    try:
        # Once now (a cancel that landed while the gate was asking), and
        # again, under lock, as the run is recorded.
        await _claim_to_dial(dispatch_id, organization_id, None)
        run_id = await _dial(dispatch_id, verdict.dialable or "", on_run_created=link)
    except _Superseded:
        logger.warning("reminder_calls: dispatch {} settled mid-dial", dispatch_id)
        await _release_if_unrung(dispatch_id)
        return
    except _Refused as exc:
        await _skip(dispatch_id, exc.reason)
        return
    except Exception as exc:  # noqa: BLE001 - the attempt must say something
        logger.error(
            "reminder_calls: dispatch {} raised while dialling: {!r}", dispatch_id, exc
        )
        await _after_dial_error(dispatch_id, now, error=exc)
        return
    await _link_run(dispatch_id, organization_id, run_id)
    await _move(
        dispatch_id,
        rc.ACCEPTED,
        reason=None,
        allowed_from=(rc.DISPATCHING,),
        source="dial",
        values={"dialled_at": now},
    )


async def _claim_to_dial(
    dispatch_id: int, organization_id: int, run_id: int | None
) -> None:
    """Re-check, under lock, that this attempt is still wanted, and (with
    ``run_id``) record its run: the point of no return.

    Locks in the order a cancel takes them (``schedule._stop``): the
    schedule and the occurrence for share, then the attempt for update. A
    cancel, a "done" or a superseding card that committed first is seen
    here; one that comes after waits for this commit and then finds the run
    recorded, so it leaves the attempt alone (it is ringing). Raises
    ``_Superseded`` when the attempt is no longer this worker's to ring
    (somebody settled it: a cancel skipped it, the sweep took it back), and
    ``_Refused`` when the reminder was stopped but the attempt was not yet
    touched."""
    async with db_client.async_session() as session:
        ids = (
            await session.execute(
                select(
                    ReminderCallDispatchModel.occurrence_id,
                    ReminderCallOccurrenceModel.schedule_id,
                )
                .join(
                    ReminderCallOccurrenceModel,
                    ReminderCallOccurrenceModel.id
                    == ReminderCallDispatchModel.occurrence_id,
                )
                .where(
                    ReminderCallDispatchModel.id == dispatch_id,
                    ReminderCallDispatchModel.organization_id == organization_id,
                )
            )
        ).first()
        if ids is None:
            raise _Superseded()
        schedule = (
            await session.execute(
                select(ReminderCallScheduleModel)
                .where(
                    ReminderCallScheduleModel.id == ids.schedule_id,
                    ReminderCallScheduleModel.organization_id == organization_id,
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        occurrence = (
            await session.execute(
                select(ReminderCallOccurrenceModel)
                .where(
                    ReminderCallOccurrenceModel.id == ids.occurrence_id,
                    ReminderCallOccurrenceModel.organization_id == organization_id,
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        dispatch = (
            await session.execute(
                select(ReminderCallDispatchModel)
                .where(ReminderCallDispatchModel.id == dispatch_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            dispatch is None
            or dispatch.state != rc.DISPATCHING
            or dispatch.workflow_run_id not in (None, run_id)
        ):
            await session.rollback()
            raise _Superseded()
        if (
            schedule is None
            or occurrence is None
            or schedule.state != rc.ACTIVE
            or occurrence.task_state != rc.OPEN
        ):
            await session.rollback()
            raise _Refused("cancelled")
        if occurrence.schedule_version != schedule.version:
            await session.rollback()
            raise _Refused("superseded")
        if run_id is None:
            await session.rollback()
            return
        dispatch.workflow_run_id = run_id
        await session.commit()


async def _release_if_unrung(dispatch_id: int) -> None:
    """An attempt settled under this worker as ``skipped`` (a cancel) never
    rang: a slot it took at the gate after that cancel goes back."""
    from api.services.call_when_done import allowance

    if await _state(dispatch_id) == rc.SKIPPED:
        await allowance.give_back(ReminderCallDispatchModel, dispatch_id)


async def _link_run(
    dispatch_id: int, organization_id: int, run_id: int, *, claimed: bool = False
) -> bool:
    """Record which run this attempt is, before the provider is asked, and
    again after, harmlessly. With ``claimed``, only while ``dispatching``."""
    query = update(ReminderCallDispatchModel).where(
        ReminderCallDispatchModel.id == dispatch_id,
        ReminderCallDispatchModel.organization_id == organization_id,
    )
    if claimed:
        query = query.where(ReminderCallDispatchModel.state == rc.DISPATCHING)
    async with db_client.async_session() as session:
        written = (
            await session.execute(
                query.values(workflow_run_id=run_id).returning(
                    ReminderCallDispatchModel.id
                )
            )
        ).first()
        await session.commit()
    return written is not None


async def _dial(dispatch_id: int, dialable: str, *, on_run_created=None) -> int:
    """The platform's outbound path, the one every outbound call takes."""
    from api.services import member_preferences
    from api.services.reminder_calls import agent
    from api.services.telephony.factory import get_telephony_provider_by_id
    from api.services.telephony.outbound import (
        OutboundRefused,
        QuotaExhausted,
        dial_workflow,
    )

    dispatch, _occurrence, schedule = await _rows(dispatch_id)
    if dispatch is None or schedule is None:
        raise _Refused("cancelled")
    organization_id = dispatch.organization_id
    config = await db_client.get_default_telephony_configuration(organization_id)
    if not config:
        raise _Refused("no_line")
    prefs = await member_preferences.get(schedule.user_id)
    workflow = await agent.ensure_workflow(organization_id, user_id=schedule.user_id)
    provider = await get_telephony_provider_by_id(config.id, organization_id)
    async with db_client.async_session() as session:
        await session.execute(
            update(ReminderCallDispatchModel)
            .where(ReminderCallDispatchModel.id == dispatch_id)
            .values(provider=str(getattr(provider, "PROVIDER_NAME", "") or "")[:32])
        )
        await session.commit()
    try:
        return await dial_workflow(
            workflow=workflow,
            organization_id=organization_id,
            to_number=dialable,
            provider=provider,
            telephony_configuration_id=config.id,
            source="reminder_call",
            extra_context={
                "trigger_source": "reminder_call",
                "reminder_dispatch_id": dispatch_id,
                **agent.call_context(
                    title=schedule.title,
                    language=schedule.language,
                    name=prefs.get("preferred_name") or "",
                ),
            },
            on_run_created=on_run_created,
        )
    except QuotaExhausted as exc:
        raise _Refused("quota") from exc
    except OutboundRefused as exc:
        raise _Refused("line_busy") from exc
    except dnd.CallRefused as exc:
        # The calling number is not declared: refused before a run exists.
        raise _Refused("call_error") from exc


def _provider_rejected(error: BaseException | None) -> bool:
    """The provider answered the dial request with a 4xx: it refused the
    request (an invalid number, say), so nothing rang. A timeout or a 5xx
    proves nothing either way."""
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    return isinstance(status, int) and 400 <= status < 500


async def _after_dial_error(
    dispatch_id: int, now: datetime, *, error: BaseException | None = None
) -> None:
    """The dial raised for a reason that is not a refusal. No run recorded:
    the provider was never asked (skipped, slot released). The provider
    refused the request (4xx): ``failed``, nothing rang, slot released. Any
    other error after the run was recorded: it may have rung -- ``unknown``,
    reconciled, never dialled again."""
    from api.services.call_when_done import allowance

    dispatch, _o, _s = await _rows(dispatch_id)
    if dispatch is None or dispatch.state != rc.DISPATCHING:
        return
    if dispatch.workflow_run_id is None:
        await _skip(dispatch_id, "call_error")
        return
    if _provider_rejected(error):
        await allowance.give_back(ReminderCallDispatchModel, dispatch_id)
        await _apply(dispatch_id, rc.FAILED, "rejected", source="dial", now=now)
        return
    await reconcile(dispatch_id, now=now, unknown_reason="dial_unconfirmed")


async def _skip(dispatch_id: int, reason: str) -> None:
    """A verified non-dispatch -- the gate refused, the dial was refused
    before the provider was asked (a run may exist when the spend check
    refused it), or no run was ever recorded: its slot goes back, it is
    ``skipped`` with the reason, and -- unless the person stopped it -- the reminder reaches
    them as a notification and a line on the thread."""
    from api.services.call_when_done import allowance

    await allowance.give_back(ReminderCallDispatchModel, dispatch_id)
    moved = await _move(
        dispatch_id,
        rc.SKIPPED,
        reason=reason,
        allowed_from=(rc.QUEUED, rc.DISPATCHING),
        source="gate",
        values={"settled_at": _now()},
    )
    if moved is not None and reason not in QUIET_REASONS:
        await fallback(dispatch_id, reason)


# --- moving a dispatch ---------------------------------------------------------


def _entry(prior: str, to: str, reason: str | None, source: str) -> dict[str, Any]:
    return {
        "at": _now().isoformat(),
        "from": prior,
        "to": to,
        "reason": reason,
        "source": source,
    }


async def _move(
    dispatch_id: int,
    state: str,
    *,
    reason: str | None,
    allowed_from: tuple[str, ...],
    source: str,
    values: dict[str, Any] | None = None,
    require_run: bool = False,
) -> str | None:
    """Compare-and-swap a dispatch's state, appending to its history.
    Returns the state it moved from, or None when it did not move (not in
    ``allowed_from``, already there -- a duplicate is a no-op -- or the run
    condition does not hold)."""
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(ReminderCallDispatchModel)
                .where(ReminderCallDispatchModel.id == dispatch_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            row is None
            or row.state not in allowed_from
            or row.state == state
            or (require_run and row.workflow_run_id is None)
        ):
            await session.rollback()
            return None
        prior = row.state
        row.state = state
        row.reason = reason
        for key, value in (values or {}).items():
            setattr(row, key, value)
        row.outcome_history = [
            *(row.outcome_history or []),
            _entry(prior, state, reason, source),
        ]
        await session.commit()
    return prior


#: Where each kind of evidence may move an attempt from. ``no_answer``
#: needs a run as well (``require_run``): an attempt the provider was never
#: asked to place cannot be "not answered". Nothing moves ``answered``.
_FROM = {
    rc.ANSWERED: (rc.DISPATCHING, rc.ACCEPTED, rc.UNKNOWN, rc.NO_ANSWER),
    rc.NO_ANSWER: (rc.DISPATCHING, rc.ACCEPTED, rc.UNKNOWN),
    rc.FAILED: (rc.DISPATCHING, rc.ACCEPTED, rc.UNKNOWN),
    rc.UNKNOWN: (rc.DISPATCHING, rc.ACCEPTED),
}


async def _apply(
    dispatch_id: int,
    state: str,
    reason: str | None,
    source: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """Move an attempt on evidence, and say what needs saying -- once.
    Returns the state it moved from (None: it did not move)."""
    told_missed = state == rc.ANSWERED and await _told_not_answered(dispatch_id)
    prior = await _move(
        dispatch_id,
        state,
        reason=reason,
        allowed_from=_FROM[state],
        source=source,
        values={"settled_at": _now()},
        require_run=state == rc.NO_ANSWER,
    )
    if prior is None:
        return None
    now = now or _now()
    if state == rc.NO_ANSWER and prior in (rc.DISPATCHING, rc.ACCEPTED):
        await _after_no_answer(dispatch_id, reason or "not_answered", now)
    elif state == rc.FAILED and prior in (rc.DISPATCHING, rc.ACCEPTED):
        await fallback(dispatch_id, reason or "not_connected")
    elif state == rc.UNKNOWN:
        await fallback(dispatch_id, "unknown")
    elif state == rc.ANSWERED:
        await _cancel_queued_retries(dispatch_id)
        if prior == rc.NO_ANSWER and told_missed:
            await _correct(dispatch_id)
    return prior


async def _told_not_answered(dispatch_id: int) -> bool:
    """Whether the person was told this attempt was not answered: it went
    from a live state straight to ``no_answer`` (``_apply`` says so then)."""
    async with db_client.async_session() as session:
        history = await session.scalar(
            select(ReminderCallDispatchModel.outcome_history).where(
                ReminderCallDispatchModel.id == dispatch_id
            )
        )
    return any(
        e.get("from") in (rc.DISPATCHING, rc.ACCEPTED) and e.get("to") == rc.NO_ANSWER
        for e in history or []
    )


async def _cancel_queued_retries(dispatch_id: int) -> None:
    """The person answered: a retry still waiting for this occurrence is not
    rung (an answer that arrived late, after the retry was queued)."""
    dispatch, _o, _s = await _rows(dispatch_id)
    if dispatch is None:
        return
    async with db_client.async_session() as session:
        waiting = (
            await session.scalars(
                select(ReminderCallDispatchModel.id).where(
                    ReminderCallDispatchModel.occurrence_id == dispatch.occurrence_id,
                    ReminderCallDispatchModel.state == rc.QUEUED,
                )
            )
        ).all()
    for other in waiting:
        await _move(
            other,
            rc.SKIPPED,
            reason="answered_earlier",
            allowed_from=(rc.QUEUED,),
            source="answered",
            values={"settled_at": _now()},
        )


async def _after_no_answer(dispatch_id: int, reason: str, now: datetime) -> None:
    """D1: one retry, ``RETRY_GAP_MINUTES`` after the first ring, inside the
    window, while the task is open. Otherwise, the notification."""
    dispatch, occurrence, schedule = await _rows(dispatch_id)
    if dispatch is None or occurrence is None or schedule is None:
        return
    when = occurrence.due_at + timedelta(
        minutes=policy.RETRY_GAP_MINUTES * dispatch.attempt
    )
    when = max(when, now)
    may_retry = (
        dispatch.attempt <= policy.MAX_RETRIES
        and occurrence.task_state == rc.OPEN
        and schedule.state == rc.ACTIVE
        and policy.within_window(schedule.timezone, when)
    )
    if not may_retry:
        await fallback(dispatch_id, reason)
        return
    async with db_client.async_session() as session:
        made = (
            await session.execute(
                insert(ReminderCallDispatchModel)
                .values(
                    occurrence_id=occurrence.id,
                    organization_id=dispatch.organization_id,
                    user_id=dispatch.user_id,
                    attempt=dispatch.attempt + 1,
                    state=rc.QUEUED,
                    due_at=when,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_reminder_call_attempt")
                .returning(ReminderCallDispatchModel.id)
            )
        ).first()
        await session.commit()
    if made is None:
        return
    at = _local(dispatch.dialled_at or occurrence.due_at, schedule.timezone)
    missed = (
        "it went to voicemail, so I left no details"
        if reason == "voicemail"
        else "you didn't pick up"
    )
    await say(
        dispatch_id,
        f"I called about your reminder at {at}, but {missed}. I'll try once "
        f"more at {_local(when, schedule.timezone)}.",
        state=rc.NO_ANSWER,
        reason=reason,
    )


async def _correct(dispatch_id: int) -> None:
    await say(
        dispatch_id,
        "Correction: my call about your reminder did reach you, so please "
        "ignore my note that you did not pick up.",
        state=rc.ANSWERED,
        reason="corrected",
    )


# --- the thread, and the person's own channels ------------------------------


async def say(
    dispatch_id: int, line: str, *, state: str, reason: str | None = None
) -> None:
    """One line from Decibyl on the reminder's own thread, the person's
    alone. Never raises."""
    from api.services.workflow import agent_timeline

    try:
        dispatch, occurrence, schedule = await _rows(dispatch_id)
        if dispatch is None or schedule is None:
            return
        await agent_timeline.record(
            organization_id=dispatch.organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=line[:500],
            payload={
                "body": line,
                "from": "Decibyl",
                "private_to": schedule.user_id,
                "reminder_call": {
                    "schedule_id": schedule.id,
                    "occurrence_id": occurrence.id if occurrence else None,
                    "attempt": dispatch.attempt,
                    "state": state,
                    "reason": reason,
                },
            },
            in_channel=False,
            thread_id=schedule.thread_id,
        )
    except Exception as exc:  # noqa: BLE001 - the dispatch keeps the state
        logger.error("reminder_calls: could not say on {}: {}", dispatch_id, exc)


async def fallback(dispatch_id: int, reason: str) -> None:
    """No call reached the person: the reminder, as a notification on
    their own channels (once per occurrence) and on the thread, with why.
    Never raises."""
    from api.services.identity import notifications

    try:
        dispatch, occurrence, schedule = await _rows(dispatch_id)
        if dispatch is None or occurrence is None or schedule is None:
            return
        title = schedule.title
        link = (
            f"/overview?thread={schedule.thread_id}"
            if schedule.thread_id
            else "/overview"
        )
        sent = await notifications.notify(
            schedule.user_id,
            topic="reminders",
            title=f"Reminder: {title}"[:200],
            body=title[:500],
            link=link,
            dedupe_key=f"reminder-call:{occurrence.id}",
        )
        async with db_client.async_session() as session:
            await session.execute(
                update(ReminderCallDispatchModel)
                .where(
                    ReminderCallDispatchModel.id == dispatch_id,
                    ReminderCallDispatchModel.organization_id
                    == dispatch.organization_id,
                )
                .values(notified=sent)
            )
            await session.commit()
        went = any(v == "sent" for v in (sent or {}).values())
        where = (
            "I've sent it to you as a notification as well."
            if went
            else "It's here in the thread."
        )
        at = _local(occurrence.due_at, schedule.timezone)
        if reason in ("not_answered", "voicemail"):
            tries = "twice" if dispatch.attempt > 1 else "at " + at
            line = f"I called {tries} about your reminder, but you didn't pick up."
        elif reason == "unknown":
            line = "I tried to call you with your reminder, but I can't confirm the call reached you."
        elif reason in ("someone_else", "not_confirmed"):
            line = (
                f"Someone else answered my {at} call, so I only asked them to "
                "tell you to check Decibyl."
            )
        elif reason == "answered_no_report":
            line = f"My {at} call was answered, but I can't confirm you heard your reminder."
        else:
            line = f"I couldn't call you with your reminder: {why(reason)}."
        await say(
            dispatch_id,
            f"{line} Your reminder: “{title}”. {where}",
            state=dispatch.state,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - the dispatch keeps the reason
        logger.error(
            "reminder_calls: could not tell dispatch {} in app: {}", dispatch_id, exc
        )


# --- what came of it -------------------------------------------------------------


def _evidence_state(evidence: str) -> tuple[str, str | None] | None:
    if evidence == call_evidence.NOT_CONNECTED:
        return rc.NO_ANSWER, "not_answered"
    if evidence == call_evidence.CARRIER_FAILED:
        return rc.FAILED, "not_connected"
    return None


async def reconcile(
    dispatch_id: int,
    *,
    now: datetime | None = None,
    unknown_reason: str = "no_outcome",
) -> str | None:
    """Read the attempt's run and settle it on what the run proves.

    * No run recorded and still ``dispatching``: never requested --
      ``skipped`` (``not_dialled``), slot released, never "not answered".
    * The carrier's no-answer: ``no_answer`` (then the retry or the
      notification). Its failure: ``failed``.
    * Answered, with no post-call report by the answer window: ``answered``
      (``answered_no_report``), and the reminder goes as a notification too,
      since nothing proves the person themselves heard it.
    * Anything else: ``unknown``, said once. Never dials."""
    now = now or _now()
    dispatch, _o, _s = await _rows(dispatch_id)
    if dispatch is None or dispatch.state not in (
        rc.DISPATCHING,
        rc.ACCEPTED,
        rc.UNKNOWN,
    ):
        return dispatch.state if dispatch else None
    if dispatch.workflow_run_id is None:
        if dispatch.state == rc.DISPATCHING:
            await _skip(dispatch_id, "not_dialled")
        return await _state(dispatch_id)
    evidence, _run = await call_evidence.read(
        dispatch.workflow_run_id, dispatch.organization_id
    )
    final = _evidence_state(evidence)
    if final is not None:
        await _apply(
            dispatch_id, final[0], final[1], source=f"reconcile:{evidence}", now=now
        )
    elif (
        evidence == call_evidence.ANSWERED
        and dispatch.reserved_at
        and (now - dispatch.reserved_at >= timedelta(minutes=policy.ANSWER_MINUTES))
    ):
        if await _apply(
            dispatch_id, rc.ANSWERED, "answered_no_report", source="reconcile:answered"
        ):
            await fallback(dispatch_id, "answered_no_report")
    elif dispatch.state in (rc.DISPATCHING, rc.ACCEPTED):
        await _apply(
            dispatch_id, rc.UNKNOWN, unknown_reason, source=f"reconcile:{evidence}"
        )
    return await _state(dispatch_id)


async def _state(dispatch_id: int) -> str | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(ReminderCallDispatchModel.state).where(
                ReminderCallDispatchModel.id == dispatch_id
            )
        )


def _extracted(run: Any) -> dict[str, Any]:
    gathered = getattr(run, "gathered_context", None) or {}
    return dict(gathered.get("extracted_variables") or {})


def _word(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


async def record_run_outcome(workflow_run_id: int) -> None:
    """Post-call: if this run was a reminder call, settle the attempt on
    what the run shows, and move the task by what the person said. Late
    evidence repairs ``unknown`` and corrects ``no_answer``; a duplicate is
    a no-op. Never raises; any other run is left alone."""
    try:
        run = await db_client.get_workflow_run(workflow_run_id)
        context = getattr(run, "initial_context", None) or {}
        dispatch_id = context.get("reminder_dispatch_id")
        if run is None or not dispatch_id:
            return
        organization_id = await db_client.get_organization_id_by_workflow_run_id(
            workflow_run_id
        )
        dispatch, _o, _s = await _rows(int(dispatch_id))
        # The run's workspace must be the attempt's, and the run must be the
        # attempt's own when one is recorded: an id in a context is not proof.
        if dispatch is None or dispatch.organization_id != organization_id:
            logger.warning(
                "reminder_calls: run {} names an attempt it does not own",
                workflow_run_id,
            )
            return
        if dispatch.workflow_run_id not in (None, workflow_run_id):
            logger.warning(
                "reminder_calls: run {} is not attempt {}'s run",
                workflow_run_id,
                dispatch.id,
            )
            return
        if dispatch.workflow_run_id is None:
            # The report came before the run id was written: it is evidence
            # that the provider was asked, so the attempt is this run's.
            await _link_run(dispatch.id, organization_id, workflow_run_id)
        from api.services.workflow import answered

        extracted = _extracted(run)
        reached = _word(extracted.get("reached"))
        if not answered.was_answered(run):
            await _apply(dispatch.id, rc.NO_ANSWER, "not_answered", source="post_call")
            return
        if reached == "voicemail":
            await _apply(dispatch.id, rc.NO_ANSWER, "voicemail", source="post_call")
            return
        if reached != "person":
            # Somebody else, or nobody said who: they heard only the
            # content-free line. The reminder reaches the person in the app.
            why_not = "someone_else" if reached == "someone_else" else "not_confirmed"
            if await _apply(dispatch.id, rc.ANSWERED, why_not, source="post_call"):
                await fallback(dispatch.id, why_not)
            return
        moved = await _apply(dispatch.id, rc.ANSWERED, None, source="post_call")
        said = await _task_from_call(dispatch.id, extracted)
        if moved is not None:
            await _say_answered(dispatch.id, said)
    except Exception as exc:  # noqa: BLE001 - post-call work must not fail a call
        logger.error(
            "reminder_calls: could not record run {}: {}", workflow_run_id, exc
        )


async def _say_answered(dispatch_id: int, said: str | None) -> None:
    dispatch, occurrence, schedule = await _rows(dispatch_id)
    if dispatch is None or occurrence is None or schedule is None:
        return
    at = _local(dispatch.dialled_at or occurrence.due_at, schedule.timezone)
    line = f"I called you with your reminder at {at}."
    if said:
        line = f"{line} {said}"
    await say(dispatch_id, line, state=rc.ANSWERED)


async def _set_task(
    occurrence_id: int,
    state: str,
    *,
    snoozed_until: datetime | None = None,
    session: Any = None,
) -> bool:
    """Move the task from open only. With ``session``, inside the caller's
    transaction (which commits); otherwise in its own."""
    query = (
        update(ReminderCallOccurrenceModel)
        .where(
            ReminderCallOccurrenceModel.id == occurrence_id,
            ReminderCallOccurrenceModel.task_state == rc.OPEN,
        )
        .values(task_state=state, snoozed_until=snoozed_until, updated_at=_now())
        .returning(ReminderCallOccurrenceModel.id)
    )
    if session is not None:
        return (await session.execute(query)).first() is not None
    async with db_client.async_session() as own:
        moved = (await own.execute(query)).first()
        await own.commit()
    return moved is not None


_SNOOZE = re.compile(r"^\s*(\d{1,4})\s*(m|min|mins|minute|minutes)?\s*$", re.IGNORECASE)


def snooze_minutes(value: Any) -> int | None:
    """How long the person asked to snooze, when they said it clearly and
    within ``SNOOZE_MIN_MINUTES``-``SNOOZE_MAX_MINUTES``: "15", "15 min".
    None for anything else -- nothing said, "later", "a bit", 300 -- which
    is asked back, never guessed."""
    match = _SNOOZE.match(str(value or ""))
    if not match:
        return None
    minutes = int(match.group(1))
    if not policy.SNOOZE_MIN_MINUTES <= minutes <= policy.SNOOZE_MAX_MINUTES:
        return None
    return minutes


def snooze_unclear_words(title: str) -> str:
    """What the thread says when a snooze's length was not clear."""
    return (
        "You asked me to call again later, but I didn't catch when, so I "
        f"haven't set another call. Your reminder: “{title}”. How many "
        f"minutes from now should I call ({policy.SNOOZE_MIN_MINUTES} to "
        f"{policy.SNOOZE_MAX_MINUTES})?"
    )


async def _snooze(
    dispatch: Any, occurrence: Any, schedule: Any, minutes: int
) -> str | None:
    """Snoozed, and its follow-up ring, in one transaction: a crash between
    the two cannot leave a snoozed task with nothing to ring it again."""
    base = dispatch.dialled_at or _now()
    until = base + timedelta(minutes=minutes)
    async with db_client.async_session() as session:
        if not await _set_task(
            occurrence.id, rc.SNOOZED, snoozed_until=until, session=session
        ):
            await session.rollback()
            return None
        await _new_occurrence(session, schedule, until, _now())
        await session.commit()
    return f"You asked me to call again at {_local(until, schedule.timezone)}."


async def _task_from_call(dispatch_id: int, extracted: dict[str, Any]) -> str | None:
    """What the person said on the call moves the task, from open only (so
    a duplicate report changes nothing). Returns the thread's words."""
    dispatch, occurrence, schedule = await _rows(dispatch_id)
    if dispatch is None or occurrence is None or schedule is None:
        return None
    reply = _word(extracted.get("reminder_reply"))
    if reply == "done":
        return (
            "You said it's done."
            if await _set_task(occurrence.id, rc.USER_REPORTED_DONE)
            else None
        )
    if reply == "cancel":
        return (
            "You asked me to cancel it."
            if await _set_task(occurrence.id, rc.TASK_CANCELLED)
            else None
        )
    if reply == "snooze":
        minutes = snooze_minutes(extracted.get("snooze_minutes"))
        if minutes is None:
            # Never guessed: the task stays open and the person is asked.
            return (
                snooze_unclear_words(schedule.title)
                if occurrence.task_state == rc.OPEN
                else None
            )
        return await _snooze(dispatch, occurrence, schedule, minutes)
    return None


async def sweep(now: datetime | None = None) -> int:
    """Attempts with no outcome after ``ANSWER_MINUTES`` are reconciled
    against their runs -- settled on evidence, or ``unknown`` and said so,
    never "not answered" for want of a report. ``unknown`` attempts are
    re-read for ``RECONCILE_HOURS``. Returns how many changed state."""
    if not features.on_anywhere(rc.FLAG):
        return 0
    now = now or _now()
    cutoff = now - timedelta(minutes=policy.ANSWER_MINUTES)
    horizon = now - timedelta(hours=policy.RECONCILE_HOURS)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(ReminderCallDispatchModel.id, ReminderCallDispatchModel.state)
                .where(
                    (
                        ReminderCallDispatchModel.state.in_(
                            (rc.DISPATCHING, rc.ACCEPTED)
                        )
                        & (ReminderCallDispatchModel.reserved_at < cutoff)
                    )
                    | (
                        (ReminderCallDispatchModel.state == rc.UNKNOWN)
                        & (ReminderCallDispatchModel.reserved_at >= horizon)
                    )
                )
                .order_by(ReminderCallDispatchModel.id)
                .limit(500)
            )
        ).all()
    changed = 0
    for dispatch_id, before in rows:
        if await reconcile(dispatch_id, now=now) != before:
            changed += 1
    return changed

"""The call that says it is done: queued, placed once, and never silent.

``queue`` is called when a finish settles a person's callback. It writes
(or joins) the person's one ``queued`` call -- the partial unique index on
``done_calls`` makes two finishes, two workers or two ticks land on the same
row -- and says on the thread when the call will come:

* within calling hours, after a short gather (``CALL_WHEN_DONE_GATHER_SECONDS``)
  so tasks that finish together are one call;
* outside them, at the next opening (09:00 in the person's own timezone,
  else the organisation's), and the thread says so. The window holds for
  calls the person asked for too: asking is not a reason to ring at night.

At most ``CALL_WHEN_DONE_DAILY_CAP`` calls are placed for one person in one
of their days. Past it, the finish still reaches them -- in the thread and on
their notification channels -- and the thread says the day's limit is
reached. Checked when the call is queued and again right before the dial.

``tick`` (every minute, from the ARQ worker) claims each due call by a
compare-and-swap (queued -> calling), the way care's ``_claim`` does, and
``place`` asks ``dnd.assert_may_call`` -- calling window enforced -- right
before ``dial_workflow``. Outside hours it goes back to the queue for the
next opening; a listed number is never rung.

Where no call can be placed (no line, no confirmed number, a listed number,
a refused dial) the person is told in the thread and on their own
notification channels, and the thread says why. Nothing fails silently.

The call is metered exactly as every outbound call is: ``dial_workflow``
holds the concurrency slot and the quota check, and post-call costing
prices the run like any other. Nothing here adds a price.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.call_when_done_models import DoneCallbackModel, DoneCallModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import call_when_done as cwd
from api.services import features
from api.services.call_when_done import number as numbers
from api.services.compliance import dnd

#: Why no call was placed, as the thread says it.
WHY = {
    "needs_setup": "this workspace has no phone line for calling out yet",
    "no_number": "you have not confirmed a number for me to ring",
    "do_not_call": "your number is on this workspace's do-not-call list",
    "line_busy": "every line was busy",
    "quota": "the workspace could not pay for the call",
    "call_error": "something went wrong on our side",
    "not_answered": "you did not pick up",
}


def why(reason: str) -> str:
    if reason == "daily_cap":
        cap = constants.CALL_WHEN_DONE_DAILY_CAP
        return f"the day's call limit is reached ({cap} call{'s' if cap != 1 else ''} a day)"
    return WHY.get(reason, WHY["call_error"])


def _now() -> datetime:
    return datetime.now(UTC)


async def org_timezone(organization_id: int) -> str | None:
    """The organisation's zone: the calling window's proxy (see dnd)."""
    from api.services.organization_preferences import get_organization_preferences

    try:
        prefs = await get_organization_preferences(organization_id, db=db_client)
        return getattr(prefs, "timezone", None)
    except Exception as exc:  # noqa: BLE001 - dnd falls back to the default zone
        logger.warning(
            "call_when_done: no timezone for org {}: {}", organization_id, exc
        )
        return None


async def person_timezone(organization_id: int, user_id: int) -> str | None:
    """The zone the calling window is judged in: the person's own (they are
    the one being rung), else the organisation's, else dnd's default."""
    from api.services import member_preferences
    from api.services.today.scope import valid_zone

    own = valid_zone(await member_preferences.timezone_of(user_id))
    return own or await org_timezone(organization_id)


async def calls_today(user_id: int, timezone_name: str | None, now: datetime) -> int:
    """Calls actually dialled for this person since their local midnight.
    Counted per person (every workspace they are rung from), because the cap
    protects the person, not a workspace's budget."""
    zone = dnd.resolve_zone(timezone_name)
    midnight = datetime.combine(now.astimezone(zone).date(), time(0), tzinfo=zone)
    async with db_client.async_session() as session:
        count = await session.scalar(
            select(func.count(DoneCallModel.id)).where(
                DoneCallModel.user_id == user_id,
                DoneCallModel.workflow_run_id.is_not(None),
                DoneCallModel.placed_at >= midnight.astimezone(UTC),
            )
        )
    return int(count or 0)


async def over_cap(user_id: int, timezone_name: str | None, now: datetime) -> bool:
    return await calls_today(user_id, timezone_name, now) >= max(
        0, constants.CALL_WHEN_DONE_DAILY_CAP
    )


def next_opening(timezone_name: str | None, now: datetime) -> datetime:
    """When the calling window next opens, after ``now`` (UTC)."""
    zone = dnd.resolve_zone(timezone_name)
    local = now.astimezone(zone)
    start = dnd._parse_hhmm(constants.CALLING_HOURS_START, time(9, 0))
    candidate = datetime.combine(local.date(), start, tzinfo=zone)
    if candidate <= local:
        candidate = datetime.combine(
            local.date() + timedelta(days=1), start, tzinfo=zone
        )
    return candidate.astimezone(UTC)


def when_line(due: datetime, timezone_name: str | None) -> str:
    """ "9:00" -- a due time as the person reads it, in the window's zone."""
    local = due.astimezone(dnd.resolve_zone(timezone_name))
    return f"{local.hour}:{local.minute:02d}"


async def readiness(
    organization_id: int, user_id: int
) -> tuple[str | None, str | None]:
    """``(reason, phone)``: why no call can be placed (None when one can),
    and the confirmed number. Never a "ready" that is not."""
    try:
        line = await db_client.get_default_telephony_configuration(organization_id)
    except Exception as exc:  # noqa: BLE001 - a status read
        logger.warning("call_when_done: could not read telephony: {}", exc)
        line = None
    if not line:
        return "needs_setup", None
    phone = await numbers.confirmed(organization_id, user_id)
    if not phone:
        return "no_number", None
    return None, phone


# --- queueing ---------------------------------------------------------------


async def queue(
    organization_id: int,
    user_id: int,
    callback_ids: list[int],
    *,
    thread_id: str | None,
    now: datetime | None = None,
) -> int | None:
    """Attach finished callbacks to the person's one queued call (made if
    there is none) and say on the thread when it will come. Where no call
    can be placed, tell them in the app now instead. Returns the call id."""
    now = now or _now()
    reason, _ = await readiness(organization_id, user_id)
    tz = await person_timezone(organization_id, user_id)
    if reason is None and await over_cap(user_id, tz, now):
        reason = "daily_cap"
    if reason:
        async with db_client.async_session() as session:
            row = DoneCallModel(
                organization_id=organization_id,
                user_id=user_id,
                state=cwd.NOTIFIED,
                reason=reason,
                due_at=now,
                thread_id=thread_id,
                created_at=now,
            )
            session.add(row)
            await session.flush()
            call_id = row.id
            await _attach(session, organization_id, call_id, callback_ids)
            await session.commit()
        await tell_in_app(call_id, reason)
        return call_id

    due = now + timedelta(seconds=max(0, constants.CALL_WHEN_DONE_GATHER_SECONDS))
    outside = not dnd.within_calling_hours(timezone_name=tz, now=due)
    if outside:
        due = next_opening(tz, now)
    async with db_client.async_session() as session:
        made = (
            await session.execute(
                insert(DoneCallModel)
                .values(
                    organization_id=organization_id,
                    user_id=user_id,
                    state=cwd.QUEUED,
                    due_at=due,
                    thread_id=thread_id,
                    created_at=now,
                    attempts=0,
                )
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "user_id"],
                    # A literal, as in the index: a bound parameter cannot be
                    # matched to a partial index's predicate.
                    index_where=text("state = 'queued'"),
                )
                .returning(DoneCallModel.id)
            )
        ).first()
        joined = made is None
        call = await session.scalar(
            select(DoneCallModel).where(
                DoneCallModel.organization_id == organization_id,
                DoneCallModel.user_id == user_id,
                DoneCallModel.state == cwd.QUEUED,
            )
        )
        if call is None:  # placed between the two statements: queue afresh
            await session.rollback()
            return await queue(
                organization_id, user_id, callback_ids, thread_id=thread_id, now=now
            )
        call_id, due = call.id, call.due_at
        await _attach(session, organization_id, call_id, callback_ids)
        await session.commit()

    items = await _items(organization_id, callback_ids)
    title = items[0]["title"] if items else "your task"
    lead = (
        f"Done: {title}."
        if not any(i["needs_you"] for i in items)
        else (f"Finished: {title}. It needs you.")
    )
    if outside:
        line = (
            f"{lead} I'll call you at {when_line(due, tz)}, outside calling hours now."
        )
    elif joined:
        line = f"{lead} I'll tell you about it on the same call."
    else:
        line = f"{lead} I'll call you in a minute with the result."
    await notice(
        organization_id,
        user_id,
        thread_id,
        line,
        call_id=call_id,
        state=cwd.QUEUED,
        due_at=due,
    )
    return call_id


async def _attach(session, organization_id: int, call_id: int, ids: list[int]) -> None:
    if not ids:
        return
    await session.execute(
        update(DoneCallbackModel)
        .where(
            DoneCallbackModel.id.in_(ids),
            DoneCallbackModel.organization_id == organization_id,
        )
        .values(call_id=call_id)
    )


async def _items(organization_id: int, callback_ids: list[int]) -> list[dict[str, Any]]:
    if not callback_ids:
        return []
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(DoneCallbackModel)
                .where(
                    DoneCallbackModel.id.in_(callback_ids),
                    DoneCallbackModel.organization_id == organization_id,
                )
                .order_by(DoneCallbackModel.id)
            )
        ).all()
    return [_item(r) for r in rows]


def _item(row: Any) -> dict[str, Any]:
    return {
        "title": row.title or "your task",
        "summary": row.summary or "",
        "output": row.output or "",
        "needs_you": bool(row.needs_you),
    }


async def items_of_call(organization_id: int, call_id: int) -> list[dict[str, Any]]:
    """Every finished task one call says, once each (by what finished)."""
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(DoneCallbackModel)
                .where(
                    DoneCallbackModel.call_id == call_id,
                    DoneCallbackModel.organization_id == organization_id,
                )
                .order_by(DoneCallbackModel.id)
            )
        ).all()
    seen: set[str] = set()
    out = []
    for row in rows:
        key = row.finished_key or f"cb:{row.id}"
        if row.finished_key is None and row.title in seen:
            continue
        if key in seen:
            continue
        seen.update({key, row.title or ""})
        out.append(_item(row))
    return out


# --- the thread, and the person's own channels ------------------------------


async def notice(
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    line: str,
    *,
    call_id: int,
    state: str,
    due_at: datetime | None = None,
    reason: str | None = None,
) -> None:
    """One line from Decibyl on the person's conversation, theirs alone."""
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
                "call_id": call_id,
                "state": state,
                "due_at": due_at.isoformat() if due_at else None,
                "reason": reason,
            },
        },
        in_channel=False,
        thread_id=thread_id,
    )


async def tell_in_app(call_id: int, reason: str) -> None:
    """No call (or no answer): the result in the thread and on the person's
    own notification channels, with why. Never raises."""
    from api.services.identity import notifications

    try:
        async with db_client.async_session() as session:
            call = await session.get(DoneCallModel, call_id)
        if call is None:
            return
        items = await items_of_call(call.organization_id, call_id)
        title = "; ".join(i["title"] for i in items) or "your task"
        summary = " ".join(i["summary"] for i in items if i["summary"]).strip()
        link = f"/overview?thread={call.thread_id}" if call.thread_id else "/overview"
        sent = await notifications.notify(
            call.user_id,
            topic="task_updates",
            title=f"Done: {title}"[:200],
            body=(summary or "It has finished. Open Decibyl to see the result.")[:500],
            link=link,
            dedupe_key=f"call-when-done:{call_id}:{reason}",
        )
        async with db_client.async_session() as session:
            await session.execute(
                update(DoneCallModel)
                .where(
                    DoneCallModel.id == call_id,
                    DoneCallModel.organization_id == call.organization_id,
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
        said = why(reason)
        if reason == "not_answered":
            line = f"I called about {title}, but {said}. {summary} {where}"
        else:
            line = f"Done: {title}. I couldn't call you: {said}. {summary} {where}"
        await notice(
            call.organization_id,
            call.user_id,
            call.thread_id,
            " ".join(line.split()),
            call_id=call_id,
            state=call.state,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - the call row keeps the reason
        logger.error("call_when_done: could not tell call {} in app: {}", call_id, exc)


# --- the scheduler ----------------------------------------------------------


async def tick(now: datetime | None = None) -> int:
    """Place every queued call that has come due. Returns calls handled."""
    if not features.on_anywhere(cwd.FLAG):
        return 0
    now = now or _now()
    async with db_client.async_session() as session:
        due = (
            await session.execute(
                select(DoneCallModel.id, DoneCallModel.organization_id)
                .where(DoneCallModel.state == cwd.QUEUED, DoneCallModel.due_at <= now)
                .order_by(DoneCallModel.due_at)
                .limit(200)
            )
        ).all()
    handled = 0
    for call_id, organization_id in due:
        if not cwd.enabled(organization_id):
            continue
        if not await _claim(call_id, now):
            continue
        await place(call_id, now=now)
        handled += 1
    return handled


async def _claim(call_id: int, now: datetime) -> bool:
    """queued -> calling, once: the tick that moved it places it."""
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(DoneCallModel)
                .where(DoneCallModel.id == call_id, DoneCallModel.state == cwd.QUEUED)
                .values(
                    state=cwd.CALLING,
                    placed_at=now,
                    attempts=DoneCallModel.attempts + 1,
                )
                .returning(DoneCallModel.id)
            )
        ).first()
        await session.commit()
    return bool(moved)


async def place(call_id: int, *, now: datetime | None = None) -> None:
    """Ring the person for one claimed call. Never raises: a refusal is the
    call's state, and the person is told."""
    now = now or _now()
    async with db_client.async_session() as session:
        call = await session.get(DoneCallModel, call_id)
    if call is None or call.state != cwd.CALLING:
        return
    organization_id, user_id = call.organization_id, call.user_id
    reason, phone = await readiness(organization_id, user_id)
    if reason:
        await settle(call_id, cwd.NOTIFIED, reason=reason)
        await tell_in_app(call_id, reason)
        return
    tz = await person_timezone(organization_id, user_id)
    if await over_cap(user_id, tz, now):
        # Re-checked here: calls placed since this one was queued count.
        await settle(call_id, cwd.NOTIFIED, reason="daily_cap")
        await tell_in_app(call_id, "daily_cap")
        return
    try:
        # The hard rule, asked immediately before dialling: the calling
        # window (enforced, never dropped) and the do-not-call list.
        dialable = await dnd.assert_may_call(
            organization_id, phone, timezone_name=tz, now=now, db=db_client
        )
    except dnd.OutsideCallingHours:
        await _requeue(call, next_opening(tz, now), tz)
        return
    except dnd.CallRefused:
        await settle(call_id, cwd.FAILED, reason="do_not_call")
        await tell_in_app(call_id, "do_not_call")
        return
    try:
        run_id = await _dial(call, dialable)
    except _Refused as exc:
        await settle(call_id, cwd.FAILED, reason=exc.reason)
        await tell_in_app(call_id, exc.reason)
        return
    except Exception as exc:  # noqa: BLE001 - the call must say something
        logger.error("call_when_done: call {} failed: {}", call_id, exc)
        await settle(call_id, cwd.FAILED, reason="call_error")
        await tell_in_app(call_id, "call_error")
        return
    async with db_client.async_session() as session:
        await session.execute(
            update(DoneCallModel)
            .where(
                DoneCallModel.id == call_id,
                DoneCallModel.organization_id == organization_id,
            )
            .values(workflow_run_id=run_id)
        )
        await session.commit()


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _dial(call: Any, dialable: str) -> int:
    """The platform's outbound path: the same one care's reminder calls use."""
    from api.services import member_preferences
    from api.services.call_when_done import agent
    from api.services.telephony.factory import get_telephony_provider_by_id
    from api.services.telephony.outbound import (
        OutboundRefused,
        QuotaExhausted,
        dial_workflow,
    )

    config = await db_client.get_default_telephony_configuration(call.organization_id)
    if not config:
        raise _Refused("needs_setup")
    prefs = await member_preferences.get(call.user_id)
    items = await items_of_call(call.organization_id, call.id)
    workflow = await agent.ensure_workflow(call.organization_id, user_id=call.user_id)
    provider = await get_telephony_provider_by_id(config.id, call.organization_id)
    try:
        return await dial_workflow(
            workflow=workflow,
            organization_id=call.organization_id,
            to_number=dialable,
            provider=provider,
            telephony_configuration_id=config.id,
            source="call_when_done",
            extra_context={
                "trigger_source": "call_when_done",
                "done_call_id": call.id,
                **agent.call_context(
                    items=items,
                    language=prefs.get("language"),
                    person=prefs.get("preferred_name") or "",
                ),
            },
        )
    except QuotaExhausted as exc:
        raise _Refused("quota") from exc
    except OutboundRefused as exc:
        raise _Refused("line_busy") from exc


async def _requeue(call: Any, due: datetime, tz: str | None) -> None:
    """Back to the queue for the next opening. If another finish queued a
    call meanwhile, its tasks join this one: one call, never two."""
    async with db_client.async_session() as session:
        other = await session.scalar(
            select(DoneCallModel.id).where(
                DoneCallModel.organization_id == call.organization_id,
                DoneCallModel.user_id == call.user_id,
                DoneCallModel.state == cwd.QUEUED,
                DoneCallModel.id != call.id,
            )
        )
        if other is not None:
            await session.execute(
                update(DoneCallbackModel)
                .where(
                    DoneCallbackModel.call_id == other,
                    DoneCallbackModel.organization_id == call.organization_id,
                )
                .values(call_id=call.id)
            )
            await session.execute(
                delete(DoneCallModel).where(DoneCallModel.id == other)
            )
        await session.execute(
            update(DoneCallModel)
            .where(DoneCallModel.id == call.id, DoneCallModel.state == cwd.CALLING)
            .values(state=cwd.QUEUED, due_at=due, placed_at=None)
        )
        await session.commit()
    await notice(
        call.organization_id,
        call.user_id,
        call.thread_id,
        f"It's outside calling hours now, so I'll call you at {when_line(due, tz)}.",
        call_id=call.id,
        state=cwd.QUEUED,
        due_at=due,
    )


# --- what came of it --------------------------------------------------------


async def settle(call_id: int, state: str, *, reason: str | None = None) -> bool:
    """Move a call from calling to its outcome, once."""
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(DoneCallModel)
                .where(DoneCallModel.id == call_id, DoneCallModel.state == cwd.CALLING)
                .values(state=state, reason=reason, outcome_at=_now())
                .returning(DoneCallModel.id)
            )
        ).first()
        await session.commit()
    return bool(moved)


async def record_run_outcome(workflow_run_id: int) -> None:
    """Post-call: if this run was an "it's done" call, settle it. Never
    raises; any other run is left alone."""
    try:
        run = await db_client.get_workflow_run(workflow_run_id)
        context = getattr(run, "initial_context", None) or {}
        call_id = context.get("done_call_id")
        if run is None or not call_id:
            return
        organization_id = await db_client.get_organization_id_by_workflow_run_id(
            workflow_run_id
        )
        async with db_client.async_session() as session:
            call = await session.get(DoneCallModel, int(call_id))
        # The run's workspace must be the call's: an id in a context is not
        # proof of ownership.
        if call is None or call.organization_id != organization_id:
            logger.warning(
                "call_when_done: run {} names a call it does not own", workflow_run_id
            )
            return
        from api.services.workflow import answered

        if answered.was_answered(run):
            await settle(call.id, cwd.ANSWERED)
            return
        if await settle(call.id, cwd.NOT_ANSWERED, reason="not_answered"):
            await tell_in_app(call.id, "not_answered")
    except Exception as exc:  # noqa: BLE001 - post-call work must not fail a call
        logger.error(
            "call_when_done: could not record run {}: {}", workflow_run_id, exc
        )


async def sweep(now: datetime | None = None) -> int:
    """Calls with no outcome after ``CALL_WHEN_DONE_ANSWER_MINUTES`` were not
    answered; the person is told in the app."""
    if not features.on_anywhere(cwd.FLAG):
        return 0
    now = now or _now()
    cutoff = now - timedelta(minutes=constants.CALL_WHEN_DONE_ANSWER_MINUTES)
    async with db_client.async_session() as session:
        ids = (
            await session.scalars(
                select(DoneCallModel.id)
                .where(
                    DoneCallModel.state == cwd.CALLING, DoneCallModel.placed_at < cutoff
                )
                .limit(500)
            )
        ).all()
    settled = 0
    for call_id in ids:
        if await settle(call_id, cwd.NOT_ANSWERED, reason="not_answered"):
            await tell_in_app(call_id, "not_answered")
            settled += 1
    return settled

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
reached. Read when the call is queued, and **reserved** right before the
dial (``allowance.reserve``, atomic across workspaces).

What came of a dial is decided on evidence, never on silence:

* ``dial_workflow`` records the call's run (``_link_run``) before it asks
  the provider. A call with no run recorded was never requested: a worker
  that died, or a refusal, is a **verified non-dispatch** -- its slot is
  released and it is queued again (at most ``MAX_ATTEMPTS`` claims).
* A dial that raised after the run was recorded (a provider timeout after
  the carrier accepted) is ``unknown``: never re-dialled, never "I couldn't
  call you", reconciled against the run right away and by every sweep.
* The sweep reconciles a call still ``calling`` after the answer window
  (``reconcile``): the carrier's no-answer makes it ``not_answered``, an
  answered run ``answered``, and anything else ``unknown``, said to the
  person as exactly that, with the result.
* The post-call report (``record_run_outcome``) is evidence too: it moves
  ``calling`` or ``unknown`` to the truth, and an answer corrects an earlier
  ``not_answered``. Each change is appended to ``outcome_history``; a
  duplicate report changes nothing; a notice is sent once per call, plus a
  correction only when an earlier one said something false.

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
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.call_when_done_models import DoneCallbackModel, DoneCallModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import call_when_done as cwd
from api.services import features
from api.services.call_when_done import allowance
from api.services.call_when_done import number as numbers
from api.services.compliance import dnd
from api.services.telephony import call_evidence

#: Why no call was placed, as the thread says it.
WHY = {
    "needs_setup": "this workspace has no phone line for calling out yet",
    "no_number": "you have not confirmed a number for me to ring",
    "do_not_call": "your number is on this workspace's do-not-call list",
    "line_busy": "every line was busy",
    "quota": "the workspace could not pay for the call",
    "call_error": "something went wrong on our side",
    "not_answered": "you did not pick up",
    "not_member": "you are no longer in this workspace",
    "not_connected": "the call could not be connected",
    "unknown": "I can't confirm the call reached you",
}

#: How many times one call is claimed and found not dialled (a worker that
#: died before the provider was asked) before the person is told instead.
MAX_ATTEMPTS = 3

#: An unknown outcome is re-read against its run for this long after the
#: dial; after that it stays unknown (the person was already told so).
RECONCILE_HOURS = 24


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
    """Calls reserved for this person on their local day, from every
    workspace: dialled, being dialled, or with an unknown outcome
    (``allowance``)."""
    return await allowance.used(user_id, timezone_name, now)


async def over_cap(user_id: int, timezone_name: str | None, now: datetime) -> bool:
    """A read, for saying so early (``queue``). The dial itself takes its
    slot with ``allowance.reserve``, which is what actually holds the cap."""
    return await allowance.remaining(user_id, timezone_name, now) <= 0


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


async def _their_hours(
    organization_id: int, user_id: int, tz: str | None, due: datetime
) -> datetime:
    """``due``, or later when the person asked not to be rung then
    (services/personal, ``evolve_personal``). Never earlier: their hours
    narrow the calling window, they cannot widen it."""
    from api.services.personal import preferences as personal_preferences

    window = await personal_preferences.call_window(user_id, organization_id)
    later = personal_preferences.held_until(window, tz, due)
    return later if later is not None and later > due else due


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
    due = now + timedelta(seconds=max(0, constants.CALL_WHEN_DONE_GATHER_SECONDS))
    outside = not dnd.within_calling_hours(timezone_name=tz, now=due)
    if outside:
        due = next_opening(tz, now)
    due = await _their_hours(organization_id, user_id, tz, due)
    # The allowance of the day the call will ring: a finish at 23:30 rings
    # at 09:00 tomorrow, on tomorrow's five.
    if reason is None and await over_cap(user_id, tz, due):
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
        elif reason == "unknown":
            line = f"I tried to call you about {title}, but {said}. {summary} {where}"
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
        await _not_placed(call_id, cwd.NOTIFIED, reason)
        return
    if await db_client.get_membership(user_id, organization_id) is None:
        # Removed from the workspace since the call was queued: this
        # workspace no longer rings them, and its thread is not theirs.
        await allowance.release(call_id)
        await settle(call_id, cwd.FAILED, reason="not_member")
        return
    tz = await person_timezone(organization_id, user_id)
    if not dnd.within_calling_hours(timezone_name=tz, now=now):
        # Asked here as well as in the gate: the gate skips every check,
        # the window included, when do-not-call enforcement is switched off
        # for a deployment, and the window for these calls is not optional.
        await _requeue(call, next_opening(tz, now), tz)
        return
    # The hours the person asked for ("call me after 10"), inside the
    # window above: it can only hold a call later, never ring one earlier.
    later = await _their_hours(organization_id, user_id, tz, now)
    if later > now:
        await _requeue(call, later, tz)
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
        await _not_placed(call_id, cwd.FAILED, "do_not_call")
        return
    # The person's daily allowance, taken atomically: two workspaces ringing
    # the same person at once cannot both have the last slot. Last of the
    # checks, so a call refused for any other reason never holds one.
    if not await allowance.reserve(call_id, tz, now):
        await _not_placed(call_id, cwd.NOTIFIED, "daily_cap")
        return

    async def link(run_id: int) -> None:
        # Only while this worker still holds the claim. If the sweep has
        # meanwhile judged it never dialled (and queued it again), raising
        # here stops dial_workflow before the provider is asked: one call.
        if not await _link_run(call_id, organization_id, run_id, claimed=True):
            raise _Superseded()

    try:
        run_id = await _dial(call, dialable, on_run_created=link)
    except _Superseded:
        logger.warning("call_when_done: call {} was re-queued mid-dial", call_id)
        return
    except _Refused as exc:
        # Refused before the provider was asked: nothing rang.
        await _not_placed(call_id, cwd.FAILED, exc.reason)
        return
    except Exception as exc:  # noqa: BLE001 - the call must say something
        logger.error(
            "call_when_done: call {} raised while dialling: {!r}", call_id, exc
        )
        await _after_dial_error(call_id, now)
        return
    await _link_run(call_id, organization_id, run_id)


async def _not_placed(call_id: int, state: str, reason: str) -> None:
    """A verified non-dispatch: the slot (if any) goes back, the call takes
    its final state, and the person is told why."""
    await allowance.release(call_id)
    if await settle(call_id, state, reason=reason):
        await tell_in_app(call_id, reason)


class _Superseded(Exception):
    """The claim was taken back (re-queued) before the provider was asked."""


async def _link_run(
    call_id: int, organization_id: int, run_id: int, *, claimed: bool = False
) -> bool:
    """Record which run this call is. Written before the provider is asked
    (``dial_workflow``'s ``on_run_created``), and again after, harmlessly.
    With ``claimed``, only while the call is still ``calling``. Returns
    whether a row was written."""
    query = update(DoneCallModel).where(
        DoneCallModel.id == call_id,
        DoneCallModel.organization_id == organization_id,
    )
    if claimed:
        query = query.where(DoneCallModel.state == cwd.CALLING)
    async with db_client.async_session() as session:
        written = (
            await session.execute(
                query.values(workflow_run_id=run_id).returning(DoneCallModel.id)
            )
        ).first()
        await session.commit()
    return written is not None


async def _after_dial_error(call_id: int, now: datetime) -> None:
    """The dial raised for a reason that is not a refusal. With no run
    recorded, the provider was never asked: queued again (bounded). With a
    run recorded, it may have rung: ``unknown``, reconciled, never dialled
    again and never "I couldn't call you"."""
    async with db_client.async_session() as session:
        call = await session.get(DoneCallModel, call_id)
    if call is None or call.state != cwd.CALLING:
        return
    if call.workflow_run_id is None:
        await _retry_undialled(call, now, source="dial_error")
        return
    await reconcile(call_id, now=now, unknown_reason="dial_unconfirmed")


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _dial(call: Any, dialable: str, *, on_run_created=None) -> int:
    """The platform's outbound path: the same one care's reminder calls use.

    ``on_run_created`` is handed to ``dial_workflow``, which awaits it with
    the run id before asking the provider to dial."""
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
    # "Tamil for calls" (services/personal): the person's own call language,
    # read here, not remembered by a model. Falls back to their language.
    from api.services.personal import preferences as personal_preferences

    language = await personal_preferences.language_for(
        call.user_id, "calls", call.organization_id
    ) or prefs.get("language")
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
                    language=language,
                    person=prefs.get("preferred_name") or "",
                ),
            },
            on_run_created=on_run_created,
        )
    except QuotaExhausted as exc:
        raise _Refused("quota") from exc
    except OutboundRefused as exc:
        raise _Refused("line_busy") from exc
    except dnd.CallRefused as exc:
        # The calling number is not declared (predeclaration): refused
        # before a run or a slot exists.
        raise _Refused("call_error") from exc


async def _requeue(
    call: Any, due: datetime, tz: str | None, *, say: bool = True
) -> bool:
    """Back to the queue (from ``calling``) for ``due``. If another finish
    queued a call meanwhile, its tasks join this one: one call, never two.
    Only for a call that was never dialled. Returns whether it moved."""
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
        moved = (
            await session.execute(
                update(DoneCallModel)
                .where(
                    DoneCallModel.id == call.id,
                    DoneCallModel.state == cwd.CALLING,
                    DoneCallModel.workflow_run_id.is_(None),
                )
                .values(state=cwd.QUEUED, due_at=due, placed_at=None)
                .returning(DoneCallModel.id)
            )
        ).first()
        if moved is None:
            await session.rollback()
            return False
        await session.commit()
    if say:
        await notice(
            call.organization_id,
            call.user_id,
            call.thread_id,
            f"It's outside calling hours now, so I'll call you at {when_line(due, tz)}.",
            call_id=call.id,
            state=cwd.QUEUED,
            due_at=due,
        )
    return True


async def _retry_undialled(call: Any, now: datetime, *, source: str) -> None:
    """A claimed call the provider was never asked to place (no run is
    recorded, and ``dial_workflow`` records it before asking): its slot goes
    back and it is queued again, quietly -- nothing rang, so nothing is
    "not answered". After ``MAX_ATTEMPTS`` claims, the person is told."""
    await allowance.release(call.id)
    if (call.attempts or 0) >= MAX_ATTEMPTS:
        if await settle(call.id, cwd.NOTIFIED, reason="call_error"):
            await tell_in_app(call.id, "call_error")
        return
    tz = await person_timezone(call.organization_id, call.user_id)
    due = (
        now
        if dnd.within_calling_hours(timezone_name=tz, now=now)
        else next_opening(tz, now)
    )
    if await _requeue(call, due, tz, say=False):
        await _append_history(call.id, cwd.CALLING, cwd.QUEUED, "not_dialled", source)


# --- what came of it --------------------------------------------------------


def _entry(prior: str, to: str, reason: str | None, source: str) -> dict[str, Any]:
    return {
        "at": _now().isoformat(),
        "from": prior,
        "to": to,
        "reason": reason,
        "source": source,
    }


async def _append_history(
    call_id: int, prior: str, to: str, reason: str | None, source: str
) -> None:
    async with db_client.async_session() as session:
        call = (
            await session.execute(
                select(DoneCallModel)
                .where(DoneCallModel.id == call_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if call is None:
            return
        call.outcome_history = [
            *(call.outcome_history or []),
            _entry(prior, to, reason, source),
        ]
        await session.commit()


async def _move(
    call_id: int,
    state: str,
    *,
    reason: str | None,
    allowed_from: tuple[str, ...],
    source: str,
) -> str | None:
    """Compare-and-swap a call's state, appending to its history. Returns
    the state it moved from, or None when it did not move (not in
    ``allowed_from``, or already there: a duplicate report is a no-op)."""
    async with db_client.async_session() as session:
        call = (
            await session.execute(
                select(DoneCallModel)
                .where(DoneCallModel.id == call_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if call is None or call.state not in allowed_from or call.state == state:
            await session.rollback()
            return None
        prior = call.state
        call.state = state
        call.reason = reason
        call.outcome_at = _now()
        call.outcome_history = [
            *(call.outcome_history or []),
            _entry(prior, state, reason, source),
        ]
        await session.commit()
    return prior


async def settle(call_id: int, state: str, *, reason: str | None = None) -> bool:
    """Move a call from calling to its outcome, once."""
    return (
        await _move(
            call_id, state, reason=reason, allowed_from=(cwd.CALLING,), source="settle"
        )
        is not None
    )


#: Where each kind of evidence may move a call from. An answer corrects an
#: earlier "not answered"; nothing moves a call out of ``answered``.
_FROM = {
    cwd.ANSWERED: (cwd.CALLING, cwd.UNKNOWN, cwd.NOT_ANSWERED),
    cwd.NOT_ANSWERED: (cwd.CALLING, cwd.UNKNOWN),
    cwd.FAILED: (cwd.CALLING, cwd.UNKNOWN),
}


async def _apply(call_id: int, state: str, reason: str | None, source: str) -> bool:
    """Move a call on evidence, and say what needs saying -- once:

    * ``calling`` -> not answered / could not connect: the person is told,
      with the result.
    * ``unknown`` -> anything: they were already told it was unknown, with
      the result; nothing more is said.
    * ``not_answered`` -> answered: if the person was told "you did not pick
      up" (it went straight from ``calling``), that was wrong, so a
      one-line correction goes on the thread. If they were told "unknown",
      nothing they read was false, and nothing is added.
    """
    told_missed = state == cwd.ANSWERED and await _told_not_answered(call_id)
    prior = await _move(
        call_id, state, reason=reason, allowed_from=_FROM[state], source=source
    )
    if prior is None:
        return False
    if prior == cwd.CALLING and state in (cwd.NOT_ANSWERED, cwd.FAILED):
        await tell_in_app(call_id, reason or "call_error")
    elif prior == cwd.NOT_ANSWERED and told_missed:
        await _correct(call_id)
    return True


async def _told_not_answered(call_id: int) -> bool:
    """Whether "you did not pick up" was said: the call went from
    ``calling`` straight to ``not_answered`` (``_apply`` tells then)."""
    async with db_client.async_session() as session:
        history = await session.scalar(
            select(DoneCallModel.outcome_history).where(DoneCallModel.id == call_id)
        )
    return any(
        e.get("from") == cwd.CALLING and e.get("to") == cwd.NOT_ANSWERED
        for e in history or []
    )


async def _correct(call_id: int) -> None:
    async with db_client.async_session() as session:
        call = await session.get(DoneCallModel, call_id)
    if call is None:
        return
    items = await items_of_call(call.organization_id, call_id)
    title = "; ".join(i["title"] for i in items) or "your task"
    await notice(
        call.organization_id,
        call.user_id,
        call.thread_id,
        f"Correction: my call about {title} did reach you, so please ignore my "
        "note that you did not pick up.",
        call_id=call_id,
        state=cwd.ANSWERED,
        reason="corrected",
    )


def _evidence_state(evidence: str) -> tuple[str, str | None] | None:
    if evidence == call_evidence.ANSWERED:
        return cwd.ANSWERED, None
    if evidence == call_evidence.NOT_CONNECTED:
        return cwd.NOT_ANSWERED, "not_answered"
    if evidence == call_evidence.CARRIER_FAILED:
        return cwd.FAILED, "not_connected"
    return None


async def reconcile(
    call_id: int, *, now: datetime | None = None, unknown_reason: str = "no_outcome"
) -> str | None:
    """Read the call's run and settle it on what the run proves.

    * No run recorded: never requested -- released and queued again.
    * Final evidence (answered, carrier no-answer, carrier failure): moved
      there (and, from ``calling``, the person told).
    * Anything else: ``unknown`` (from ``calling``), said to the person once,
      with the result.

    Returns the call's state afterwards. Never dials."""
    now = now or _now()
    async with db_client.async_session() as session:
        call = await session.get(DoneCallModel, call_id)
    if call is None or call.state not in (cwd.CALLING, cwd.UNKNOWN):
        return call.state if call else None
    if call.workflow_run_id is None:
        if call.state == cwd.CALLING:
            await _retry_undialled(call, now, source="sweep")
        return await _state(call_id)
    evidence, _run = await call_evidence.read(
        call.workflow_run_id, call.organization_id
    )
    final = _evidence_state(evidence)
    if final is not None:
        await _apply(call_id, final[0], final[1], source=f"reconcile:{evidence}")
    elif call.state == cwd.CALLING:
        moved = await _move(
            call_id,
            cwd.UNKNOWN,
            reason=unknown_reason,
            allowed_from=(cwd.CALLING,),
            source=f"reconcile:{evidence}",
        )
        if moved is not None:
            # Once: only the reading that moved it out of ``calling`` says so.
            await tell_in_app(call_id, "unknown")
    return await _state(call_id)


async def _state(call_id: int) -> str | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(DoneCallModel.state).where(DoneCallModel.id == call_id)
        )


async def record_run_outcome(workflow_run_id: int) -> None:
    """Post-call: if this run was an "it's done" call, settle it on what the
    run shows. Late evidence repairs a provisional state (``unknown``) or a
    wrong one (``not_answered`` -> ``answered``); a duplicate is a no-op.
    Never raises; any other run is left alone."""
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
        # The run's workspace must be the call's, and the run must be the
        # call's own (when one is recorded): an id in a context is not proof
        # of ownership.
        if call is None or call.organization_id != organization_id:
            logger.warning(
                "call_when_done: run {} names a call it does not own", workflow_run_id
            )
            return
        if call.workflow_run_id not in (None, workflow_run_id):
            logger.warning(
                "call_when_done: run {} is not call {}'s run ({})",
                workflow_run_id,
                call.id,
                call.workflow_run_id,
            )
            return
        if call.workflow_run_id is None:
            await _link_run(call.id, organization_id, workflow_run_id)
        from api.services.workflow import answered

        if answered.was_answered(run):
            await _apply(call.id, cwd.ANSWERED, None, source="post_call")
            return
        await _apply(call.id, cwd.NOT_ANSWERED, "not_answered", source="post_call")
    except Exception as exc:  # noqa: BLE001 - post-call work must not fail a call
        logger.error(
            "call_when_done: could not record run {}: {}", workflow_run_id, exc
        )


async def sweep(now: datetime | None = None) -> int:
    """Calls with no outcome after ``CALL_WHEN_DONE_ANSWER_MINUTES`` are
    reconciled against their runs (``reconcile``): settled on evidence, or
    ``unknown`` and said so -- never "not answered" for want of a report.
    Calls already ``unknown`` are re-read for ``RECONCILE_HOURS``, so late
    evidence lands. Returns how many changed state."""
    if not features.on_anywhere(cwd.FLAG):
        return 0
    now = now or _now()
    cutoff = now - timedelta(minutes=constants.CALL_WHEN_DONE_ANSWER_MINUTES)
    horizon = now - timedelta(hours=RECONCILE_HOURS)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(DoneCallModel.id, DoneCallModel.state)
                .where(
                    (
                        (DoneCallModel.state == cwd.CALLING)
                        & (DoneCallModel.placed_at < cutoff)
                    )
                    | (
                        (DoneCallModel.state == cwd.UNKNOWN)
                        & (DoneCallModel.placed_at >= horizon)
                    )
                )
                .order_by(DoneCallModel.id)
                .limit(500)
            )
        ).all()
    changed = 0
    for call_id, before in rows:
        after = await reconcile(call_id, now=now)
        if after != before:
            changed += 1
    return changed

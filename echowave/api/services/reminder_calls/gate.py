"""Every check before a reminder call rings, in one function, in order.

``may_dial`` runs immediately before the dial (``calls.place``), on a
dispatch already claimed (``dispatching``), and answers the first check
that says no, with the reason stored on the dispatch:

a. the occurrence is still open (not cancelled, snoozed or done) and was
   made under the schedule's current version, and the schedule is active
   -- ``cancelled``, ``superseded``;
b. the person is still a member of the workspace (``not_member``), the
   workspace still has a line (``no_line``), and the number is still the
   confirmed one, confirmed by an adult (``no_number``, ``not_adult``);
c. quiet hours: ``policy.CALLING_WINDOW_START``-``_END`` (09:00-21:00 by
   default) in the person's zone, asked of ``dnd.within_calling_hours``
   **directly**, never through the do-not-call switch. No approved exception exists for general reminders (D2)
   -- ``quiet_hours``;
d. the do-not-call list (``dnd.assert_may_call``, fail closed, window
   already decided above) -- ``dnd``;
e. the person's daily cap, reserved atomically (``allowance.hold``, the
   shared per-person local-day counter, D3) -- ``cap``. Last of the checks
   that refuse, so a call refused for any other reason holds no slot.

f. Spend (the workspace can pay for the call) is ``dial_workflow``'s own
   check, right after, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from api.db import db_client
from api.db.reminder_call_models import (
    ReminderCallDispatchModel,
    ReminderCallOccurrenceModel,
    ReminderCallScheduleModel,
)
from api.services import reminder_calls as rc
from api.services.compliance import dnd
from api.services.reminder_calls import number, policy

#: The reasons, in the order they are asked.
ORDER = (
    "cancelled",
    "superseded",
    "not_member",
    "no_line",
    "no_number",
    "not_adult",
    "quiet_hours",
    "dnd",
    "cap",
)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None
    #: The number to dial, in E.164, as the do-not-call gate handed it back.
    dialable: str | None = None
    timezone: str | None = None


def _no(reason: str, tz: str | None = None) -> Verdict:
    return Verdict(ok=False, reason=reason, timezone=tz)


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


def quiet_exception_covers(schedule: Any, now: datetime) -> bool:
    """Whether an approved exception covers this ring. Never while D2 allows
    none for general reminders (``schedule.quiet_exception`` stays NULL).
    If D2 is decided the other way, an exception is data on the schedule,
    approved on its card, for one occurrence's window only."""
    # Fails closed either way until that exception's shape is agreed: the
    # switch alone never opens the window.
    del schedule, now
    return False


async def may_dial(dispatch_id: int, now: datetime) -> Verdict:
    dispatch, occurrence, schedule = await _rows(dispatch_id)
    if dispatch is None or occurrence is None or schedule is None:
        return _no("cancelled")
    org = dispatch.organization_id
    if occurrence.organization_id != org or schedule.organization_id != org:
        return _no("cancelled")
    tz = schedule.timezone

    # a. Still wanted, as confirmed.
    if occurrence.task_state != rc.OPEN or schedule.state != rc.ACTIVE:
        return _no("cancelled", tz)
    if occurrence.schedule_version != schedule.version:
        return _no("superseded", tz)

    # b. Still theirs to receive, on the number they confirmed.
    if await db_client.get_membership(schedule.user_id, org) is None:
        return _no("not_member", tz)
    try:
        line = await db_client.get_default_telephony_configuration(org)
    except Exception:  # noqa: BLE001 - no line known is no line
        line = None
    if not line:
        return _no("no_line", tz)
    found = await number.on_file(org, schedule.user_id)
    if found is None or found["phone"] != schedule.phone:
        return _no("no_number", tz)
    if policy.REQUIRE_ADULT_CONFIRMATION and not found["adult"]:
        return _no("not_adult", tz)

    # c. Quiet hours, asked directly: the window is not the list's to drop.
    if not policy.within_window(tz, now):
        if not quiet_exception_covers(schedule, now):
            return _no("quiet_hours", tz)

    # d. The do-not-call list, fail closed.
    try:
        dialable = await dnd.assert_may_call(
            org,
            schedule.phone,
            timezone_name=tz,
            now=now,
            enforce_calling_hours=False,
            db=db_client,
        )
    except dnd.CallRefused:
        return _no("dnd", tz)

    # e. The person's day: one slot per ring, reserved atomically.
    from api.services.call_when_done import allowance

    if policy.RETRY_COUNTS_AGAINST_CAP or dispatch.attempt == 1:
        if not await allowance.hold(
            ReminderCallDispatchModel,
            dispatch_id,
            timezone_name=tz,
            now=now,
            user_id=schedule.user_id,
        ):
            return _no("cap", tz)
    return Verdict(ok=True, dialable=dialable, timezone=tz)

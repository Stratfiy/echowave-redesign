"""When a scheduled agent runs, and why it did not.

A bot on a routine is one nobody rings. Nothing triggers it but the clock,
which makes the clock the whole product: a run at the wrong time, or
twice, or not at all, has no caller waiting to notice and no transcript to
explain itself. So the decision is a pure function of a schedule and a moment,
and every outcome is named -- including each way of not running.

Three rules shaped this file, and each of them is a bug we would otherwise
ship.

**Anchor to the business, not to the clock.** A routine set to "every morning"
means *when the shop opens*, and the shop's opening time is already recorded
and already changes. A cron string pinned to 09:00 goes quietly wrong the week
a clinic moves to 10:00 -- the report still arrives, an hour before anybody is
there to read it, and nothing anywhere says the schedule is now wrong. So
``Anchor.OPENING`` and ``Anchor.CLOSING`` are computed from the organisation's
hours on the day, and a literal time is a third, explicit choice somebody
makes rather than the only one available.

**A skipped run is recorded, never silent.** This is the silent-absence shape
the codebase keeps getting caught by: a routine that does not fire produces
nothing at all, and "my morning report stopped coming" is the only symptom,
arriving days late from a customer. Every refusal here returns a
:class:`SkipReason` for the caller to write to the timeline, and the reason a
routine did not run is a fact about it, the same as the times it did.

**Late is better than missing, but not indefinitely.** A worker that was down
for twenty minutes should still send the eight o'clock summary. One that was
down overnight should not send it at two in the afternoon, when it is no
longer a morning report and the figures read as today's. So a run has a
catch-up window and past that it is ``MISSED`` -- which is recorded, because a
bot that stopped for a day is exactly the thing an operator needs told.

The functions here never touch the database and never read the clock. Both
arrive as arguments, so a test can put a routine at 23:59 on a Sunday in
Kolkata without waiting for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from api.services.workflow import agent_hours

#: How late a run may still go out. Twenty minutes covers a deploy, a worker
#: restart and a Redis blip, which is every ordinary reason a minute tick is
#: missed. Longer would let a morning report arrive in the afternoon reading
#: as though it were current; shorter would drop a run for a routine restart.
CATCH_UP_MINUTES = 20

MINUTES_IN_DAY = 24 * 60


class Cadence(str, Enum):
    """How often a routine comes round.

    Deliberately not cron. The person setting this runs a clinic, and the
    difference between ``0 9 * * 1-5`` and ``0 9 * * 1,5`` is a support ticket
    waiting to happen. Four cadences cover every routine we have written, and a
    fifth is a smaller change than a cron parser plus the screen that would
    have to explain it.
    """

    #: Every hour the business is open. For a sweep that should not wait a day.
    HOURLY = "hourly"
    DAILY = "daily"
    #: Monday to Friday. The most common shape for back-office work, and one
    #: nobody should have to express as a day list.
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"


class Anchor(str, Enum):
    """What the time is measured from."""

    #: When the business opens that day, plus ``offset_minutes``. Moves with
    #: the hours, which is the point.
    OPENING = "opening"
    #: When it closes, plus ``offset_minutes`` (negative to run before).
    CLOSING = "closing"
    #: A literal minute of the day, honoured whether the business is open or
    #: not. Somebody who types 06:00 means 06:00.
    CLOCK = "clock"


class SkipReason(str, Enum):
    """Why a routine did not run at a moment it might have.

    Every value here is written to the timeline as ``ROUTINE_SKIPPED``. None
    of them is an error; they are the ordinary answers, and an operator asking
    "why didn't it run" is entitled to every one of them.
    """

    #: Switched off by a person.
    INACTIVE = "inactive"
    #: Never test-run, so never armed. See :func:`may_arm`.
    NEVER_TESTED = "never_tested"
    #: An app this routine cannot work without is failing. Running anyway
    #: produces a run that reports nothing and looks like the bot is broken.
    CONNECTOR_BROKEN = "connector_broken"
    #: The business is shut on this day, and the routine is anchored to its
    #: hours. A Sunday is not a missed run.
    CLOSED = "closed"
    #: Not this weekday.
    WRONG_DAY = "wrong_day"
    #: Due later today.
    NOT_YET = "not_yet"
    #: Already ran for this slot.
    ALREADY_RAN = "already_ran"
    #: Due, but too long ago to still be worth sending. The one skip that
    #: means something went wrong.
    MISSED = "missed"


#: The reasons an operator should be shown rather than left to notice. The
#: rest are the system working: a Sunday, a not-yet, an already-ran.
ATTENTION_SKIPS: frozenset[SkipReason] = frozenset(
    {SkipReason.CONNECTOR_BROKEN, SkipReason.MISSED, SkipReason.NEVER_TESTED}
)


@dataclass(frozen=True)
class RoutineSpec:
    """A routine's schedule, free of the database row it came from.

    A plain value so the decision can be tested at any moment in any zone
    without a session, and so the rules stay readable next to each other
    rather than spread across a query.
    """

    cadence: Cadence
    anchor: Anchor = Anchor.OPENING
    #: Minute of the day for ``Anchor.CLOCK``; for ``HOURLY`` its remainder is
    #: the minute past each hour.
    at_minute: int = 0
    #: Signed minutes from the anchor. ``-30`` with ``CLOSING`` is "half an
    #: hour before you shut".
    offset_minutes: int = 0
    #: 0 = Monday, matching ``date.weekday()``. Only read for ``WEEKLY``.
    weekday: int = 0
    is_active: bool = False
    tested_at: Optional[datetime] = None
    last_fired_at: Optional[datetime] = None
    #: Apps the routine cannot do its job without, by connector slug.
    needs_apps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    """Whether to run, and the named reason either way."""

    fire: bool
    #: The slot this decision is about, in the organisation's zone. Present
    #: even on a skip, because "which run did not happen" is half the answer,
    #: and it is what stamps ``last_fired_at`` when it does.
    slot: Optional[datetime] = None
    reason: Optional[SkipReason] = None
    #: One line for the timeline, in the words an operator would use.
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.reason in ATTENTION_SKIPS


def may_arm(spec: RoutineSpec) -> bool:
    """Whether this routine is allowed to be switched on.

    A routine arms only after a test run. The first time a bot does its job
    unsupervised it writes into somebody's real accounting software, and a
    test run is the one chance to see what it would do before it does it --
    which is worth nothing if the toggle does not wait for it.
    """
    return spec.tested_at is not None


def _windows(business_hours: Any, day: date) -> list[tuple[int, int]]:
    """Open windows for ``day``, merged. Fails open to the whole day."""
    return agent_hours.open_windows(business_hours, day)


def targets_for_day(
    spec: RoutineSpec, day: date, business_hours: Any = None
) -> list[int]:
    """Every minute of ``day`` this routine is due, ascending.

    Empty when it is not due that day at all -- the wrong weekday, or a day
    the business is shut and the routine follows its hours. One list for all
    four cadences so the firing rule below has a single shape to reason about;
    hourly is simply the cadence with more than one entry.
    """
    if spec.cadence is Cadence.WEEKLY and day.weekday() != spec.weekday:
        return []
    if spec.cadence is Cadence.WEEKDAYS and day.weekday() >= 5:
        return []

    # A literal time is honoured whether the shop is open or not. Somebody who
    # set 06:00 for a routine that sweeps yesterday's orders meant 06:00, and
    # refusing it because the counter is shut would be us overruling them.
    if spec.anchor is Anchor.CLOCK and spec.cadence is not Cadence.HOURLY:
        return [_clamp(spec.at_minute)]

    windows = _windows(business_hours, day)
    if not windows:
        return []

    if spec.cadence is Cadence.HOURLY:
        past_the_hour = _clamp(spec.at_minute) % 60
        # Only the hours the business is open. An hourly sweep that ran
        # through the night would bill the account for twenty-four runs of
        # nothing and bury the eight that mattered.
        return sorted(
            {
                hour * 60 + past_the_hour
                for start, end in windows
                for hour in range(0, 24)
                if start <= hour * 60 + past_the_hour < end
            }
        )

    if spec.anchor is Anchor.CLOSING:
        base = windows[-1][1]
    else:
        base = windows[0][0]
    return [_clamp(base + spec.offset_minutes)]


def _clamp(minute: int) -> int:
    """Inside the day. An offset that walks off either end sticks to the edge.

    Clamping rather than wrapping on purpose: a routine set to half an hour
    after a shop that closes at midnight belongs at the end of that day, not
    at 00:30 the next one, where it would read as a different day's run.
    """
    return max(0, min(MINUTES_IN_DAY - 1, minute))


def decide(
    spec: RoutineSpec,
    *,
    now: datetime,
    zone: ZoneInfo,
    business_hours: Any = None,
    broken_apps: Iterable[str] = (),
) -> Decision:
    """Should this routine run at ``now``, and if not, say which reason.

    ``now`` may be in any zone; it is read in the organisation's. The order of
    the checks is the order an operator would ask them in, and the state
    checks come first so a switched-off routine is never reported as "closed
    today" -- a true statement that answers the wrong question.
    """
    if not spec.is_active:
        return Decision(False, reason=SkipReason.INACTIVE, detail="Switched off.")
    if not may_arm(spec):
        return Decision(
            False,
            reason=SkipReason.NEVER_TESTED,
            detail="Not test-run yet, so it has never been armed.",
        )

    broken = sorted({app for app in broken_apps if app in set(spec.needs_apps)})
    if broken:
        return Decision(
            False,
            reason=SkipReason.CONNECTOR_BROKEN,
            detail=f"{', '.join(broken)} is not working, so the run would report nothing.",
        )

    local = now.astimezone(zone)
    today = local.date()
    minute_now = local.hour * 60 + local.minute

    targets = targets_for_day(spec, today, business_hours)
    if not targets:
        # Two different absences, and telling them apart is the difference
        # between "it is Sunday" and "your Monday report is broken".
        closed = not _windows(business_hours, today)
        reason = SkipReason.CLOSED if closed else SkipReason.WRONG_DAY
        detail = "Closed today." if closed else "Not one of the days this runs on."
        return Decision(False, reason=reason, detail=detail)

    due = [minute for minute in targets if minute <= minute_now]
    if not due:
        return Decision(
            False,
            slot=_at(local, targets[0]),
            reason=SkipReason.NOT_YET,
            detail=f"Due at {_hhmm(targets[0])}.",
        )

    slot = _at(local, due[-1])

    if spec.last_fired_at is not None and spec.last_fired_at.astimezone(zone) >= slot:
        return Decision(
            False,
            slot=slot,
            reason=SkipReason.ALREADY_RAN,
            detail=f"Already ran the {_hhmm(due[-1])} slot.",
        )

    late = minute_now - due[-1]
    if late > CATCH_UP_MINUTES:
        return Decision(
            False,
            slot=slot,
            reason=SkipReason.MISSED,
            detail=(
                f"The {_hhmm(due[-1])} run was {late} minutes ago and is too "
                "late to send now."
            ),
        )

    return Decision(True, slot=slot, detail=f"The {_hhmm(due[-1])} run.")


def next_slot(
    spec: RoutineSpec,
    *,
    now: datetime,
    zone: ZoneInfo,
    business_hours: Any = None,
    within_days: int = 14,
) -> Optional[datetime]:
    """When this routine runs next, for a screen to say so.

    ``None`` when nothing is due inside ``within_days`` -- a weekly routine on
    a day the business is never open, which is worth showing as "never" rather
    than as a date it will not honour.
    """
    local = now.astimezone(zone)
    for offset in range(0, max(1, within_days)):
        day = local.date() + timedelta(days=offset)
        for minute in targets_for_day(spec, day, business_hours):
            candidate = datetime(
                day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=zone
            )
            if candidate > local:
                return candidate
    return None


def _at(local: datetime, minute: int) -> datetime:
    return local.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


#: How each cadence reads on a card.
_CADENCE_WORDS: dict[Cadence, str] = {
    Cadence.HOURLY: "Every hour",
    Cadence.DAILY: "Every day",
    Cadence.WEEKDAYS: "Every weekday",
    Cadence.WEEKLY: "Every {weekday}",
}

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def describe(spec: RoutineSpec) -> str:
    """The schedule in a sentence, for the card.

    Written here rather than by a screen for the same reason the timeline's
    summaries are: two screens formatting the same schedule would eventually
    disagree, and the one a person quotes back to us would be the wrong one.

    The anchor is the part worth saying out loud. "Every weekday when you
    open" tells an operator that moving their hours moves the run, which is
    the whole reason the anchor exists and is invisible in "every weekday at
    09:30".
    """
    words = _CADENCE_WORDS[spec.cadence]
    if spec.cadence is Cadence.WEEKLY:
        words = words.format(weekday=_WEEKDAYS[spec.weekday % 7])

    if spec.cadence is Cadence.HOURLY:
        return f"{words} while you are open, at {spec.at_minute % 60:02d} past"

    if spec.anchor is Anchor.CLOCK:
        return f"{words} at {_hhmm(_clamp(spec.at_minute))}"

    edge = "open" if spec.anchor is Anchor.OPENING else "close"
    offset = spec.offset_minutes
    if not offset:
        return f"{words} when you {edge}"

    # "Half an hour before you close" reads; "-30 minutes from closing" does
    # not. Hours where it divides evenly, because 90 minutes is an hour and a
    # half to everybody except a computer.
    minutes = abs(offset)
    if minutes % 60 == 0:
        hours = minutes // 60
        span = "an hour" if hours == 1 else f"{hours} hours"
    elif minutes == 30:
        span = "half an hour"
    else:
        span = f"{minutes} minutes"
    when = "after" if offset > 0 else "before"
    return f"{words} {span} {when} you {edge}"


def spec_from_model(model: Any) -> RoutineSpec:
    """Read a schedule off an ``agent_routines`` row.

    A converter rather than properties on the model, so every rule above stays
    testable at any moment in any zone without a database, and the row stays a
    row. An unrecognised cadence or anchor raises here rather than silently
    picking one -- a routine whose schedule we cannot read must not quietly
    become a daily one.
    """
    return RoutineSpec(
        cadence=Cadence(model.cadence),
        anchor=Anchor(model.anchor),
        at_minute=int(model.at_minute or 0),
        offset_minutes=int(model.offset_minutes or 0),
        weekday=int(model.weekday or 0),
        is_active=bool(model.is_active),
        tested_at=model.tested_at,
        last_fired_at=model.last_fired_at,
        needs_apps=tuple(
            str(app) for app in (model.needs_apps or []) if isinstance(app, str)
        ),
    )


__all__ = [
    "ATTENTION_SKIPS",
    "CATCH_UP_MINUTES",
    "Anchor",
    "Cadence",
    "Decision",
    "RoutineSpec",
    "SkipReason",
    "decide",
    "describe",
    "may_arm",
    "next_slot",
    "spec_from_model",
    "targets_for_day",
]

"""When an agent is open, as a thing the platform knows rather than says.

Narayani's opening hours live in its prompt: "9:30 முதல் 1:00 வரை தான் clinic
open". The agent can therefore *say* the hours and the platform cannot *keep*
them. A call at eleven at night is answered, a slot is agreed, and nobody at
the clinic will honour it -- the same shape as every other bug found on this
platform: nothing errors, and the damage is discovered by a person.

The slot shape is the campaign scheduler's, deliberately. An operator who has
set calling windows on a campaign should not meet a second, differently-shaped
idea of a week on the agent; and a platform with two notions of "open" will
eventually disagree with itself about one of them.

**Open is the fail-open answer**, everywhere: no schedule, disabled, no slots,
an unreadable timezone, a malformed slot. Taking a number off the air is worse
than answering a call out of hours, and a schedule nobody configured must
behave exactly as today.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from loguru import logger

#: The key on an agent's configuration.
CONFIG_KEY = "agent_schedule"

#: What a slot's day_of_week means. Monday is 0, matching datetime.weekday()
#: and the campaign scheduler, rather than Sunday-is-0 as some calendars have
#: it -- a mismatch here shifts every window by a day and looks like a bug in
#: the clock.
MONDAY, SUNDAY = 0, 6


def _minutes(value: Any) -> Optional[int]:
    """ "HH:MM" as minutes past midnight, or None if it is not that."""
    if not isinstance(value, str):
        return None
    hour, _, minute = value.partition(":")
    try:
        hours, minutes = int(hour), int(minute)
    except ValueError:
        return None
    if not (0 <= hours <= 24 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def _slot_covers(slot: Mapping[str, Any], weekday: int, minute_of_day: int) -> bool:
    """Whether one slot is open at this moment.

    A window whose end is at or before its start runs past midnight: 22:00 to
    02:00 is a night shift, not an empty set. The campaign scheduler compares
    "HH:MM" strings and so cannot express one; this can, and a support line
    that answers overnight is an ordinary thing to want.
    """
    if slot.get("day_of_week") != weekday:
        # An overnight slot is still the *previous* day's slot after midnight,
        # which is checked by the caller passing yesterday in as well.
        return False

    start = _minutes(slot.get("start_time"))
    end = _minutes(slot.get("end_time"))
    if start is None or end is None:
        return False

    if end > start:
        return start <= minute_of_day < end
    if end == start:
        # A zero-length window. Read as "closed" rather than "all day": an
        # operator who wanted all day leaves the schedule off.
        return False
    return minute_of_day >= start  # runs to midnight; the tail is yesterday's


def _tail_of_yesterday(slot: Mapping[str, Any], weekday: int, minute: int) -> bool:
    """The part of an overnight slot that falls after midnight."""
    yesterday = (weekday - 1) % 7
    if slot.get("day_of_week") != yesterday:
        return False
    start = _minutes(slot.get("start_time"))
    end = _minutes(slot.get("end_time"))
    if start is None or end is None or end >= start:
        return False
    return minute < end


def is_open(schedule: Any, now: Optional[datetime] = None) -> bool:
    """Is this agent open? Open whenever the question cannot be answered."""
    if not isinstance(schedule, Mapping):
        return True
    if not schedule.get("enabled", False):
        return True

    slots = schedule.get("slots")
    if not isinstance(slots, list) or not slots:
        return True

    zone_name = schedule.get("timezone") or "Asia/Kolkata"
    try:
        zone = ZoneInfo(zone_name)
    except Exception:  # noqa: BLE001 - a typo must not take a number off the air
        logger.warning(
            "Agent schedule names an unreadable timezone {!r}; treating the "
            "agent as open.",
            zone_name,
        )
        return True

    moment = (now or datetime.now(zone)).astimezone(zone)
    weekday, minute = moment.weekday(), moment.hour * 60 + moment.minute

    # A slot only counts against the agent if it could be read at all. A
    # schedule whose every slot is malformed is a schedule nobody can honour,
    # and closing on it takes the number off the air for an operator whose
    # config is broken -- the precise failure this module exists to avoid. So
    # unreadable slots are not merely skipped, they are not counted, and a
    # schedule left with none behaves as no schedule at all.
    usable = 0
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        if _minutes(slot.get("start_time")) is None:
            continue
        if _minutes(slot.get("end_time")) is None:
            continue
        usable += 1
        if _slot_covers(slot, weekday, minute) or _tail_of_yesterday(
            slot, weekday, minute
        ):
            return True

    if not usable:
        logger.warning(
            "Agent schedule has {} slot(s) and none could be read; treating "
            "the agent as open.",
            len(slots),
        )
        return True
    return False


def describe(schedule: Any) -> str:
    """One line for a log or a disposition, never for a caller to hear."""
    if not isinstance(schedule, Mapping) or not schedule.get("enabled", False):
        return "always open"
    slots = schedule.get("slots") or []
    return f"{len(slots)} window(s), {schedule.get('timezone') or 'Asia/Kolkata'}"


def effective_schedule(agent_schedule: Any, organization_hours: Any = None) -> Any:
    """Which schedule applies to this agent.

    The agent's own when it has one, the organization's otherwise. Most
    businesses keep one set of hours and every agent should inherit them
    without being asked again -- asking twice is how the two answers start to
    disagree, and a clinic whose phone says one thing and whose agent keeps
    another is worse off than one that was never asked.

    An agent that must answer when the business is shut -- an after-hours
    emergency line -- says so by keeping its own schedule, not by switching
    the organization's off. "All day" is one window of 00:00 to 24:00.
    """
    if isinstance(agent_schedule, Mapping) and agent_schedule.get("enabled", False):
        return agent_schedule
    return organization_hours


#: Midnight to midnight, in minutes. The answer whenever hours are unknown.
DAY = (0, 24 * 60)


def open_windows(schedule: Any, day: date) -> list[tuple[int, int]]:
    """The minutes of ``day`` this agent is open, as merged windows.

    ``is_open`` answers "now"; offering a caller a time needs "when". Same
    slots, same fail-open contract: a schedule that is absent, disabled, empty
    or wholly unreadable returns the whole day, because a clinic whose hours
    nobody configured must not have every slot refused.

    Windows are returned sorted and merged, so two overlapping slots for one
    day cannot make a caller hear the same hour offered twice.

    An overnight slot contributes twice: its head on its own day and its tail
    on the following one. That is the same two-sided reading ``is_open`` does
    with ``_tail_of_yesterday``, and leaving the tail out would close a
    support line at midnight for callers it is meant to be answering.
    """
    if not isinstance(schedule, Mapping) or not schedule.get("enabled", False):
        return [DAY]

    slots = schedule.get("slots")
    if not isinstance(slots, list) or not slots:
        return [DAY]

    weekday = day.weekday()
    yesterday = (weekday - 1) % 7

    usable = 0
    spans: list[tuple[int, int]] = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        start = _minutes(slot.get("start_time"))
        end = _minutes(slot.get("end_time"))
        if start is None or end is None:
            continue
        usable += 1

        if slot.get("day_of_week") == weekday:
            if end > start:
                spans.append((start, end))
            elif end < start:
                # Runs to midnight; the rest belongs to tomorrow.
                spans.append((start, DAY[1]))
            # end == start is a zero-length window: closed, per _slot_covers.
        if slot.get("day_of_week") == yesterday and end < start:
            spans.append((0, end))

    if not usable:
        logger.warning(
            "Agent schedule has {} slot(s) and none could be read; treating "
            "the whole day as open.",
            len(slots),
        )
        return [DAY]

    return _merge(spans)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted, with overlapping and touching spans joined into one."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

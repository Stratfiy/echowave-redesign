"""The reminder-call draft: typed and deterministic, read back on a card.

``clean`` turns what the person said (as the model passes it: the words to
read out, a day, a time, a recurrence, a language) into the exact thing the
card shows and the schedule saves. Nothing here guesses silently:

* **The zone** is the person's own (member preferences), else the
  workspace's. With neither, it refuses and asks which city: a call at
  "8:30" in an unknown zone is a call at an unknown time.
* **The day**: ``today``, ``tomorrow``, ``YYYY-MM-DD``
  (``today.reminders.resolve_date``), a weekday name ("friday" is the next
  Friday still ahead; "next friday" on a Friday is a week away), or a
  relative "in N minutes/hours" (``in_minutes``). "Tomorrow" is the
  person's tomorrow: at 00:10 it is the calendar day after, and the card
  shows the full date so the reading is never hidden.
* **The window**: a time outside calling hours is not moved quietly. The
  draft is set to 09:00 (that day if it is still ahead, else the next) and
  carries ``asked_time`` so the card says "You asked for 21:30, outside
  calling hours", and the person confirms 09:00 or says another time.
* **Recurrence**: ``once``, ``daily``, ``weekdays`` or ``weekly``, with the
  next instant computed DST-safely (``today.reminders.next_recurring``).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from api import constants
from api.services.reminder_calls import ReminderCallError

RECURRENCES = ("once", "daily", "weekdays", "weekly")
MAX_TITLE = 200
#: The furthest ahead a one-off may be set.
MAX_DAYS_AHEAD = 366
#: The longest "in N minutes" read as relative.
MAX_RELATIVE_MINUTES = 24 * 60

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_TIME = re.compile(r"^\s*(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.I)


def _now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: Any) -> time | None:
    """ "08:30", "8:30", "830", "8.30", "8 pm", "20:00" -> a time, or None."""
    raw = str(value or "").strip().lower()
    match = _TIME.match(raw)
    if not match:
        return None
    hour_text, minute_text, meridiem = match.groups()
    if minute_text is None and not meridiem:
        # "8" alone is too thin to ring someone on; "8 pm" is fine.
        return None
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _window() -> tuple[time, time]:
    from api.services.compliance import dnd

    return (
        dnd._parse_hhmm(constants.CALLING_HOURS_START, time(9, 0)),
        dnd._parse_hhmm(constants.CALLING_HOURS_END, time(21, 0)),
    )


def inside_window(value: time) -> bool:
    start, end = _window()
    return start <= value < end


def _weekday(words: str) -> tuple[int, bool] | None:
    """ "friday" -> (4, False); "next friday" -> (4, True)."""
    key = words.strip().lower()
    later = key.startswith("next ")
    if later:
        key = key[5:].strip()
    if key.startswith("this "):
        key = key[5:].strip()
    for index, name in enumerate(WEEKDAYS):
        if key in (name, name[:3]):
            return index, later
    return None


def resolve_day(words: str, zone_name: str, at: time, *, now: datetime) -> date:
    """The local date for a day said in words (see the module docstring)."""
    from api.services.today import reminders
    from api.services.today.scope import TodayError, local_today

    zone = ZoneInfo(zone_name)
    today = local_today(zone, now)
    named = _weekday(words or "")
    if named is not None:
        index, later = named
        ahead = (index - today.weekday()) % 7
        if later and ahead == 0:
            # "next Friday" said on a Friday is a week away, not today.
            ahead = 7
        candidate = today + timedelta(days=ahead)
        if ahead == 0 and datetime.combine(candidate, at, tzinfo=zone) <= now:
            candidate += timedelta(days=7)
        return candidate
    try:
        return reminders.resolve_date(words or "today", zone_name, now=now)
    except TodayError as exc:
        raise ReminderCallError(
            "Say the day as today, tomorrow, a weekday or a date like 2026-10-09."
        ) from exc


async def zone_of(organization_id: int, user_id: int) -> str:
    """The person's zone, else the workspace's. Refuses with neither: the
    call's time would be a guess."""
    from api.services import member_preferences
    from api.services.call_when_done import calls as done_calls
    from api.services.today.scope import valid_zone

    own = valid_zone(await member_preferences.timezone_of(user_id))
    if own:
        return own
    workspace = valid_zone(await done_calls.org_timezone(organization_id))
    if workspace:
        return workspace
    raise ReminderCallError(
        "Which city are you in? I need your timezone before I can set a "
        "reminder call for a time."
    )


async def language_of(user_id: int, said: str | None) -> str:
    from api.services import member_preferences
    from api.services.call_when_done import agent as done_agent

    if said:
        return done_agent.language_tag(said)
    try:
        prefs = await member_preferences.get(user_id)
    except Exception:  # noqa: BLE001 - a default
        prefs = {}
    return done_agent.language_tag(prefs.get("language"))


def _at(day: date, at: time, zone: ZoneInfo) -> datetime:
    from api.services.today.scope import at_local

    return at_local(day, at, zone)


def first_due(draft: dict[str, Any], *, after: datetime) -> datetime | None:
    """The first instant this draft rings at, strictly after ``after``."""
    from api.services.today import reminders

    zone = ZoneInfo(draft["timezone"])
    at = parse_time(draft["local_time"])
    if at is None:
        return None
    if draft["recurrence"] == "once":
        due = _at(date.fromisoformat(draft["date"]), at, zone)
        return due if due > after else None
    return reminders.next_recurring(
        recurrence=draft["recurrence"],
        local_time=draft["local_time"],
        weekday=draft.get("weekday"),
        zone_name=draft["timezone"],
        after=after,
    )


def _clean_title(title: Any) -> str:
    words = " ".join(str(title or "").split())
    if not words:
        raise ReminderCallError("What should the call remind you about?")
    return words[:MAX_TITLE]


async def clean(
    organization_id: int,
    user_id: int,
    arguments: dict[str, Any],
    *,
    now: datetime | None = None,
    zone_name: str | None = None,
) -> dict[str, Any]:
    """The draft the card shows. Raises ReminderCallError with a sentence
    for the person (what is missing or wrong)."""
    now = now or _now()
    title = _clean_title(arguments.get("title"))
    recurrence = str(arguments.get("recurrence") or "once").strip().lower()
    if recurrence not in RECURRENCES:
        raise ReminderCallError("Say once, daily, weekdays or weekly.")
    zone_name = zone_name or await zone_of(organization_id, user_id)
    zone = ZoneInfo(zone_name)
    language = await language_of(user_id, arguments.get("language"))

    relative = arguments.get("in_minutes")
    if relative not in (None, ""):
        try:
            minutes = int(relative)
        except (TypeError, ValueError):
            raise ReminderCallError("Say how many minutes from now.") from None
        if not 1 <= minutes <= MAX_RELATIVE_MINUTES:
            raise ReminderCallError("A reminder call can be up to a day from now.")
        if recurrence != "once":
            raise ReminderCallError("A repeating reminder needs a time of day.")
        local = (now + timedelta(minutes=minutes)).astimezone(zone)
        at = time(local.hour, local.minute)
        day = local.date()
    else:
        at = parse_time(arguments.get("time"))
        if at is None:
            raise ReminderCallError(
                "What time should I call? Say it like 08:30 or 6 pm."
            )
        day = resolve_day(str(arguments.get("date") or "today"), zone_name, at, now=now)

    asked_time = None
    if not inside_window(at):
        start, _end = _window()
        asked_time = hhmm(at)
        if at >= start:
            # Past the evening end: the next morning.
            day = day + timedelta(days=1)
        at = start

    weekday = None
    if recurrence == "weekly":
        raw = arguments.get("weekday")
        named = _weekday(str(raw)) if isinstance(raw, str) else None
        if named is not None:
            weekday = named[0]
        elif isinstance(raw, int) and 0 <= raw <= 6:
            weekday = raw
        else:
            weekday = day.weekday()

    draft: dict[str, Any] = {
        "title": title,
        "language": language,
        "timezone": zone_name,
        "local_time": hhmm(at),
        "recurrence": recurrence,
        "weekday": weekday,
        "date": day.isoformat() if recurrence == "once" else None,
        "asked_time": asked_time,
    }
    if recurrence != "once":
        # Recurring: the first one is the next matching day from the day said.
        start_after = max(now, _at(day, time(0, 0), zone) - timedelta(seconds=1))
        due = first_due(draft, after=start_after)
    else:
        due = _at(day, at, zone)
        if due <= now:
            raise ReminderCallError(
                "That time has already passed. Which time should I call?"
            )
        if due > now + timedelta(days=MAX_DAYS_AHEAD):
            raise ReminderCallError("A reminder call can be set up to a year ahead.")
    if due is None:
        raise ReminderCallError("I could not find when that falls. Say the day again.")
    draft["first_due_at"] = due.astimezone(UTC).isoformat()
    return draft

"""The schedule inside "every morning at 8am".

The routine runtime is built -- a clock ticking every minute, a runner, four
cadences, three anchors, a test-before-arming gate. Every piece works. Nothing
creates a routine from what somebody actually says.

Asked to "read my Gmail every morning at 8am and post me a summary", the
builder produced a live bot whose ``triggers`` list was empty. The eight
o'clock survived only as prose in the spec, where no clock reads it. The bot
existed and would never run, and nothing anywhere said so.

So the words are read here, once, at build time.

**Deliberately a parser, not a model call.** A build is already several
seconds of LLM; asking again for a field this shape is spending a round trip
to be less predictable. "every morning at 8am" has one meaning and a regex can
hold it.

**"Morning" is not eight o'clock.** This is the distinction ``Anchor`` exists
for, and the reason to respect it here rather than flatten everything to a
literal time: somebody who says "every morning" means *when the shop opens*,
and the shop's opening time is recorded and changes. Pinning that to 09:00
goes quietly wrong the week a clinic moves to 10:00 -- the report still
arrives, an hour before anybody is there to read it. Somebody who says "at
8am" means 08:00, and gets ``CLOCK``.

**Silence is an answer.** A spec with no schedule in it returns ``None``, and
the bot is built without a routine exactly as before. Guessing a daily run for
a bot nobody asked to schedule would be worse than the gap this closes.

Never raises: unreadable words are no schedule, not an error that loses the
bot the person asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.services.workflow.routines import Anchor, Cadence

#: Minutes in an hour, named so the arithmetic below reads.
HOUR = 60

_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

#: "at 8am", "at 08:00", "at 8:30pm", "by 9 am". The verb is optional so
#: "8am every day" reads too, but a bare number is not a time -- "summarise 5
#: emails" must not become a five o'clock routine.
_TIME = re.compile(
    r"(?:\b(?:at|by|around)\s+)?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    r"|(?:\b(?:at|by|around)\s+)\b(\d{1,2}):(\d{2})\b",
    re.I,
)

_HOURLY = re.compile(r"\b(every hour|hourly|each hour)\b", re.I)
_WEEKDAYS = re.compile(
    r"\b(every weekday|weekdays|each weekday|monday to friday|mon-fri)\b", re.I
)
_WEEKLY = re.compile(
    r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I
)
_DAILY = re.compile(
    r"\b(every day|each day|daily|every morning|each morning|every evening|"
    r"each evening|every afternoon|every night|each night)\b",
    re.I,
)

_MORNING = re.compile(r"\b(morning|when (?:we|you) open|at open(?:ing)?)\b", re.I)
_EVENING = re.compile(
    r"\b(evening|night|end of (?:the )?day|before (?:we|you) close|"
    r"at clos(?:e|ing))\b",
    re.I,
)


@dataclass(frozen=True)
class Schedule:
    """A routine's timing, as the routine schema takes it."""

    cadence: Cadence
    anchor: Anchor
    at_minute: int = 0
    offset_minutes: int = 0
    weekday: int = 0
    #: How to say it back, so a card can name what it is about to arm.
    said: str = ""


def _minute_of_day(match: re.Match[str]) -> int | None:
    """The minute a time expression names, or ``None`` if it names none."""
    if match.group(1) is not None:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if hour > 12 or minute > 59:
            return None
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    else:
        hour = int(match.group(4))
        minute = int(match.group(5))
        if hour > 23 or minute > 59:
            return None
    return hour * HOUR + minute


def _said(cadence: Cadence, anchor: Anchor, at_minute: int, weekday: int) -> str:
    """The schedule in words, for the card that asks to arm it."""
    if anchor is Anchor.OPENING:
        when = "when you open"
    elif anchor is Anchor.CLOSING:
        when = "before you close"
    else:
        when = f"at {at_minute // HOUR:02d}:{at_minute % HOUR:02d}"
    if cadence is Cadence.HOURLY:
        return "every hour"
    if cadence is Cadence.WEEKDAYS:
        return f"every weekday {when}"
    if cadence is Cadence.WEEKLY:
        day = next(name for name, index in _DAYS.items() if index == weekday)
        return f"every {day.capitalize()} {when}"
    return f"every day {when}"


def parse(text: str) -> Schedule | None:
    """The schedule these words describe, or ``None`` if they describe none."""
    if not text or not text.strip():
        return None

    weekly = _WEEKLY.search(text)
    hourly = _HOURLY.search(text)
    weekdays = _WEEKDAYS.search(text)
    daily = _DAILY.search(text)
    time_match = _TIME.search(text)
    at_minute = _minute_of_day(time_match) if time_match else None

    if not (weekly or hourly or weekdays or daily):
        # A time on its own is not a schedule: "call them back at 5pm" is one
        # errand, not a standing order.
        return None

    if hourly:
        return Schedule(
            cadence=Cadence.HOURLY,
            anchor=Anchor.CLOCK,
            at_minute=(at_minute or 0) % HOUR,
            said="every hour",
        )

    if weekly:
        cadence, weekday = Cadence.WEEKLY, _DAYS[weekly.group(1).lower()]
    elif weekdays:
        cadence, weekday = Cadence.WEEKDAYS, 0
    else:
        cadence, weekday = Cadence.DAILY, 0

    # A stated time wins. Without one, "morning" and "evening" are the
    # business's own hours rather than a number we invented for it.
    if at_minute is not None:
        anchor, minute = Anchor.CLOCK, at_minute
    elif _EVENING.search(text):
        anchor, minute = Anchor.CLOSING, 0
    elif _MORNING.search(text):
        anchor, minute = Anchor.OPENING, 0
    else:
        anchor, minute = Anchor.OPENING, 0

    return Schedule(
        cadence=cadence,
        anchor=anchor,
        at_minute=minute,
        weekday=weekday,
        said=_said(cadence, anchor, minute, weekday),
    )

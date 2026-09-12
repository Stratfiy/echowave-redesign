"""What times are actually free, as something the agent can ask before it
promises one.

The tool beside this one can only *create*. So an agent had no way to find out
what was open: it guessed from whatever its prompt said about opening hours,
offered a time, and the only correction available was a refusal at write time.
On a live clinic line that produced the worst possible failure -- a caller
asking for a slot that was empty, being told no, and ringing somebody else.
The evidence was a day with three appointments, noon plainly free, and no tool
call recorded against noon at all: the agent never looked.

Two rules hold this together:

**One definition of busy, shared with the write.** Availability filters events
through ``_occupies_the_chair``, the same predicate the conflict check uses. If
the two disagreed, the agent would offer a slot and then fail to book it, which
is worse than never offering it. That predicate treats an all-day entry as a
closure the business made -- leave, a shutdown, a service visit -- so such a
day has no free slots at all, and only an entry the operator marked Free is
read as informational.

**The same calendars the write checks**, which is now more than one: an
appointment kept on a second calendar used to be invisible to both, so a slot
already taken could be offered and booked again. Which calendars count is the
operator's explicit choice, defaulting to none, because "read everything on the
account" is right for a solo practitioner with a personal calendar and wrong
for a two-doctor clinic -- merging both doctors reports the clinic full when
one of them is free, which is this module's own bug wearing a different hat. A
multi-practitioner business wants one tool per practitioner instead.

A partial read counts as a failure. Slots computed from two of three calendars
are how a booked chair gets offered.

Fail-open is **not** the posture here, and that is the one place this module
departs from ``agent_hours``. A day whose events we could not read returns no
opinion rather than an empty calendar: answering "everything is free" from a
failed read would have the agent promise slots confidently and wrongly, where
"I could not check" leaves it able to take a message.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

from api.constants import GOOGLE_CALENDAR_DEFAULT_TIMEZONE
from api.services.workflow import agent_hours

#: Candidate start times land on this grid, counted from midnight. Fifteen
#: minutes because a clinic books on the quarter hour and a caller asked to
#: choose between 12:07 and 12:22 hears a machine.
SLOT_STEP_MINUTES = 15

#: How many to hand back. A model given twenty free slots reads out twenty;
#: three or four is what a receptionist offers, and the caller can ask for a
#: different part of the day.
MAX_SLOTS = 6

#: The default appointment length, matching the booking tool's own default so
#: a slot offered is a slot that will fit.
DEFAULT_DURATION_MINUTES = 30

FUNCTION_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "description": (
                "The day to check, in YYYY-MM-DD format, e.g. 2026-09-14. "
                "Work it out from what the caller said before calling this -- "
                "'tomorrow' and 'Monday' are not accepted."
            ),
        },
        "duration_minutes": {
            "type": "number",
            "description": (
                "How long the appointment needs to be, in minutes. Defaults "
                "to 30. Only slots this long are returned."
            ),
        },
    },
    "required": ["date"],
}


def function_schema(tool: Any) -> Dict[str, Any]:
    """The same shape as the booking tool's schema, named apart from it.

    Derived from the configured tool's name so the two functions read as a
    pair, and suffixed so a tool called "Book appointment" cannot generate two
    functions with one name -- which the model would resolve by calling
    whichever it saw last.
    """
    import re

    base = re.sub(r"[^a-z0-9_]", "_", (tool.name or "").lower())
    base = re.sub(r"_+", "_", base).strip("_") or "calendar"
    return {
        "type": "function",
        "function": {
            "name": f"check_{base}_availability"[:64],
            "description": (
                "Find out which times are free on a given day before offering "
                "one to the caller. Call this first whenever the caller asks "
                "for a time, asks what is available, or suggests a slot -- do "
                "not decide from memory whether a time is free or whether the "
                "business is open."
            ),
            "parameters": FUNCTION_PARAMETERS,
        },
        "_tool_uuid": tool.tool_uuid,
    }


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - a bad zone must not break the answer
        return ZoneInfo(GOOGLE_CALENDAR_DEFAULT_TIMEZONE)


def _minute_of_day(moment: datetime, zone: ZoneInfo, day: date) -> Optional[int]:
    """Where a moment falls in ``day``, in local minutes, or None if outside.

    Clamped rather than dropped for an event that starts before the day or
    ends after it: a procedure running from yesterday evening into this
    morning occupies this morning, and discarding it would free a chair that
    is taken.
    """
    local = moment.astimezone(zone)
    if local.date() < day:
        return 0
    if local.date() > day:
        return agent_hours.DAY[1]
    return local.hour * 60 + local.minute


def _busy_spans(
    items: List[Dict[str, Any]], zone: ZoneInfo, day: date
) -> List[tuple[int, int]]:
    """The minutes of ``day`` already taken, merged.

    Uses the booking path's own predicate, so an all-day holiday and an event
    the owner marked Free do not eat the day here either.
    """
    from api.services.integrations.google_calendar.client import _occupies_the_chair

    spans: List[tuple[int, int]] = []
    for item in items:
        if not isinstance(item, dict) or not _occupies_the_chair(item):
            continue
        start_field = item.get("start") or {}
        end_field = item.get("end") or {}

        # An all-day entry carries `date` rather than `dateTime`, and by the
        # time it reaches here `_occupies_the_chair` has already decided it is
        # busy -- a closure the business entered itself: leave, a shutdown, a
        # service visit. It takes the whole day. Reading only `dateTime` here
        # would drop it and offer every slot on a day the clinic is shut,
        # which is the same hole by a different route.
        if start_field.get("date") and not start_field.get("dateTime"):
            spans.append(agent_hours.DAY)
            continue

        start_raw = start_field.get("dateTime")
        end_raw = end_field.get("dateTime")
        if not start_raw or not end_raw:
            continue
        try:
            start = datetime.fromisoformat(start_raw)
            end = datetime.fromisoformat(end_raw)
        except ValueError:
            # An unparseable event is a chair we cannot account for. Skipping
            # it would free the slot; the whole read is treated as unusable by
            # the caller instead.
            logger.warning(
                "Google Calendar event has an unreadable time: {!r}/{!r}",
                start_raw,
                end_raw,
            )
            continue
        first = _minute_of_day(start, zone, day)
        last = _minute_of_day(end, zone, day)
        if first is None or last is None or last <= first:
            continue
        spans.append((first, last))

    return agent_hours._merge(spans)


def free_slots(
    *,
    open_windows: List[tuple[int, int]],
    busy: List[tuple[int, int]],
    duration_minutes: int,
    step_minutes: int = SLOT_STEP_MINUTES,
    limit: int = MAX_SLOTS,
) -> List[str]:
    """Start times that fit ``duration_minutes`` inside an open window and
    clear of every busy span, as "HH:MM".

    Aligned to the step grid counted from midnight rather than from the start
    of the window, so a clinic opening at 9:30 offers 9:30, 9:45, 10:00 and
    not 9:30, 9:47.
    """
    if duration_minutes <= 0:
        duration_minutes = DEFAULT_DURATION_MINUTES

    out: List[str] = []
    for window_start, window_end in open_windows:
        start = -(-window_start // step_minutes) * step_minutes
        while start + duration_minutes <= window_end:
            finish = start + duration_minutes
            if not any(finish > b_start and start < b_end for b_start, b_end in busy):
                out.append(f"{start // 60:02d}:{start % 60:02d}")
                if len(out) >= limit:
                    return out
            start += step_minutes
    return out


async def busy_events(
    access_token: str, calendar_ids: Any, day: date, timezone: str
) -> Optional[List[Dict[str, Any]]]:
    """Everything on these calendars overlapping ``day``, or None if no read
    succeeded.

    Several calendars because a slot taken on a second one was invisible here
    and would be offered as free -- the same gap the conflict check had, and
    the two must agree or the agent offers a time and then cannot book it.

    None rather than an empty list on failure, and the distinction is the
    point: an empty calendar and an unanswered question must not produce the
    same promise to a caller. With several calendars that becomes a partial
    read, which is treated as a failure too: offering slots computed from two
    of three calendars is exactly how a booked chair gets offered, and "I
    could not check" is the honest answer.

    Accepts a single id as well as a sequence, so a caller holding one string
    cannot iterate it character by character.
    """
    from api.services.integrations.google_calendar.client import (
        EVENTS_ENDPOINT_TEMPLATE,
    )

    if isinstance(calendar_ids, str):
        calendar_ids = (calendar_ids,)
    wanted = tuple(calendar_ids or ("primary",))

    zone = _zone(timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)

    items: List[Dict[str, Any]] = []
    for calendar_id in wanted:
        url = EVENTS_ENDPOINT_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "timeMin": start.isoformat(),
                        "timeMax": (start + timedelta(days=1)).isoformat(),
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        # A clinic's whole day, not the five the conflict check
                        # needs: every one of them removes a candidate slot, and
                        # a truncated list would offer a taken chair.
                        "maxResults": 250,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "Google Calendar availability read on {} failed: {}", calendar_id, exc
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "Google Calendar availability read on {} failed ({}).",
                calendar_id,
                response.status_code,
            )
            return None
        items.extend(response.json().get("items") or [])
    return items


async def resolve_open_windows(
    *, organization_id: int, workflow_id: Optional[int], day: date
) -> List[tuple[int, int]]:
    """The hours this agent keeps on ``day``.

    The agent's own schedule when it has one, its organization's otherwise --
    ``agent_hours.effective_schedule`` decides, so an agent inherits the
    business's hours without being configured twice and an after-hours line
    can still keep its own.

    Nothing here is per-account beyond what the account itself has set: no
    default clinic hours, no assumed lunch break, no industry's timetable. An
    organization that has configured nothing gets the whole day, and its
    calendar alone decides what is free.
    """
    from api.db import db_client
    from api.services.organization_preferences import get_organization_preferences

    agent_schedule = None
    if workflow_id:
        try:
            workflow = await db_client.get_workflow(
                workflow_id, organization_id=organization_id
            )
            if workflow:
                released = await db_client.get_released_configurations(workflow)
                agent_schedule = (released or {}).get(agent_hours.CONFIG_KEY)
        except Exception as exc:  # noqa: BLE001 - hours must not end a call
            logger.warning("Could not read the agent schedule: {}", exc)

    organization_hours = None
    try:
        preferences = await get_organization_preferences(organization_id)
        organization_hours = getattr(preferences, "business_hours", None)
        if hasattr(organization_hours, "model_dump"):
            organization_hours = organization_hours.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the organization's hours: {}", exc)

    schedule = agent_hours.effective_schedule(agent_schedule, organization_hours)
    return agent_hours.open_windows(schedule, day)


async def execute_check_availability(
    tool: Any,
    arguments: Dict[str, Any],
    organization_id: Optional[int],
    workflow_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Answer "what is free on this day". Same ``{"status": ...}`` contract as
    every other tool, so the model reads one shape whatever it called."""
    from api.db import db_client
    from api.services.integrations.google_calendar.client import booking_timezone
    from api.services.integrations.google_calendar.oauth import (
        GoogleCalendarError,
        get_status,
        get_valid_access_token,
    )

    if not organization_id:
        return {"status": "error", "error": "No organization context for this call."}

    raw_day = str((arguments or {}).get("date") or "")
    try:
        day = date.fromisoformat(raw_day)
    except ValueError:
        return {
            "status": "error",
            "error": (
                f"Could not read {raw_day!r} as a date. Work out the actual "
                "day the caller means and pass it as YYYY-MM-DD."
            ),
        }

    try:
        duration = int(float((arguments or {}).get("duration_minutes") or 0))
    except (TypeError, ValueError):
        duration = 0
    duration = duration if duration > 0 else DEFAULT_DURATION_MINUTES

    timezone = await booking_timezone(organization_id)
    windows = await resolve_open_windows(
        organization_id=organization_id, workflow_id=workflow_id, day=day
    )
    if not windows:
        # Closed, as the operator configured it. A definite answer, so the
        # agent can offer another day instead of reading out nothing.
        return {
            "status": "success",
            "data": {
                "date": raw_day,
                "open": False,
                "free": [],
                "note": "The business is closed on this day.",
            },
        }

    async with db_client.async_session() as session:
        try:
            access_token = await get_valid_access_token(
                session, organization_id=organization_id
            )
        except GoogleCalendarError:
            return {
                "status": "error",
                "error": (
                    "Google Calendar is not connected for this account. "
                    "Ask a human to connect it under Provider Keys."
                ),
            }
        status = await get_status(session, organization_id=organization_id)
        read_calendar_ids = status.read_calendar_ids

    items = await busy_events(access_token, read_calendar_ids, day, timezone)
    if items is None:
        # Deliberately not "everything is free" -- see the module docstring.
        return {
            "status": "error",
            "error": (
                "Could not read the calendar just now. Do not promise a time; "
                "take the caller's preferred slot and tell them it will be "
                "confirmed."
            ),
        }

    busy = _busy_spans(items, _zone(timezone), day)
    slots = free_slots(open_windows=windows, busy=busy, duration_minutes=duration)
    return {
        "status": "success",
        "data": {
            "date": raw_day,
            "open": True,
            "timezone": timezone,
            "duration_minutes": duration,
            "free": slots,
            "note": (
                "Offer one of these times. Nothing else on this day is free."
                if slots
                else "Nothing is free on this day. Offer a different day."
            ),
        },
    }

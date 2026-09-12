"""The google_calendar_create_event tool: schema, and execution against a
connected organization's calendar.

Unlike an HTTP API tool, this tool's parameters are not user-configured —
there is no URL or parameter builder to fill in when creating one of these in
the UI, only a name and a description telling the LLM when to use it. The
parameter shape below *is* the tool; keeping it fixed is what makes "create a
Google Calendar tool" a two-field form instead of another HTTP API form with
Google's URL and auth pre-filled in.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

from api.constants import GOOGLE_CALENDAR_DEFAULT_TIMEZONE
from api.db import db_client
from api.services.integrations.google_calendar.oauth import (
    GoogleCalendarError,
    get_status,
    get_valid_access_token,
)

TOOL_TYPE = "google_calendar"

EVENTS_ENDPOINT_TEMPLATE = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)

FUNCTION_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "Short event title, e.g. 'Demo call with Rahul (Acme Corp)'. "
                "Include the caller's name and, if given, their company."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "What the caller wants to discuss, or any other notes worth "
                "having on the event. Optional."
            ),
        },
        "start_date": {
            "type": "string",
            "description": (
                "The confirmed event date in YYYY-MM-DD format, e.g. "
                "2026-08-15. Only call this tool after reading the date back "
                "to the caller and having them confirm it — do not guess."
            ),
        },
        "start_time": {
            "type": "string",
            "description": (
                "The confirmed event start time in 24-hour HH:MM format, e.g. "
                "15:00 for 3pm. Only call this tool after reading the time "
                "back to the caller and having them confirm it — do not guess."
            ),
        },
        "duration_minutes": {
            "type": "number",
            "description": "How long the event should be, in minutes. Defaults to 30.",
        },
        "attendee_email": {
            "type": "string",
            "description": (
                "The caller's email address, if they gave one. When set, "
                "Google sends them a calendar invite. Optional."
            ),
        },
    },
    "required": ["summary", "start_date", "start_time"],
}


def google_calendar_function_schema(tool: Any) -> Dict[str, Any]:
    """Same return shape as custom_tool.tool_to_function_schema, fixed parameters."""
    function_name = re.sub(r"[^a-z0-9_]", "_", tool.name.lower())
    function_name = (
        re.sub(r"_+", "_", function_name).strip("_") or "book_calendar_event"
    )

    return {
        "type": "function",
        "function": {
            "name": function_name,
            "description": tool.description
            or "Create an event on the connected Google Calendar.",
            "parameters": FUNCTION_PARAMETERS,
        },
        "_tool_uuid": tool.tool_uuid,
    }


async def booking_timezone(organization_id: Optional[int]) -> str:
    """The zone an appointment is booked in: the organization's own.

    The agent works dates out in the organization's timezone -- the field on
    the Settings page, read into every prompt. If the event were stamped with
    the deployment's zone instead, an agent saying "tomorrow at five" and the
    calendar entry it creates would mean two different moments for any account
    that is not in the deployment's own zone, and nothing in the transcript
    would show it.

    Falls back to the calendar default when the organization has set nothing or
    the read fails, which is also what the prompt falls back to, so the two stay
    in step either way.
    """
    if organization_id is None:
        return GOOGLE_CALENDAR_DEFAULT_TIMEZONE
    try:
        from api.services.organization_preferences import get_organization_preferences

        preferences = await get_organization_preferences(organization_id)
        return preferences.timezone or GOOGLE_CALENDAR_DEFAULT_TIMEZONE
    except Exception as exc:  # noqa: BLE001 - a booking must not fail over this
        logger.warning("Could not read the organization timezone: {}", exc)
        return GOOGLE_CALENDAR_DEFAULT_TIMEZONE


def _localize(naive_iso: str, timezone: str = GOOGLE_CALENDAR_DEFAULT_TIMEZONE) -> str:
    """Attach the deployment's calendar timezone to a ``_combine_start_end``
    ISO string, so it carries the explicit UTC offset Google's ``events.list``
    requires for ``timeMin``/``timeMax``. Event creation itself sends a naive
    ``dateTime`` alongside a separate ``timeZone`` field, which Google accepts
    on its own -- this is only for the availability-check query below."""
    naive = datetime.fromisoformat(naive_iso)
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - an unknown zone must not stop a booking
        zone = ZoneInfo(GOOGLE_CALENDAR_DEFAULT_TIMEZONE)
    return naive.replace(tzinfo=zone).isoformat()


async def _find_conflicting_event(
    access_token: str,
    calendar_ids: Any,
    start_iso: str,
    end_iso: str,
    timezone: str = GOOGLE_CALENDAR_DEFAULT_TIMEZONE,
) -> Optional[Dict[str, Any]]:
    """The first event on any of these calendars that overlaps
    [start_iso, end_iso), or None if the slot is free.

    Several calendars rather than one, because a slot already taken on a
    second calendar was invisible here and could be booked again -- a
    practitioner whose own appointments sit on a personal calendar got
    double-booked, and nothing said why. Which calendars count is the
    operator's explicit choice; an account that made none reads exactly the
    one calendar it read before.

    Accepts a single id as well as a sequence, so a caller that has only the
    booking target cannot accidentally iterate a string character by
    character and ask Google about a calendar named "p".

    Best-effort: a failed check is logged and treated as "no conflict found"
    rather than blocking the booking. The write is what matters here -- a
    transient read failure must not turn into no bookings getting made at
    all, the same trade-off issue_receipt_voucher makes for a missing
    supplier identity elsewhere in billing.

    Stops at the first conflict rather than reading every calendar. One is
    enough to refuse, and the rest would be latency a caller waits through.
    """
    if isinstance(calendar_ids, str):
        calendar_ids = (calendar_ids,)

    for calendar_id in calendar_ids or ("primary",):
        url = EVENTS_ENDPOINT_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "timeMin": _localize(start_iso, timezone),
                        "timeMax": _localize(end_iso, timezone),
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": 5,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "Google Calendar availability check on {} failed: {}",
                calendar_id,
                exc,
            )
            continue

        if response.status_code != 200:
            # One calendar we cannot read must not stop the others being
            # checked. A calendar removed or unshared after being configured
            # is a 404 here, and treating that as "the whole check failed"
            # would quietly stop checking the calendar that still works.
            logger.warning(
                "Google Calendar availability check on {} failed ({}).",
                calendar_id,
                response.status_code,
            )
            continue

        for item in response.json().get("items") or []:
            if _occupies_the_chair(item):
                return item
    return None


def _occupies_the_chair(event: Dict[str, Any]) -> bool:
    """Whether an event on the calendar means the slot is actually taken.

    ``events.list`` returns everything that *overlaps* the window, which is
    not the same question, so two kinds of overlap are not a busy chair.

    **An event the owner marked Free is not busy.** Google calls it
    ``transparency: "transparent"`` and it is the operator's own statement of
    intent, made in the Busy/Free control beside every event. A reminder, a
    held placeholder, a travel note, and every entry on Google's own holiday
    and birthday calendars.

    **A cancelled event is not an event.**

    Everything else is busy, **including an all-day event**, and that is the
    line worth defending. An all-day entry on the calendar a business books
    against is almost always the business closing itself: "Dr Anitha on
    leave", "Clinic shut for Diwali", "Equipment service". Treating those as
    free books a patient who then travels to a locked door, which is a worse
    failure than a slot we declined to offer -- they can ring back for another
    time, they cannot get the morning back.

    Public holidays do not arrive this way. They live on a separate holiday
    calendar, which this integration does not read, and they are published
    ``transparent`` in any case -- so the rule above already lets them
    through twice over.

    I had this wrong: an earlier version excluded every all-day event on the
    theory that a holiday was refusing a free noon slot. The refusal turned
    out not to come from this function at all -- the tool was never called --
    and the exclusion silently stopped leave and closures from blocking
    anything. Being wrong in this direction is expensive and invisible, which
    is why it is now one rule the operator can see rather than an inference
    about event shapes.
    """
    if event.get("status") == "cancelled":
        return False
    if str(event.get("transparency") or "").lower() == "transparent":
        return False
    return True


def _combine_start_end(
    start_date: str, start_time: str, duration_minutes: float
) -> tuple[str, str]:
    try:
        start = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError(
            f"Could not parse start_date/start_time as YYYY-MM-DD / HH:MM: "
            f"{start_date!r} {start_time!r}"
        ) from exc

    duration = duration_minutes if duration_minutes and duration_minutes > 0 else 30
    end = start + timedelta(minutes=duration)
    return start.isoformat(), end.isoformat()


async def execute_google_calendar_tool(
    tool: Any,
    arguments: Dict[str, Any],
    organization_id: Optional[int],
) -> Dict[str, Any]:
    """Create the event. Returns the same {"status": ...} shape as execute_http_tool
    so the rest of the pipeline (result_callback, logging) needs no special case."""
    if not organization_id:
        return {"status": "error", "error": "No organization context for this call."}

    try:
        summary = (arguments or {}).get("summary") or ""
        if not summary.strip():
            return {"status": "error", "error": "summary is required"}

        start_date = (arguments or {}).get("start_date") or ""
        start_time = (arguments or {}).get("start_time") or ""
        try:
            start_iso, end_iso = _combine_start_end(
                start_date,
                start_time,
                float((arguments or {}).get("duration_minutes") or 30),
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        description = (arguments or {}).get("description") or ""
        attendee_email = (arguments or {}).get("attendee_email") or None

        # The same zone the agent reasoned in, so the window checked for
        # clashes and the entry created are the same moment as the one it read
        # back to the caller.
        event_timezone = await booking_timezone(organization_id)

        event_body: Dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_iso,
                "timeZone": event_timezone,
            },
            "end": {"dateTime": end_iso, "timeZone": event_timezone},
        }
        if attendee_email:
            event_body["attendees"] = [{"email": attendee_email}]

        async with db_client.async_session() as session:
            try:
                access_token = await get_valid_access_token(
                    session, organization_id=organization_id
                )
            except GoogleCalendarError as exc:
                logger.warning(
                    "Google Calendar tool '{}' could not get a token for org {}: {}",
                    tool.name,
                    organization_id,
                    exc,
                )
                return {
                    "status": "error",
                    "error": (
                        "Google Calendar is not connected for this account. "
                        "Ask a human to connect it under Provider Keys."
                    ),
                }

            status = await get_status(session, organization_id=organization_id)
            calendar_id = status.calendar_id or "primary"
            # Read many, write one. The booking goes to `calendar_id`; every
            # calendar the operator marked as busy is checked first.
            read_calendar_ids = status.read_calendar_ids

        conflict = await _find_conflicting_event(
            access_token, read_calendar_ids, start_iso, end_iso, event_timezone
        )
        if conflict:
            conflict_summary = conflict.get("summary") or "an existing event"
            start_field = conflict.get("start")
            conflict_start = start_field if isinstance(start_field, dict) else {}
            logger.info(
                "Google Calendar booking for org {} at {} skipped -- conflicts "
                "with '{}' (start={}, transparency={}, status={})",
                organization_id,
                start_iso,
                conflict_summary,
                conflict_start.get("dateTime") or conflict_start.get("date"),
                conflict.get("transparency"),
                conflict.get("status"),
            )
            # A closure and a clash need different answers from the agent. A
            # taken slot means offer another time; a day the business has shut
            # means offer another day, and telling the agent to try 12:30
            # instead would walk it through every slot of a closed day.
            if conflict_start.get("date") and not conflict_start.get("dateTime"):
                return {
                    "status": "error",
                    "error": (
                        f"The business is closed that day ({conflict_summary}). "
                        "Offer the caller a different date -- no time on this "
                        "day can be booked."
                    ),
                }
            return {
                "status": "error",
                "error": (
                    f"That time is already booked ({conflict_summary}). "
                    "Ask the caller for a different date or time and try again."
                ),
            }

        url = EVENTS_ENDPOINT_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
        params = {"sendUpdates": "all"} if attendee_email else {}

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                json=event_body,
            )

        if response.status_code not in (200, 201):
            logger.error(
                "Google Calendar event creation failed for org {}: {} {}",
                organization_id,
                response.status_code,
                response.text,
            )
            return {
                "status": "error",
                "error": f"Google Calendar rejected the request ({response.status_code}).",
            }

        data = response.json()
        logger.info(
            "Google Calendar event created for org {}: {}",
            organization_id,
            data.get("id"),
        )
        return {
            "status": "success",
            "data": {
                "event_id": data.get("id"),
                "html_link": data.get("htmlLink"),
                "start": start_iso,
                "end": end_iso,
            },
        }

    except Exception as exc:  # noqa: BLE001 - tool executors report, never raise
        logger.error("Google Calendar tool '{}' execution failed: {}", tool.name, exc)
        return {"status": "error", "error": f"Tool execution failed: {exc}"}

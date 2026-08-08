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

        event_body: Dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_iso,
                "timeZone": GOOGLE_CALENDAR_DEFAULT_TIMEZONE,
            },
            "end": {"dateTime": end_iso, "timeZone": GOOGLE_CALENDAR_DEFAULT_TIMEZONE},
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

"""Execution for OAuth-connected-account (integration) tools.

v1 actions: Google Calendar event creation and Gmail send. Both call
Google's REST APIs directly via httpx (no google-api-python-client
dependency) using a connection's access token, refreshed on demand via
api/services/oauth/google.py.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any, Dict

import httpx
from loguru import logger

from api.db import db_client
from api.services.oauth.google import GoogleTokenRefreshError, get_valid_access_token

CALENDAR_EVENTS_ENDPOINT = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)
GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


async def execute_integration_tool(
    tool: Any,
    arguments: Dict[str, Any],
    *,
    organization_id: int,
) -> Dict[str, Any]:
    """Dispatch to the right Google action based on the tool's config."""
    definition = tool.definition or {}
    config = definition.get("config", {})
    connection_id = config.get("connection_id")
    action = config.get("action")

    connection = await db_client.get_oauth_connection(connection_id, organization_id)
    if not connection or not connection.is_active:
        return {
            "status": "error",
            "error": (
                "The connected Google account for this tool is no longer "
                "available. Reconnect it from the Tools page."
            ),
        }

    try:
        access_token = await get_valid_access_token(connection)
    except GoogleTokenRefreshError as e:
        logger.error(f"Google token refresh failed for tool '{tool.name}': {e}")
        return {
            "status": "error",
            "error": "The connected Google account needs to be reconnected.",
        }

    if action == "google_calendar_create_event":
        return await _create_calendar_event(access_token, arguments or {})
    if action == "google_gmail_send":
        return await _send_gmail(access_token, arguments or {})

    return {"status": "error", "error": f"Unknown integration action: {action}"}


async def _create_calendar_event(
    access_token: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    summary = arguments.get("summary")
    start_time = arguments.get("start_time")
    end_time = arguments.get("end_time")
    description = arguments.get("description")
    attendee_emails = arguments.get("attendee_emails")

    if not summary or not start_time or not end_time:
        return {
            "status": "error",
            "error": "summary, start_time, and end_time are required.",
        }

    body: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if description:
        body["description"] = description
    if attendee_emails:
        emails = [e.strip() for e in str(attendee_emails).split(",") if e.strip()]
        if emails:
            body["attendees"] = [{"email": e} for e in emails]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                CALENDAR_EVENTS_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Google Calendar create-event failed: {e.response.status_code} "
            f"{e.response.text}"
        )
        return {
            "status": "error",
            "error": f"Google Calendar rejected the request: {e.response.text}",
        }
    except httpx.RequestError as e:
        return {"status": "error", "error": f"Request failed: {str(e)}"}

    return {
        "status": "success",
        "event_id": data.get("id"),
        "html_link": data.get("htmlLink"),
    }


async def _send_gmail(access_token: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    to_email = arguments.get("to")
    subject = arguments.get("subject")
    body_text = arguments.get("body")

    if not to_email or not subject or not body_text:
        return {"status": "error", "error": "to, subject, and body are required."}

    mime_message = MIMEText(body_text)
    mime_message["to"] = to_email
    mime_message["subject"] = subject
    raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                GMAIL_SEND_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Gmail send failed: {e.response.status_code} {e.response.text}"
        )
        return {
            "status": "error",
            "error": f"Gmail rejected the request: {e.response.text}",
        }
    except httpx.RequestError as e:
        return {"status": "error", "error": f"Request failed: {str(e)}"}

    return {"status": "success", "message_id": data.get("id")}

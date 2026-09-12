"""HTTP surface for the Google Calendar connect flow.

Split into two authenticated JSON endpoints (``authorize-url``, ``status``,
``disconnect``) plus one unauthenticated browser redirect target
(``callback``) that only Google ever calls. The split matters:
``/authorize-url`` cannot itself be a redirect, because the browser's
top-level navigation to it would carry none of the app's bearer-token
headers — the frontend fetches the URL with an authenticated request first,
then navigates the browser to *that* (Google's) URL itself.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.constants import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    PUBLIC_BASE_URL,
)
from api.db import db_client
from api.db.models import GoogleCalendarConnectionModel, UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import get_user, require_organization_role
from api.services.integrations.google_calendar import oauth

router = APIRouter(prefix="/integrations/google-calendar", tags=["google-calendar"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _ui_base_url() -> str:
    return (PUBLIC_BASE_URL or "http://localhost:3010").rstrip("/")


@router.get("/authorize-url")
async def authorize_url(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """The URL the frontend should navigate the browser to next."""
    organization_id = _organization_id(user)
    try:
        url = oauth.build_authorize_url(
            organization_id=organization_id, user_id=user.id
        )
    except oauth.GoogleCalendarNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Google redirects here after the consent screen. No session, no bearer
    token — ``state`` (HMAC-signed by ``build_authorize_url``) is the only
    thing authenticating this request, the same role a CSRF token plays.
    """
    ui_base = _ui_base_url()

    if error:
        # The caller denied consent, or Google reported some other failure
        # before ever issuing a code. Not a bug — send them back with a
        # reason rather than a 400 they will never see.
        return RedirectResponse(
            f"{ui_base}/integrations/apps?google_calendar=error&reason={quote(error)}"
        )

    if not code or not state:
        return RedirectResponse(
            f"{ui_base}/integrations/apps?google_calendar=error&reason=missing_code"
        )

    async with db_client.async_session() as session:
        try:
            await oauth.handle_callback(session, code=code, state=state)
        except oauth.GoogleCalendarError as exc:
            await session.rollback()
            return RedirectResponse(
                f"{ui_base}/integrations/apps?google_calendar=error&reason={quote(str(exc))}"
            )
        await session.commit()

    return RedirectResponse(f"{ui_base}/integrations/apps?google_calendar=connected")


@router.get("/status")
async def status(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        result = await oauth.get_status(session, organization_id=organization_id)
    return {
        "connected": result.connected,
        "connected_email": result.connected_email,
        "calendar_id": result.calendar_id,
        "busy_calendar_ids": list(result.busy_calendar_ids),
        "updated_at": result.updated_at,
        "configured": bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET),
    }


class BusyCalendarsRequest(BaseModel):
    """The calendars that count as busy besides the booking calendar."""

    #: Ids, as Google writes them: an email address for a secondary calendar,
    #: or a long "…@group.calendar.google.com". Capped because this is a
    #: per-request read fan-out -- every id is one more call before an agent
    #: can answer a caller.
    calendar_ids: list[str] = Field(default_factory=list, max_length=10)


@router.put("/busy-calendars")
async def set_busy_calendars(
    request: BusyCalendarsRequest,
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    """Choose which calendars are read when deciding whether a slot is free.

    Read-only, always. Bookings keep going to the connected calendar alone --
    a second calendar that received bookings would need to know *which* of
    them, and that is a different feature.

    Deliberately not "read every calendar on the account". That is right for a
    solo practitioner whose own appointments sit on a personal calendar, and
    wrong for a two-doctor clinic, where merging both doctors reports the
    clinic full when only one is booked -- the same failure as refusing a free
    slot. Nothing in a calendar list says which case an account is, so the
    operator says, and an account that says nothing behaves as it did before.

    The ids are not verified against Google here. A calendar can be unshared
    later, so a check now would prove nothing durable, and the read path
    already skips a calendar it cannot fetch and logs which one. Refusing a
    valid id because a verification call timed out would be worse.
    """
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(GoogleCalendarConnectionModel).where(
                GoogleCalendarConnectionModel.organization_id == organization_id,
                GoogleCalendarConnectionModel.is_active.is_(True),
            )
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Google Calendar is not connected for this account.",
            )

        # Deduplicated, and the booking calendar dropped if it was listed: it
        # is always read, and carrying it here would have it fetched twice on
        # every check.
        target = row.calendar_id or "primary"
        seen: set[str] = set()
        cleaned: list[str] = []
        for candidate in request.calendar_ids:
            value = (candidate or "").strip()
            if not value or value == target or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)

        row.busy_calendar_ids = cleaned
        await session.commit()

    logger.info(
        "Busy calendars for org {} set to {} ({} id(s))",
        organization_id,
        cleaned,
        len(cleaned),
    )
    return {"busy_calendar_ids": cleaned}


@router.post("/disconnect")
async def disconnect(
    user: UserModel = Depends(require_organization_role(OrganizationRole.ADMIN)),
) -> dict[str, Any]:
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        await oauth.disconnect(session, organization_id=organization_id)
        await session.commit()
    return {"connected": False}

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

from api.constants import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    PUBLIC_BASE_URL,
)
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
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
async def authorize_url(user: UserModel = Depends(get_user)) -> dict[str, Any]:
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
            f"{ui_base}/provider-keys?google_calendar=error&reason={quote(error)}"
        )

    if not code or not state:
        return RedirectResponse(
            f"{ui_base}/provider-keys?google_calendar=error&reason=missing_code"
        )

    async with db_client.async_session() as session:
        try:
            await oauth.handle_callback(session, code=code, state=state)
        except oauth.GoogleCalendarError as exc:
            await session.rollback()
            return RedirectResponse(
                f"{ui_base}/provider-keys?google_calendar=error&reason={quote(str(exc))}"
            )
        await session.commit()

    return RedirectResponse(f"{ui_base}/provider-keys?google_calendar=connected")


@router.get("/status")
async def status(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        result = await oauth.get_status(session, organization_id=organization_id)
    return {
        "connected": result.connected,
        "connected_email": result.connected_email,
        "calendar_id": result.calendar_id,
        "updated_at": result.updated_at,
        "configured": bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET),
    }


@router.post("/disconnect")
async def disconnect(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    organization_id = _organization_id(user)
    async with db_client.async_session() as session:
        await oauth.disconnect(session, organization_id=organization_id)
        await session.commit()
    return {"connected": False}

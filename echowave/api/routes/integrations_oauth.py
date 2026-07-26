"""Routes for OAuth-connected third-party accounts used by integration tools.

/google/oauth/start is called (authenticated) by the frontend to get a
consent-screen URL. /google/oauth/callback is hit directly by the caller's
browser after Google redirects back — it has no Authorization header, so the
organization is recovered from the CSRF state token instead (see
api/services/oauth/google.py).
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from api.constants import BACKEND_API_ENDPOINT, UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.oauth import google as google_oauth
from api.utils.token_encryption import encrypt_token

router = APIRouter(prefix="/integrations")

GOOGLE_CALLBACK_PATH = "/api/v1/integrations/google/oauth/callback"


def _google_redirect_uri() -> str:
    return f"{BACKEND_API_ENDPOINT.rstrip('/')}{GOOGLE_CALLBACK_PATH}"


class OAuthAuthorizationUrlResponse(BaseModel):
    authorization_url: str


class OAuthConnectionResponse(BaseModel):
    id: int
    provider: str
    external_account_email: str
    scopes: List[str]
    created_at: datetime


@router.get("/google/oauth/start", response_model=OAuthAuthorizationUrlResponse)
async def start_google_oauth(user: UserModel = Depends(get_user)):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        url = await google_oauth.create_authorization_url(
            organization_id=user.selected_organization_id,
            redirect_uri=_google_redirect_uri(),
        )
    except google_oauth.GoogleOAuthNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return OAuthAuthorizationUrlResponse(authorization_url=url)


@router.get("/google/oauth/callback")
async def google_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    tools_url = f"{UI_APP_URL.rstrip('/')}/tools"

    if error:
        logger.warning("Google OAuth callback returned error: {}", error)
        return RedirectResponse(f"{tools_url}?google_connect=error")

    if not code or not state:
        return RedirectResponse(f"{tools_url}?google_connect=error")

    organization_id = await google_oauth.consume_state(state)
    if organization_id is None:
        logger.warning("Google OAuth callback: unknown or expired state token")
        return RedirectResponse(f"{tools_url}?google_connect=expired")

    try:
        token_response = await google_oauth.exchange_code_for_tokens(
            code=code, redirect_uri=_google_redirect_uri()
        )
        access_token = token_response["access_token"]
        refresh_token = token_response.get("refresh_token")
        userinfo = await google_oauth.fetch_userinfo(access_token=access_token)
        email = userinfo.get("email")
        if not email:
            raise ValueError("Google userinfo response had no email")

        await db_client.create_oauth_connection(
            organization_id=organization_id,
            provider="google",
            external_account_email=email,
            encrypted_access_token=encrypt_token(access_token),
            encrypted_refresh_token=(
                encrypt_token(refresh_token) if refresh_token else None
            ),
            token_expires_at=google_oauth.token_expiry_from_response(token_response),
            scopes=google_oauth.GOOGLE_OAUTH_SCOPES,
        )
    except Exception as e:
        logger.error(
            "Google OAuth callback failed for org {}: {}", organization_id, e
        )
        return RedirectResponse(f"{tools_url}?google_connect=error")

    return RedirectResponse(f"{tools_url}?google_connect=success")


@router.get("/google/connections", response_model=List[OAuthConnectionResponse])
async def list_google_connections(user: UserModel = Depends(get_user)):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    connections = await db_client.list_oauth_connections(
        user.selected_organization_id, provider="google"
    )
    return [
        OAuthConnectionResponse(
            id=c.id,
            provider=c.provider,
            external_account_email=c.external_account_email,
            scopes=c.scopes or [],
            created_at=c.created_at,
        )
        for c in connections
    ]


@router.delete("/google/connections/{connection_id}")
async def disconnect_google(
    connection_id: int, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    deactivated = await db_client.deactivate_oauth_connection(
        connection_id, user.selected_organization_id
    )
    if not deactivated:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disconnected"}

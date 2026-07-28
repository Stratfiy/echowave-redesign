"""Google OAuth2 authorization-code flow.

Pure HTTP against Google's own endpoints via httpx — no
google-api-python-client / google-auth dependency needed. Handles the
authorization URL, code exchange, refresh, userinfo lookup, and CSRF state.

State is tracked server-side in Redis (opaque token -> organization_id,
short TTL, single-use on consume) rather than embedding organization_id in
the client-visible `state` query param.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, REDIS_URL

if TYPE_CHECKING:
    from api.db.models import OAuthConnectionModel

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

# Scopes for the integration tools currently implemented (Calendar event
# creation, Gmail send), plus identity so we know which account connected.
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]

_STATE_KEY_PREFIX = "google_oauth_state:"
_STATE_TTL_SECONDS = 600  # 10 minutes to complete the consent screen

_redis_client: Optional[aioredis.Redis] = None


class GoogleOAuthNotConfigured(RuntimeError):
    """GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are unset."""


class GoogleTokenRefreshError(RuntimeError):
    """A connection's access token is expired and could not be refreshed."""


def _require_configured() -> None:
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        raise GoogleOAuthNotConfigured(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not configured."
        )


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def create_authorization_url(*, organization_id: int, redirect_uri: str) -> str:
    """Build a Google consent-screen URL and record CSRF state bound to this org."""
    _require_configured()
    state = secrets.token_urlsafe(32)
    redis_client = await _get_redis()
    await redis_client.set(
        f"{_STATE_KEY_PREFIX}{state}", str(organization_id), ex=_STATE_TTL_SECONDS
    )

    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    query = httpx.QueryParams(params)
    return f"{AUTHORIZATION_ENDPOINT}?{query}"


async def consume_state(state: str) -> Optional[int]:
    """Validate + single-use-consume a state token; returns the bound organization_id."""
    redis_client = await _get_redis()
    key = f"{_STATE_KEY_PREFIX}{state}"
    org_id = await redis_client.get(key)
    if org_id is None:
        return None
    await redis_client.delete(key)
    return int(org_id)


async def exchange_code_for_tokens(*, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for access/refresh tokens."""
    _require_configured()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        response.raise_for_status()
        return response.json()


async def refresh_access_token(*, refresh_token: str) -> dict:
    """Use a refresh token to mint a new access token."""
    _require_configured()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return response.json()


async def fetch_userinfo(*, access_token: str) -> dict:
    """Fetch the connected account's profile (used to get its email)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


def token_expiry_from_response(token_response: dict) -> Optional[datetime]:
    expires_in = token_response.get("expires_in")
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def get_valid_access_token(connection: "OAuthConnectionModel") -> str:
    """Return a usable access token for this connection, refreshing if needed.

    Called from the live call path (integration tool handlers) — refreshes
    and persists a new access token when the stored one is expired or about
    to expire (60s safety margin).
    """
    from api.db import db_client
    from api.utils.token_encryption import decrypt_token, encrypt_token

    expires_at = connection.token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if expires_at is None or now < (expires_at - timedelta(seconds=60)):
        return decrypt_token(connection.encrypted_access_token)

    if not connection.encrypted_refresh_token:
        raise GoogleTokenRefreshError(
            f"OAuth connection {connection.id} has no refresh token and its "
            "access token has expired; the account must be reconnected."
        )

    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        token_response = await refresh_access_token(refresh_token=refresh_token)
    except Exception as e:
        logger.error(
            "Failed to refresh Google access token for connection {}: {}",
            connection.id,
            e,
        )
        raise GoogleTokenRefreshError(
            f"Could not refresh the access token for connection {connection.id}."
        ) from e

    new_access_token = token_response["access_token"]
    await db_client.update_oauth_connection_tokens(
        connection.id,
        encrypted_access_token=encrypt_token(new_access_token),
        token_expires_at=token_expiry_from_response(token_response),
    )
    return new_access_token

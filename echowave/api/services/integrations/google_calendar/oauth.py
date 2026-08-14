"""Google Calendar OAuth: the "Connect Google Calendar" flow and token upkeep.

One organization, one connected calendar (see ``GoogleCalendarConnectionModel``).
The refresh token is the durable credential; the access token is a cache of it
that this module refreshes transparently so every other caller can just ask
for "a valid access token" and never think about expiry.

**Why ``state`` is signed rather than opaque.** Google's redirect back to
``/callback`` is not an authenticated request — there is no bearer token or
session cookie on it, only whatever we put in the ``state`` query param when
we sent the user to Google. That param is the only thing telling the callback
which organization this grant belongs to, so it has to be tamper-evident: an
unsigned org id in a query string would let anyone attach their own
authorization code to a different organization's calendar by editing the URL.
HMAC-signing it with the same secret that protects provider credentials closes
that without inventing a new secret to manage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import InvalidToken
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    BACKEND_API_ENDPOINT,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    PLATFORM_CREDENTIAL_SECRET,
)
from api.db.models import GoogleCalendarConnectionModel

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

# calendar.events: create/read/update/delete events on calendars the connected
# account can see. Deliberately narrower than the full `calendar` scope, which
# also grants managing calendar *settings* (sharing, deleting calendars,
# adding new ones) that a booking tool has no use for and should not be able
# to ask a customer for.
SCOPE = "https://www.googleapis.com/auth/calendar.events"

# Refresh this long before actual expiry so a slow request never straddles the
# boundary and gets a 401 mid-call.
_REFRESH_SKEW = timedelta(seconds=90)
_STATE_MAX_AGE_SECONDS = 600


class GoogleCalendarError(Exception):
    """Base for anything this module raises."""


class GoogleCalendarNotConfiguredError(GoogleCalendarError):
    """GOOGLE_OAUTH_CLIENT_ID/SECRET are not set on this deployment."""


class GoogleCalendarNotConnectedError(GoogleCalendarError):
    """This organization has never completed the connect flow (or disconnected)."""


class GoogleCalendarTokenError(GoogleCalendarError):
    """A stored token could not be decrypted or Google refused a refresh.

    Both mean the same thing to the caller: the connection is no longer
    usable and the organization needs to reconnect. Kept distinct from
    GoogleCalendarNotConnectedError only so the two log messages stay honest
    about which situation happened — never connected vs. was connected and
    the grant went bad (rotated PLATFORM_CREDENTIAL_SECRET, or the user
    revoked access from their Google Account settings).
    """


def _require_client_credentials() -> tuple[str, str]:
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        raise GoogleCalendarNotConfiguredError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not set. "
            "An administrator needs to register an OAuth client in Google "
            "Cloud Console and configure both before Google Calendar can be "
            "connected."
        )
    return GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET


def _redirect_uri() -> str:
    return f"{BACKEND_API_ENDPOINT}/api/v1/integrations/google-calendar/callback"


def _cipher():
    """The shared cipher, so a key rotation covers stored grants too.

    Left out, rotating would silently disconnect every calendar and the only
    symptom would be syncs quietly stopping.
    """
    from api.services import secret_rotation

    try:
        return secret_rotation.cipher()
    except secret_rotation.SecretError as exc:
        raise GoogleCalendarError(str(exc)) from exc


def _sign_state(*, organization_id: int, user_id: int) -> str:
    payload = {
        "organization_id": organization_id,
        "user_id": user_id,
        "nonce": secrets.token_urlsafe(12),
        "ts": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    body_b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
    secret = (PLATFORM_CREDENTIAL_SECRET or "").encode()
    sig = hmac.new(secret, body_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{body_b64}.{sig_b64}"


def _verify_state(state: str) -> dict[str, Any]:
    if not PLATFORM_CREDENTIAL_SECRET:
        raise GoogleCalendarError("PLATFORM_CREDENTIAL_SECRET is not set.")
    try:
        body_b64, sig_b64 = state.split(".", 1)
    except ValueError as exc:
        raise GoogleCalendarError("Malformed state parameter.") from exc

    secret = PLATFORM_CREDENTIAL_SECRET.encode()
    expected_sig = hmac.new(secret, body_b64.encode(), hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")
    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise GoogleCalendarError(
            "state signature does not match — possible tampering."
        )

    padded = body_b64 + "=" * (-len(body_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))

    age = time.time() - payload.get("ts", 0)
    if age > _STATE_MAX_AGE_SECONDS or age < -30:
        raise GoogleCalendarError(
            "This connect link has expired. Start over from Provider Keys."
        )
    return payload


def build_authorize_url(*, organization_id: int, user_id: int) -> str:
    """The URL to send the browser to. Raises if the OAuth app isn't configured."""
    client_id, _ = _require_client_credentials()
    state = _sign_state(organization_id=organization_id, user_id=user_id)
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Google only issues a refresh token on the *first* consent unless the
        # caller forces the consent screen every time. Reconnecting after a
        # revoke (the only path back to a working connection) would otherwise
        # silently hand back an access token with no refresh token behind it.
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"


@dataclass(frozen=True)
class OAuthCallbackResult:
    organization_id: int
    user_id: int


async def _exchange_code(code: str) -> dict[str, Any]:
    client_id, client_secret = _require_client_credentials()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        logger.error(
            "Google Calendar token exchange failed: {} {}",
            response.status_code,
            response.text,
        )
        raise GoogleCalendarError("Google rejected the authorization code.")
    return response.json()


async def _fetch_connected_email(access_token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code == 200:
            return response.json().get("email")
    except httpx.RequestError as exc:
        logger.warning("Could not fetch Google account email: {}", exc)
    return None


async def handle_callback(
    session: AsyncSession, *, code: str, state: str
) -> OAuthCallbackResult:
    """Exchange the code, store the connection, return who it belongs to."""
    payload = _verify_state(state)
    organization_id = int(payload["organization_id"])
    user_id = int(payload["user_id"])

    tokens = await _exchange_code(code)
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    expires_in = tokens.get("expires_in", 3600)

    if not access_token:
        raise GoogleCalendarError("Google did not return an access token.")
    if not refresh_token:
        # Happens if the account already granted consent and Google decided
        # `prompt=consent` didn't need re-issuing one — rare, since we always
        # pass prompt=consent, but Google's behavior here isn't a hard
        # guarantee. Without a refresh token this connection is only good
        # for the current access token's lifetime, which would silently stop
        # working an hour from now, so treat it as a failure rather than a
        # half-working connection.
        raise GoogleCalendarError(
            "Google did not return a refresh token. Disconnect any prior "
            "grant for this app in your Google Account's third-party access "
            "settings and try connecting again."
        )

    connected_email = await _fetch_connected_email(access_token)

    cipher = _cipher()
    now = datetime.now(UTC)

    existing = await session.scalar(
        select(GoogleCalendarConnectionModel).where(
            GoogleCalendarConnectionModel.organization_id == organization_id
        )
    )
    if existing is None:
        existing = GoogleCalendarConnectionModel(organization_id=organization_id)
        session.add(existing)

    existing.encrypted_refresh_token = cipher.encrypt(refresh_token.encode()).decode()
    existing.encrypted_access_token = cipher.encrypt(access_token.encode()).decode()
    existing.access_token_expires_at = now + timedelta(seconds=expires_in)
    existing.connected_email = connected_email
    existing.calendar_id = "primary"
    existing.is_active = True
    existing.connected_by = user_id

    await session.flush()
    logger.info(
        "Google Calendar connected for organization {} by user {} ({})",
        organization_id,
        user_id,
        connected_email or "email unknown",
    )
    return OAuthCallbackResult(organization_id=organization_id, user_id=user_id)


@dataclass(frozen=True)
class ConnectionStatus:
    connected: bool
    connected_email: str | None
    calendar_id: str | None
    updated_at: str | None


async def get_status(
    session: AsyncSession, *, organization_id: int
) -> ConnectionStatus:
    row = await session.scalar(
        select(GoogleCalendarConnectionModel).where(
            GoogleCalendarConnectionModel.organization_id == organization_id,
            GoogleCalendarConnectionModel.is_active.is_(True),
        )
    )
    if row is None:
        return ConnectionStatus(
            connected=False, connected_email=None, calendar_id=None, updated_at=None
        )
    return ConnectionStatus(
        connected=True,
        connected_email=row.connected_email,
        calendar_id=row.calendar_id,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


async def disconnect(session: AsyncSession, *, organization_id: int) -> None:
    row = await session.scalar(
        select(GoogleCalendarConnectionModel).where(
            GoogleCalendarConnectionModel.organization_id == organization_id
        )
    )
    if row is None:
        return

    # Best-effort revoke at Google. Not fatal if it fails — the row is being
    # deleted either way, and a network hiccup here should not block the
    # customer from disconnecting on our side. If Google doesn't hear about
    # it, the grant just goes stale in their "third-party access" list.
    try:
        cipher = _cipher()
        refresh_token = cipher.decrypt(row.encrypted_refresh_token.encode()).decode()
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(REVOKE_ENDPOINT, data={"token": refresh_token})
    except Exception as exc:  # noqa: BLE001 - best-effort, never block disconnect
        logger.warning("Could not revoke Google Calendar token at Google: {}", exc)

    await session.delete(row)
    await session.flush()
    logger.info("Google Calendar disconnected for organization {}", organization_id)


async def get_valid_access_token(session: AsyncSession, *, organization_id: int) -> str:
    """A usable access token, refreshing first if the cached one is stale.

    The only function other modules should call to actually talk to the
    Calendar API — everything above this is the connect/disconnect UI's
    concern, not the tool executor's.
    """
    row = await session.scalar(
        select(GoogleCalendarConnectionModel).where(
            GoogleCalendarConnectionModel.organization_id == organization_id,
            GoogleCalendarConnectionModel.is_active.is_(True),
        )
    )
    if row is None:
        raise GoogleCalendarNotConnectedError(
            "No Google Calendar is connected for this organization."
        )

    cipher = _cipher()

    now = datetime.now(UTC)
    if (
        row.encrypted_access_token
        and row.access_token_expires_at
        and row.access_token_expires_at - _REFRESH_SKEW > now
    ):
        try:
            return cipher.decrypt(row.encrypted_access_token.encode()).decode()
        except InvalidToken as exc:
            # PLATFORM_CREDENTIAL_SECRET rotated since this was cached. Fall
            # through to a refresh — the refresh token was encrypted at the
            # same time and will fail the same way, which is the right
            # outcome: rotating the secret invalidates every stored token,
            # by design, the same as it does for provider keys.
            logger.warning(
                "Cached Google Calendar access token for org {} could not be "
                "decrypted, attempting refresh: {}",
                organization_id,
                exc,
            )

    try:
        refresh_token = cipher.decrypt(row.encrypted_refresh_token.encode()).decode()
    except InvalidToken as exc:
        raise GoogleCalendarTokenError(
            "Stored Google Calendar credentials cannot be decrypted — "
            "PLATFORM_CREDENTIAL_SECRET has changed since this was connected. "
            "Reconnect Google Calendar."
        ) from exc

    client_id, client_secret = _require_client_credentials()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
        )

    if response.status_code != 200:
        logger.error(
            "Google Calendar token refresh failed for org {}: {} {}",
            organization_id,
            response.status_code,
            response.text,
        )
        raise GoogleCalendarTokenError(
            "Google refused to refresh the Calendar connection. It may have "
            "been revoked — reconnect Google Calendar."
        )

    tokens = response.json()
    access_token = tokens["access_token"]
    expires_in = tokens.get("expires_in", 3600)

    row.encrypted_access_token = cipher.encrypt(access_token.encode()).decode()
    row.access_token_expires_at = now + timedelta(seconds=expires_in)
    await session.flush()

    return access_token

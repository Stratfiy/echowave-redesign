"""Access tokens that outlive the hour a vendor gives them.

Every credential type before this one was static: paste a key, send it forever.
That covers an internal API and almost nothing a business already runs on.
Zoho, HubSpot, Salesforce and Google all issue an access token that expires in
roughly an hour and expect the client to exchange a long-lived refresh token
for a new one. Pasting an access token into a static credential works until
lunchtime and then every call using it fails — which is worse than not
supporting it, because it fails in the middle of a customer conversation
rather than at setup.

**The refresh token is the credential; the access token is a cache.** That is
the whole design. ``credential_data`` holds the grant (token URL, client id and
secret, refresh token) and, alongside it, whatever access token was last minted
and when it dies. Any worker can mint; the result is written back so the others
reuse it rather than each burning a refresh call.

**Why not a background refresher.** A cron that tops up every token every fifty
minutes does work nobody asked for on credentials nobody is using, and it still
races a call that arrives at minute fifty-one. Minting on demand, once, behind
a lock, is fewer moving parts and has no window where a token is expired but
the sweep has not run yet.

**Header shape is per-vendor and not negotiable.** Most send
``Authorization: Bearer <token>``. Zoho sends ``Authorization:
Zoho-oauthtoken <token>`` and rejects Bearer outright. That is one field
(``header_prefix``), and getting it wrong is a 401 that looks exactly like a
bad secret.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.db.models import ExternalCredentialModel

CREDENTIAL_TYPE = "oauth2"

#: Refresh this long before the vendor's stated expiry. A token that is valid
#: when we check and expired when the request lands is the failure this avoids;
#: a minute covers the round trip plus a slow provider without being so wide
#: that we refresh constantly.
EXPIRY_SKEW_SECONDS = 60

#: What to assume when the token response omits ``expires_in``. Deliberately
#: short: guessing long means using a dead token, guessing short costs one
#: extra refresh.
DEFAULT_EXPIRES_IN_SECONDS = 3600

#: The grant fields. Everything else in ``credential_data`` is cache or shape.
REQUIRED_FIELDS = ("token_url", "client_id", "client_secret", "refresh_token")

#: A token exchange should be fast; a slow one is holding up a live call.
TOKEN_REQUEST_TIMEOUT_SECONDS = 10

#: Serialises refreshes per credential *within one process*. Two workers can
#: still refresh at the same moment — that is safe, because a vendor issues a
#: fresh token per exchange and the last write wins with a valid token. What
#: this prevents is the pathological case: twenty concurrent tool calls on one
#: agent each opening their own exchange.
_locks: dict[str, asyncio.Lock] = {}


class OAuth2Error(Exception):
    """A token could not be obtained. Carries nothing secret."""


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def missing_fields(data: dict[str, Any]) -> list[str]:
    """Which grant fields are absent. Used by credential validation."""
    return [f for f in REQUIRED_FIELDS if not str(data.get(f) or "").strip()]


def header_for(data: dict[str, Any], access_token: str) -> dict[str, str]:
    """The header this vendor expects, given a token.

    ``header_prefix`` is what makes this work for Zoho without a Zoho-specific
    code path: the scheme is data, not a branch.
    """
    name = str(data.get("header_name") or "Authorization").strip() or "Authorization"
    prefix = data.get("header_prefix")
    prefix = "Bearer" if prefix is None else str(prefix).strip()
    value = f"{prefix} {access_token}".strip() if prefix else access_token
    return {name: value}


def _expiry_of(data: dict[str, Any]) -> datetime | None:
    raw = data.get("expires_at")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def cached_token(data: dict[str, Any], *, now: datetime | None = None) -> str | None:
    """The stored access token if it is still good, else None.

    ``None`` covers three different situations on purpose — never minted,
    expired, and unparseable expiry — because the caller does the same thing in
    all three: mint a new one.
    """
    token = str(data.get("access_token") or "").strip()
    if not token:
        return None
    expires_at = _expiry_of(data)
    if expires_at is None:
        return None
    moment = now or datetime.now(UTC)
    if expires_at - timedelta(seconds=EXPIRY_SKEW_SECONDS) <= moment:
        return None
    return token


async def exchange_refresh_token(
    data: dict[str, Any], *, session: aiohttp.ClientSession | None = None
) -> tuple[str, datetime]:
    """Trade the refresh token for an access token. Returns (token, expiry).

    Raises ``OAuth2Error`` with a message safe to log and to surface in a tool
    result — the vendor's error code, never the request body, which contains
    the client secret and the refresh token.
    """
    absent = missing_fields(data)
    if absent:
        raise OAuth2Error(
            f"OAuth credential is incomplete: missing {', '.join(absent)}"
        )

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": data["refresh_token"],
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
    }
    scope = str(data.get("scope") or "").strip()
    if scope:
        payload["scope"] = scope

    timeout = aiohttp.ClientTimeout(total=TOKEN_REQUEST_TIMEOUT_SECONDS)
    owned = session is None
    http = session or aiohttp.ClientSession(timeout=timeout)
    try:
        async with http.post(
            str(data["token_url"]),
            data=payload,
            headers={"Accept": "application/json"},
            timeout=timeout,
        ) as response:
            if response.status >= 400:
                # The status and the vendor's error slug are what an operator
                # needs. The body can echo the secret back, so it does not
                # travel any further than this branch's own parsing.
                raise OAuth2Error(
                    f"Token endpoint returned HTTP {response.status}. "
                    "Check the client id, secret and refresh token."
                )
            try:
                body = await response.json(content_type=None)
            except Exception as exc:  # noqa: BLE001 - vendor returned non-JSON
                raise OAuth2Error("Token endpoint did not return JSON.") from exc
    except aiohttp.ClientError as exc:
        raise OAuth2Error(
            f"Could not reach the token endpoint: {type(exc).__name__}"
        ) from exc
    except asyncio.TimeoutError as exc:
        raise OAuth2Error("Token endpoint timed out.") from exc
    finally:
        if owned:
            await http.close()

    if not isinstance(body, dict):
        raise OAuth2Error("Token endpoint returned an unexpected payload.")

    # Some vendors answer 200 with an error field rather than a 4xx.
    if body.get("error"):
        raise OAuth2Error(f"Token endpoint refused the grant: {body['error']}")

    access_token = str(body.get("access_token") or "").strip()
    if not access_token:
        raise OAuth2Error("Token endpoint returned no access_token.")

    try:
        expires_in = int(body.get("expires_in") or DEFAULT_EXPIRES_IN_SECONDS)
    except (TypeError, ValueError):
        expires_in = DEFAULT_EXPIRES_IN_SECONDS
    expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in, 1))

    return access_token, expires_at


async def resolve_header(
    credential: "ExternalCredentialModel",
    *,
    persist=None,
    session: aiohttp.ClientSession | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """The Authorization header for this credential, minting if needed.

    ``persist`` is an optional ``async (credential_uuid, data) -> None`` that
    writes the refreshed cache back. Without it the token still works for this
    request; it is simply re-minted next time, which is correct but wasteful.
    """
    data = dict(credential.credential_data or {})

    token = cached_token(data, now=now)
    if token:
        return header_for(data, token)

    key = str(getattr(credential, "credential_uuid", "") or id(credential))
    async with _lock_for(key):
        # Another caller may have refreshed while this one waited for the lock.
        token = cached_token(dict(credential.credential_data or {}), now=now)
        if token:
            return header_for(data, token)

        access_token, expires_at = await exchange_refresh_token(data, session=session)
        data["access_token"] = access_token
        data["expires_at"] = expires_at.isoformat()
        # Mutating the loaded row means callers holding it see the fresh token
        # even when there is no persist hook.
        credential.credential_data = data

        if persist is not None:
            try:
                await persist(credential.credential_uuid, data)
            except Exception:  # noqa: BLE001 - a cache write must not fail a call
                logger.warning(
                    "Refreshed OAuth token for credential {} could not be saved; "
                    "it will be minted again next time",
                    getattr(credential, "credential_uuid", "?"),
                )

        logger.info(
            "Minted an OAuth access token for credential {} (expires {})",
            getattr(credential, "credential_uuid", "?"),
            expires_at.isoformat(),
        )
        return header_for(data, access_token)

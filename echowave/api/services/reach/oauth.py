"""Signing a person in to an outside server (MCP authorization).

The server says who issues its tokens (RFC 9728 protected-resource metadata,
named in its 401); that issuer says where to send the person and where to
exchange the code (RFC 8414). The client id is the one configured for an
ordering app (Zomato, Swiggy) or, for any other server that allows it, one
registered on the spot (RFC 7591). PKCE (S256) always; the ``state`` is
random, only its hash is stored, and it expires.

Every address learned along the way passes :func:`safety.check_address`
before it is fetched, because each one came from the outside server.

The sign-in itself happens on the server's own screen, opened in a new tab
from the chip in the thread; the person is never sent to another Decibyl
screen, and the callback page only says "connected, you can close this".
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from api.constants import BACKEND_API_ENDPOINT
from api.services.reach import safety

#: How long a sign-in may stay open before its state stops working.
STATE_TTL = timedelta(minutes=15)
EXPIRY_SKEW = timedelta(seconds=60)
TIMEOUT_SECS = 10.0


class SignInUnavailable(Exception):
    """The server cannot be signed in to from here, with the reason."""


def redirect_uri() -> str:
    return f"{BACKEND_API_ENDPOINT}/api/v1/reach/oauth/callback"


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    try:
        await safety.check_address(url)
        response = await client.get(url, headers={"Accept": "application/json"})
    except (safety.UnsafeAddress, httpx.HTTPError):
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def discover(server_url: str, resource_metadata: str | None) -> dict[str, Any]:
    """The issuer's endpoints for this server."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
        issuer = None
        for candidate in filter(
            None,
            (
                resource_metadata,
                f"{_origin(server_url)}/.well-known/oauth-protected-resource"
                + urlparse(server_url).path.rstrip("/"),
                f"{_origin(server_url)}/.well-known/oauth-protected-resource",
            ),
        ):
            meta = await _get_json(client, candidate)
            servers = (meta or {}).get("authorization_servers") or []
            if servers:
                issuer = str(servers[0]).rstrip("/")
                break
        issuer = issuer or _origin(server_url)
        for candidate in (
            f"{issuer}/.well-known/oauth-authorization-server",
            f"{issuer}/.well-known/openid-configuration",
        ):
            meta = await _get_json(client, candidate)
            if (
                meta
                and meta.get("authorization_endpoint")
                and meta.get("token_endpoint")
            ):
                return meta
    raise SignInUnavailable(
        "This server asks for a sign-in but does not say where. Ask its "
        "owner for a token and paste it instead."
    )


async def register(meta: dict[str, Any]) -> dict[str, Any]:
    endpoint = meta.get("registration_endpoint")
    if not endpoint:
        raise SignInUnavailable(
            "This server only signs in apps it already knows, and Decibyl is "
            "not one of them yet. Paste a token from it instead."
        )
    async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
        try:
            await safety.check_address(endpoint)
            response = await client.post(
                endpoint,
                json={
                    "client_name": "Decibyl",
                    "redirect_uris": [redirect_uri()],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        except (safety.UnsafeAddress, httpx.HTTPError) as exc:
            raise SignInUnavailable("The server would not register Decibyl.") from exc
    if response.status_code not in (200, 201):
        raise SignInUnavailable("The server would not register Decibyl.")
    body = response.json()
    if not body.get("client_id"):
        raise SignInUnavailable("The server would not register Decibyl.")
    return {"client_id": body["client_id"], "client_secret": body.get("client_secret")}


async def begin(
    *, server_url: str, resource_metadata: str | None, client_id: str | None
) -> tuple[str, str, dict[str, Any]]:
    """Returns ``(authorize_url, state, secret)``: the secret is sealed on
    the pending connection and holds what the callback needs."""
    meta = await discover(server_url, resource_metadata)
    await safety.check_address(meta["authorization_endpoint"])
    await safety.check_address(meta["token_endpoint"])
    client = (
        {"client_id": client_id, "client_secret": None}
        if client_id
        else await register(meta)
    )
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server_url,
    }
    separator = "&" if "?" in meta["authorization_endpoint"] else "?"
    url = f"{meta['authorization_endpoint']}{separator}{urlencode(params)}"
    secret = {
        "oauth": {
            "token_endpoint": meta["token_endpoint"],
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret"),
            "verifier": verifier,
            "started_at": datetime.now(UTC).isoformat(),
            "resource": server_url,
        }
    }
    return url, state, secret


def _tokens(body: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    expires_in = body.get("expires_in")
    expires_at = (
        (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat()
        if isinstance(expires_in, (int, float)) or str(expires_in or "").isdigit()
        else None
    )
    return {
        **previous,
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or previous.get("refresh_token"),
        "expires_at": expires_at,
    }


async def _token_request(oauth: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    form = {**form, "client_id": oauth["client_id"]}
    if oauth.get("client_secret"):
        form["client_secret"] = oauth["client_secret"]
    if oauth.get("resource"):
        form["resource"] = oauth["resource"]
    await safety.check_address(oauth["token_endpoint"])
    async with httpx.AsyncClient(timeout=TIMEOUT_SECS) as client:
        response = await client.post(
            oauth["token_endpoint"], data=form, headers={"Accept": "application/json"}
        )
    if response.status_code != 200:
        raise SignInUnavailable("The sign-in was not accepted. Try again.")
    body = response.json()
    if not body.get("access_token"):
        raise SignInUnavailable("The sign-in was not accepted. Try again.")
    return body


def state_expired(secret: dict[str, Any]) -> bool:
    started = (secret.get("oauth") or {}).get("started_at")
    try:
        return datetime.now(UTC) - datetime.fromisoformat(started) > STATE_TTL
    except (TypeError, ValueError):
        return True


async def finish(secret: dict[str, Any], code: str) -> dict[str, Any]:
    """Exchange the code. Returns the secret to seal in place of the old."""
    oauth = dict(secret.get("oauth") or {})
    body = await _token_request(
        oauth,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "code_verifier": oauth.get("verifier", ""),
        },
    )
    oauth.pop("verifier", None)
    oauth.pop("started_at", None)
    return {"oauth": _tokens(body, oauth)}


def needs_refresh(secret: dict[str, Any]) -> bool:
    oauth = secret.get("oauth") or {}
    expires = oauth.get("expires_at")
    if not expires or not oauth.get("refresh_token"):
        return False
    try:
        return datetime.fromisoformat(expires) - EXPIRY_SKEW <= datetime.now(UTC)
    except ValueError:
        return True


async def refresh(secret: dict[str, Any]) -> dict[str, Any]:
    oauth = dict(secret.get("oauth") or {})
    body = await _token_request(
        oauth, {"grant_type": "refresh_token", "refresh_token": oauth["refresh_token"]}
    )
    return {"oauth": _tokens(body, oauth)}


__all__ = [
    "SignInUnavailable",
    "begin",
    "discover",
    "finish",
    "needs_refresh",
    "redirect_uri",
    "refresh",
    "state_expired",
    "state_hash",
]

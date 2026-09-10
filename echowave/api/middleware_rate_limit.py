"""The ASGI middleware that applies the request gate to every HTTP call.

Route classification lives here rather than on each endpoint so a new route
is covered the day it is added, not the day someone remembers to decorate it.
Three classes, cheapest allowance last:

* ``auth`` — login, signup, OTP, password, MFA. The brute-force surface, so
  the tightest bucket, always keyed by IP because an attacker guessing a
  password has no key yet.
* ``embed`` — the public, unauthenticated embed/talk endpoints. The cost-DoS
  surface (each session spins up a model), keyed by IP.
* ``default`` — everything else, keyed by API key when present so one tenant
  cannot spend another's allowance.

Health and OPTIONS preflight are never counted: monitoring and CORS probes
are not abuse, and rate-limiting a liveness check turns a Redis blip into a
false outage alarm.
"""

from __future__ import annotations

import json

from loguru import logger
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.constants import (
    RATE_LIMIT_AUTH_PER_MINUTE,
    RATE_LIMIT_DEFAULT_PER_MINUTE,
    RATE_LIMIT_EMBED_PER_MINUTE,
    RATE_LIMIT_ENABLED,
)
from api.services.rate_limit import rate_limiter

_WINDOW_SECS = 60

# Substrings, matched against the request path. Ordered: the first hit wins,
# so the tight buckets are listed before the broad one.
_AUTH_MARKERS = (
    "/auth/",
    "/login",
    "/signup",
    "/register",
    "/otp",
    "/verify-email",
    "/password",
    "/mfa",
    "/two-factor",
)
_EMBED_MARKERS = ("/public/", "/embed/", "/talk/")

# Paths never counted, matched as substrings.
_EXEMPT_MARKERS = ("/health", "/openapi.json", "/docs", "/redoc", "/mcp")


def _classify(path: str) -> tuple[str, int] | None:
    """(bucket, per-minute limit) for this path, or None to skip counting."""
    if any(m in path for m in _EXEMPT_MARKERS):
        return None
    if any(m in path for m in _AUTH_MARKERS):
        return "auth", RATE_LIMIT_AUTH_PER_MINUTE
    if any(m in path for m in _EMBED_MARKERS):
        return "embed", RATE_LIMIT_EMBED_PER_MINUTE
    return "default", RATE_LIMIT_DEFAULT_PER_MINUTE


def _identity(headers: Headers, scope: Scope) -> str:
    """Who to bucket this request under: API key if present, else client IP.

    The IP is read from X-Forwarded-For first because the app sits behind a
    proxy whose socket address is shared by every caller; the raw socket is
    the fallback for a direct connection.
    """
    api_key = headers.get("x-api-key")
    if api_key:
        # The prefix, not the secret: enough to separate tenants, and a key
        # fragment does not belong in a Redis keyspace that is dumped in logs.
        return f"key:{api_key[:12]}"
    forwarded = headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip()
    if not ip:
        client = scope.get("client")
        ip = client[0] if client else "unknown"
    return f"ip:{ip}"


class RateLimitMiddleware:
    """Count each HTTP request and reject the ones over their window's limit."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not RATE_LIMIT_ENABLED or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        classified = _classify(scope.get("path", ""))
        if classified is None:
            await self.app(scope, receive, send)
            return

        bucket, limit = classified
        headers = Headers(scope=scope)
        identity = _identity(headers, scope)

        allowed, retry_after = await rate_limiter.check(
            bucket=bucket,
            identity=identity,
            limit=limit,
            window_secs=_WINDOW_SECS,
        )
        if allowed:
            await self.app(scope, receive, send)
            return

        logger.warning(
            f"Rate limit hit: bucket={bucket} identity={identity} "
            f"limit={limit}/{_WINDOW_SECS}s path={scope.get('path')}"
        )
        await self._send_429(send, retry_after)

    async def _send_429(self, send: Send, retry_after: int) -> None:
        body = json.dumps(
            {"detail": "Too many requests. Please slow down and retry."}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(max(retry_after, 1)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

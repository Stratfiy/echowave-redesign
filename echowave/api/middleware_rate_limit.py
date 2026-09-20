"""The ASGI middleware that applies the request gate to every HTTP call.

Route classification lives here rather than on each endpoint so a new route
is covered the day it is added, not the day someone remembers to decorate it.
Three classes, cheapest allowance last:

* ``auth`` — login, signup, OTP, password, MFA. The brute-force surface, so
  the tightest bucket, always keyed by IP because an attacker guessing a
  password has no key yet.
* ``embed`` — the public, unauthenticated embed/talk endpoints. The cost-DoS
  surface (each session spins up a model), keyed by IP.
* ``default`` — everything else, counted against the API key as well when one
  is present, so one tenant cannot spend another's allowance.

Every request is counted against its client address whatever its class, and a
request carrying an API key is counted against the key *in addition*. The key
used to replace the address bucket, which meant an unauthenticated caller
could mint a fresh bucket per request out of a random header -- see
``_identities``. The address itself is read from a position in
X-Forwarded-For the caller cannot write to -- see ``_client_address``.

Health and OPTIONS preflight are never counted: monitoring and CORS probes
are not abuse, and rate-limiting a liveness check turns a Redis blip into a
false outage alarm.
"""

from __future__ import annotations

import hashlib
import json

from loguru import logger
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.constants import (
    RATE_LIMIT_AUTH_PER_MINUTE,
    RATE_LIMIT_DEFAULT_PER_MINUTE,
    RATE_LIMIT_EMBED_PER_MINUTE,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_TRUSTED_PROXY_HOPS,
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


#: Reads that carry an auth-looking segment but are not a credential
#: attempt. ``/user/auth/user`` is "who am I": the app asks it on every
#: screen, from the server and the browser both, and with the auth bucket's
#: twenty a minute one person clicking through the app was refused within a
#: minute of signing in. A credential attempt is a POST to the auth router;
#: this is a GET of the session it already has.
_WHOAMI_MARKERS = ("/user/auth/user",)


def _classify(path: str) -> tuple[str, int] | None:
    """(bucket, per-minute limit) for this path, or None to skip counting."""
    if any(m in path for m in _EXEMPT_MARKERS):
        return None
    if any(m in path for m in _WHOAMI_MARKERS):
        return "default", RATE_LIMIT_DEFAULT_PER_MINUTE
    if any(m in path for m in _AUTH_MARKERS):
        return "auth", RATE_LIMIT_AUTH_PER_MINUTE
    if any(m in path for m in _EMBED_MARKERS):
        return "embed", RATE_LIMIT_EMBED_PER_MINUTE
    return "default", RATE_LIMIT_DEFAULT_PER_MINUTE


def _client_address(headers: Headers, scope: Scope) -> str:
    """The caller's address, read from a position the caller cannot choose.

    X-Forwarded-For is a list every proxy appends the peer it saw to, so it
    reads oldest-first and the *rightmost* entries are the ones our own
    infrastructure wrote. This used to take the leftmost, which is the entry a
    caller types for themselves -- so varying it produced a fresh bucket per
    request and the limit never fired.

    Counting ``RATE_LIMIT_TRUSTED_PROXY_HOPS`` in from the right lands on the
    peer our outermost trusted proxy saw. Anything further left is caller text
    and is ignored.

    Falls back to the socket when the chain is shorter than the configured
    hops, which means the header did not come through our chain: indexing into
    it anyway would read that caller text as the client. Zero hops ignores the
    header outright -- with no proxy in front, none of it was written by anyone
    we trust.
    """
    hops = max(0, RATE_LIMIT_TRUSTED_PROXY_HOPS)
    if hops:
        # Blank entries dropped before indexing, or a caller could insert one
        # to push the real entry out of position.
        chain = [
            entry.strip()
            for entry in headers.get("x-forwarded-for", "").split(",")
            if entry.strip()
        ]
        if len(chain) >= hops:
            return chain[-hops]

    client = scope.get("client")
    return client[0] if client else "unknown"


def _identities(headers: Headers, scope: Scope) -> list[str]:
    """Every bucket this request counts against. All of them must be under.

    The API key used to *replace* the address bucket, and the middleware runs
    long before anything authenticates it, so the key did not have to be real.
    A fresh random string per request was a fresh bucket per request, and the
    limit counted to one forever -- on ``/user/auth/*``, the brute-force
    bucket, that made twenty password guesses a minute into unlimited ones.

    So the key adds a bucket rather than replacing one. Every request is
    counted against its address, which it cannot choose; a request carrying a
    key is counted against the key as well, which is what keeps one tenant from
    spending another's allowance -- the reason keys were used here at all.

    The consequence worth naming: tenants sharing one egress address now share
    that address's allowance. That is the safe direction of the trade, and it
    is what ``RATE_LIMIT_DEFAULT_PER_MINUTE`` is there to tune.
    """
    identities = [f"ip:{_client_address(headers, scope)}"]

    api_key = (headers.get("x-api-key") or "").strip()
    if api_key:
        # A digest, not a prefix. This string is logged on every refusal and
        # lives in a Redis keyspace that gets dumped, and a prefix of a
        # credential is still part of a credential. Truncated because the
        # bucket only needs to tell keys apart, not to be reversible.
        digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        identities.append(f"key:{digest}")

    return identities


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

        # Every bucket, not the first one that passes. Computing both and
        # checking one would be the same defect in a new place.
        for identity in _identities(headers, scope):
            allowed, retry_after = await rate_limiter.check(
                bucket=bucket,
                identity=identity,
                limit=limit,
                window_secs=_WINDOW_SECS,
            )
            if allowed:
                continue

            logger.warning(
                f"Rate limit hit: bucket={bucket} identity={identity} "
                f"limit={limit}/{_WINDOW_SECS}s path={scope.get('path')}"
            )
            await self._send_429(send, retry_after)
            return

        await self.app(scope, receive, send)

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

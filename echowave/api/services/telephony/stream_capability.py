"""Who is allowed to attach to a call's audio, and for how long.

The media socket at ``/api/v1/telephony/ws/{workflow_id}/{organization_id}/
{workflow_run_id}`` is dialled back by the carrier, so every part of that URL is
supplied by whoever connects. It had no other check. Scoping the lookups behind
it by ``organization_id`` prevents an *accidental* cross-org mismatch — a
carrier posting the wrong id gets a 4404 rather than another tenant's call — but
it prevents nothing deliberate, because the id is one of the three numbers the
caller chose. Three small integers are not a secret: the triple was a guessable
bearer capability granting live, bidirectional audio on somebody else's call.

So the URL now carries a capability minted here at the moment it is built, and
the socket refuses a connection that cannot present one for exactly that triple.
The token is 32 random bytes; guessing it is not a thing that happens.

**Why it expires rather than being spent on first read.** ``voice_otp`` — the
same shape, one module over — deletes its token on read, and should: that URL is
fetched exactly once, and a replay would read somebody a verification code. This
one is different in a way that matters operationally. A carrier that loses the
websocket mid-call reconnects to the same URL, and a token already spent would
turn a recoverable blip into a dropped call for a customer who is mid-sentence.
The window that needs covering is mint-to-connect, which is seconds, so a short
TTL closes the same door without inventing that failure. The token dies with the
call either way — nothing outlives ``TOKEN_TTL_SECONDS``.

**Bound to the triple, not merely valid.** Verification re-checks the ids the
token was minted for against the ones in the path. A token is therefore useless
on any run but its own, so a leaked one — an access log, a carrier's dashboard —
grants at most a replay of the call it was already for.

ARI is deliberately not covered here. It connects on its own route with its own
shape, and the Asterisk behind it is the customer's own machine on the
customer's own network; see ``ari_manager``.
"""

from __future__ import annotations

import json
import secrets

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL, TELEPHONY_STREAM_TOKEN_TTL_SECONDS
from api.utils.common import get_backend_endpoints

#: Namespaced so a token is never confused with any other short-lived key, and
#: so an operator reading Redis can tell what they are looking at.
_PREFIX = "telephony:stream:"

#: The query parameter the carrier hands back to us. A query string rather than
#: another path segment because every provider's stream element takes a URL
#: verbatim, and appending is the one edit that works for all of them.
TOKEN_PARAM = "t"


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(REDIS_URL, decode_responses=True)


async def mint(
    *, workflow_id: int, organization_id: int, workflow_run_id: int
) -> str | None:
    """A capability for one run's media socket.

    ``None`` when Redis cannot be reached. The caller then builds the URL
    without one, which is the same URL it built before this module existed —
    a call that still connects rather than a call that cannot be placed. Redis
    being down already stops new calls at the concurrency slot, so this is not
    a hole somebody can open by attacking Redis; it is what keeps a partial
    outage from being a total one.
    """
    token = secrets.token_urlsafe(32)
    try:
        client = await _redis()
        try:
            await client.set(
                _PREFIX + token,
                json.dumps(
                    {
                        "workflow_id": workflow_id,
                        "organization_id": organization_id,
                        "workflow_run_id": workflow_run_id,
                    }
                ),
                ex=TELEPHONY_STREAM_TOKEN_TTL_SECONDS,
            )
        finally:
            await client.aclose()
    except Exception as exc:
        logger.warning(
            "Could not mint a stream capability for run {}: {}. The socket URL "
            "will carry no token.",
            workflow_run_id,
            exc,
        )
        return None
    return token


async def verify(
    token: str | None,
    *,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
) -> bool:
    """Whether this token grants this exact run's audio.

    False for a missing token, an expired one, one that never existed, and one
    minted for a different call. Also false when Redis is unreachable: a check
    that cannot be performed has not passed, and the caller decides what to do
    about that — see ``TELEPHONY_WS_REQUIRE_TOKEN``.
    """
    if not token:
        return False
    try:
        client = await _redis()
        try:
            raw = await client.get(_PREFIX + token)
        finally:
            await client.aclose()
    except Exception as exc:
        logger.warning("Could not verify a stream capability: {}", exc)
        return False

    if not raw:
        return False
    try:
        granted = json.loads(raw)
    except ValueError:
        return False

    return (
        granted.get("workflow_id") == workflow_id
        and granted.get("organization_id") == organization_id
        and granted.get("workflow_run_id") == workflow_run_id
    )


async def stream_url(
    *, workflow_id: int, organization_id: int, workflow_run_id: int
) -> str:
    """The media socket URL to hand a carrier, capability included.

    The one place this URL is spelled. It used to be written out by hand in
    seven places — five provider stream elements and two inbound routes — which
    is why adding anything to it (a token, a version, a region) meant finding
    all seven and is how six of them would have kept the old shape.
    """
    _, wss_backend_endpoint = await get_backend_endpoints()
    url = (
        f"{wss_backend_endpoint}/api/v1/telephony/ws/"
        f"{workflow_id}/{organization_id}/{workflow_run_id}"
    )
    token = await mint(
        workflow_id=workflow_id,
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
    )
    return f"{url}?{TOKEN_PARAM}={token}" if token else url

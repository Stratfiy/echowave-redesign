"""Fan out a search, gather what comes back, answer a caller who is waiting.

UHI discovery is a broadcast. The agent asks the gateway for a cardiologist on
Thursday; the gateway fans that out; individual providers answer over the next
few seconds, each with its own HTTP call back to *us*, each carrying the
``message_id`` we sent. There is no response to the request that started it.

Every tool the agent can call today is request/response. This is not, and the
gap is not academic: a person is on the phone, listening to silence, while the
replies trickle in. So the shape has to be "ask, hold the line, answer with
what arrived" rather than "ask and block until done" — there is no done. A
provider that never answers is normal, not an error, and waiting for it is how
you lose the caller.

**Two processes, not one.** The web worker that receives a provider's callback
is almost never the one holding the call — callbacks arrive on whatever worker
the load balancer picked. So the collection point is Redis, not memory. A
version of this that used a dict would work perfectly on one worker and drop
every reply in production, which is the kind of bug that passes review.

**Why polling rather than pub/sub.** A subscriber has to exist before the
message is published, and here the first provider can answer before the caller
has finished the sentence that triggered the search. A list that replies push
onto and the waiter drains has no such race: replies that land early are simply
already there. It costs a poll every 150ms for a few seconds, on one call.

**The deadline is the product decision.** Wait too long and the caller hears
silence; too little and the good provider that took 1.2 seconds is missing.
``min_results`` lets a search finish early when enough has arrived to be worth
saying out loud — the common case is that two or three answer quickly and the
rest never do.

This module does not know what a UHI message looks like beyond its
``message_id``. That is deliberate: the protocol is at 0.0.1 and its envelope
will move, but "fan out, collect until a deadline, answer the person waiting"
will not.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

#: One Redis list per in-flight search.
KEY_PREFIX = "uhi:discovery:"

#: How long a bucket outlives its search. Long enough that a late reply lands
#: somewhere harmless rather than erroring, short enough that an abandoned
#: search does not sit in Redis for the rest of the day.
BUCKET_TTL_SECONDS = 120

#: How often the waiter drains. 150ms is under what anybody hears as a pause
#: and is 20 round trips over a 3s wait, which Redis does not notice.
POLL_INTERVAL_SECONDS = 0.15

#: What a live call can stand. Beyond about four seconds of silence a caller
#: assumes the line dropped, and the agent should be saying something rather
#: than waiting.
DEFAULT_DEADLINE_SECONDS = 3.0

#: Refuse to hold a caller longer than this however the tool was configured.
#: A misconfigured 30-second wait is a dropped call, not a slow one.
MAX_DEADLINE_SECONDS = 10.0


@dataclass
class DiscoveryResult:
    """What arrived before the deadline, and why we stopped waiting."""

    replies: list[dict[str, Any]] = field(default_factory=list)
    #: "enough" when min_results was reached, "deadline" when time ran out.
    #: The agent can say "here are three" differently from "that is all I
    #: found in time", and an operator debugging an empty search needs to know
    #: which happened.
    reason: str = "deadline"
    waited_seconds: float = 0.0

    @property
    def found_any(self) -> bool:
        return bool(self.replies)


class DiscoveryCollector:
    """Gathers ``on_search`` callbacks for one search, across workers."""

    def __init__(self, redis_url: str = REDIS_URL):
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(
                self._redis_url, decode_responses=True
            )
        return self._redis

    @staticmethod
    def _key(message_id: str) -> str:
        return f"{KEY_PREFIX}{message_id}"

    async def record_reply(self, message_id: str, payload: dict[str, Any]) -> None:
        """Called by the callback route when a provider answers.

        Never raises into the route. A provider whose reply we fail to store
        gets a 200 anyway — the alternative is it retrying into a search whose
        caller hung up two minutes ago, and one lost reply is a shorter list,
        not a broken call.
        """
        try:
            client = await self._client()
            key = self._key(message_id)
            async with client.pipeline(transaction=True) as pipe:
                pipe.rpush(key, json.dumps(payload))
                # Refreshed on every push rather than set once, so a search
                # that is still receiving replies cannot expire underneath the
                # waiter.
                pipe.expire(key, BUCKET_TTL_SECONDS)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - a lost reply is not an outage
            logger.warning(
                "UHI: could not record a discovery reply for {}: {}",
                message_id,
                type(exc).__name__,
            )

    async def collect(
        self,
        message_id: str,
        *,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        min_results: int = 0,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> DiscoveryResult:
        """Wait for replies, and stop at whichever comes first.

        ``min_results`` of 0 means "wait the whole deadline", which is right
        when the agent intends to compare everything. Anything above it is
        "answer as soon as you can be useful".
        """
        deadline_seconds = min(max(deadline_seconds, 0.0), MAX_DEADLINE_SECONDS)
        started = time.monotonic()
        key = self._key(message_id)

        try:
            client = await self._client()
        except Exception as exc:  # noqa: BLE001
            # No Redis means no replies can have been stored either, so this is
            # an empty result rather than a failure to report upward: the agent
            # says it could not find anything, which is true.
            logger.error("UHI: discovery unavailable — Redis unreachable: {}", exc)
            return DiscoveryResult(reason="unavailable")

        while True:
            elapsed = time.monotonic() - started
            try:
                raw = await client.lrange(key, 0, -1)
            except Exception as exc:  # noqa: BLE001
                logger.error("UHI: could not read discovery replies: {}", exc)
                return DiscoveryResult(reason="unavailable", waited_seconds=elapsed)

            replies = _decode(raw)

            if min_results and len(replies) >= min_results:
                return DiscoveryResult(
                    replies=replies, reason="enough", waited_seconds=elapsed
                )
            if elapsed >= deadline_seconds:
                return DiscoveryResult(
                    replies=replies, reason="deadline", waited_seconds=elapsed
                )

            # Never sleep past the deadline: overshooting it is exactly the
            # silence this class exists to bound.
            await asyncio.sleep(min(poll_interval, deadline_seconds - elapsed))

    async def discard(self, message_id: str) -> None:
        """Drop a search's bucket once its answer has been given.

        Not required — the TTL gets there eventually — but a busy line would
        otherwise carry a couple of minutes of dead searches in Redis at all
        times.
        """
        try:
            client = await self._client()
            await client.delete(self._key(message_id))
        except Exception:  # noqa: BLE001 - the TTL is the backstop
            pass

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


def _decode(raw: list[str]) -> list[dict[str, Any]]:
    """Parse stored replies, skipping any that will not parse.

    One provider sending something malformed must not lose the replies from
    the providers that behaved.
    """
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            parsed = json.loads(item)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out

"""A coarse, Redis-backed request gate for the whole HTTP API.

The provider-side limiters (campaign dialing, Plivo) protect a carrier from
us. This protects us from the internet: login and OTP brute force, and the
cost-DoS where someone hammers a public share link to burn the owner's
credit. It is deliberately coarse — a fixed window counted per client per
route class — because a safety gate that is cheap and always on beats a
precise one that is skipped for being expensive.

Two rules the design turns on:

* **Fail open.** If Redis is unreachable the request is allowed, never
  refused. A limiter that 429s the entire API because its counter store
  hiccuped is a worse outage than the abuse it guards against.

* **Identity before IP.** An authenticated caller is bucketed by API-key
  prefix so one tenant's traffic never spends another's allowance; only
  anonymous traffic falls back to the forwarded client IP.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

# INCR the counter, and set the window on the first hit of it. Atomic so two
# concurrent requests cannot both see "1" and both skip the EXPIRE, which
# would leave a key that never resets and locks the caller out for good.
_INCR_AND_EXPIRE = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter:
    """Fixed-window counter over Redis, shared across API workers."""

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def check(
        self, *, bucket: str, identity: str, limit: int, window_secs: int
    ) -> tuple[bool, int]:
        """Count this request. Return (allowed, retry_after_secs).

        ``retry_after_secs`` is meaningful only when allowed is False; it is
        the Redis TTL on the window, i.e. how long until the counter resets.
        On any Redis error the request is allowed and retry_after is 0 —
        failing open is the whole point.
        """
        key = f"ratelimit:{bucket}:{identity}"
        try:
            client = await self._client()
            current = await client.eval(_INCR_AND_EXPIRE, 1, key, window_secs)
            if int(current) <= limit:
                return True, 0
            ttl = await client.ttl(key)
            # A key with no TTL (-1) or already gone (-2) should still hand the
            # caller a sane number to wait, not a negative one.
            return False, ttl if ttl and ttl > 0 else window_secs
        except Exception as e:  # noqa: BLE001 - a gate must never end the request
            logger.warning(f"Rate limiter unavailable, allowing request: {e}")
            return True, 0


rate_limiter = RateLimiter()

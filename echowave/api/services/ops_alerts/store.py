"""Where the operational alerts keep their state: Redis, under one prefix.

No table. What is stored is short-lived and rebuildable -- per-minute error
counts, the time each scheduled tick last completed, the incidents open right
now and the last hundred resolved -- and losing it to a Redis flush costs at
most one repeated alert. Durable history is the email itself.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from api import constants

#: Every key this package writes starts with this. Tests point it elsewhere.
KEY_PREFIX = "decibyl:ops_alerts:"


def key(*parts: Any) -> str:
    return KEY_PREFIX + ":".join(str(p) for p in parts)


@asynccontextmanager
async def connection(client=None) -> AsyncIterator[Any]:
    """The given client, or a fresh one closed afterwards."""
    if client is not None:
        yield client
        return
    own = aioredis.from_url(constants.REDIS_URL)
    try:
        yield own
    finally:
        try:
            await own.aclose()
        except Exception:  # noqa: BLE001 - closing must not mask the real error
            pass


def text(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)

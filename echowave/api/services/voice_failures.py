"""Calls whose voice went quiet, kept for a day so staff can see them.

A voice provider can stop answering without saying why. On 23 Sept 2026
ElevenLabs refused every request from our key for most of a day: it accepted
the text, sent no audio and no error, and every call -- phone and browser --
was silence. Our hourly key check passed throughout, because the key still
authenticated. The only witness was the call itself, so the call now reports
it (``pipecat/voice_watch.py``) and this is where the report lands.

Redis, not a table: this is a recent-history signal for an operator, not a
record anybody bills or audits, and a day is all of it that matters. One
sorted set per provider, scored by time, trimmed on every write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import redis.asyncio as aioredis
from loguru import logger

from api import constants

#: How far back the staff screen looks.
WINDOW_SECONDS = 24 * 60 * 60

_PREFIX = "decibyl:voice_failures:"


@dataclass(frozen=True)
class VoiceFailures:
    provider: str
    count: int
    last_at: float
    last_run_id: int | None
    last_model: str | None


def _client():
    return aioredis.from_url(constants.REDIS_URL, decode_responses=True)


async def record(
    *, provider: str, model: str | None, run_id: int | None, at: float | None = None
) -> None:
    """Note one call whose voice went quiet. Never raises: a report about a
    failing call must not become a second failure on it."""
    now = at if at is not None else time.time()
    key = _PREFIX + (provider or "unknown")
    member = f"{now:.3f}|{run_id or ''}|{model or ''}"
    try:
        client = _client()
        try:
            await client.zadd(key, {member: now})
            await client.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
            await client.expire(key, WINDOW_SECONDS)
        finally:
            await client.aclose()
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not record a silent voice for {}: {}", provider, error)


async def recent(now: float | None = None) -> list[VoiceFailures]:
    """Every provider with a silent call in the last day, worst first."""
    now = now if now is not None else time.time()
    out: list[VoiceFailures] = []
    client = _client()
    try:
        async for key in client.scan_iter(match=_PREFIX + "*"):
            members = await client.zrangebyscore(key, now - WINDOW_SECONDS, "+inf")
            if not members:
                continue
            at, run_id, model = members[-1].split("|", 2)
            out.append(
                VoiceFailures(
                    provider=key[len(_PREFIX) :],
                    count=len(members),
                    last_at=float(at),
                    last_run_id=int(run_id) if run_id else None,
                    last_model=model or None,
                )
            )
    finally:
        await client.aclose()
    return sorted(out, key=lambda f: (-f.count, -f.last_at))

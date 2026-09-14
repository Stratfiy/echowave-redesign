"""The reply as it forms, so a screen can show it before it is a row.

A bot's answer is written to the timeline once, whole, by a worker. Between
the question and that row the screen shows "Thinking…", which is honest
and dull. This is the small thing in between: the worker writes the text so
far to Redis as it streams from the model, and the screen polls it while
the thinking row is up. Redis rather than the database because it is
written many times a second and worth nothing once the row exists; a TTL
rather than a delete-on-finish because a worker that dies mid-answer must
not leave a half-sentence on the screen for ever.

One key per thread: Decibyl's thread for the organisation, or a bot's chat
(``workflow_id``) -- the same key shape for every bot, so the stream's poll
does not care who is answering.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from redis import asyncio as aioredis

from api.constants import REDIS_URL

#: A draft older than this is a worker that died; the screen falls back to
#: the thinking row, which is what it would have shown anyway.
TTL_SECONDS = 120
#: Never more than this on the wire: a reply is sentences, not a document.
MAX_CHARS = 4000

_redis: Optional[aioredis.Redis] = None


async def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def key(organization_id: int, workflow_id: Optional[int] = None) -> str:
    who = "assistant" if workflow_id is None else f"bot:{workflow_id}"
    return f"reply_draft:{organization_id}:{who}"


async def set_draft(
    organization_id: int, text: str, *, workflow_id: Optional[int] = None
) -> None:
    """The text so far. Silent on failure: a draft is a nicety, the row is
    the record, and a Redis blip must not end an answer."""
    try:
        redis = await _client()
        await redis.set(
            key(organization_id, workflow_id), text[:MAX_CHARS], ex=TTL_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not write a reply draft: {}", exc)


async def clear(organization_id: int, *, workflow_id: Optional[int] = None) -> None:
    try:
        redis = await _client()
        await redis.delete(key(organization_id, workflow_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not clear a reply draft: {}", exc)


async def get(organization_id: int, *, workflow_id: Optional[int] = None) -> str:
    """The text so far, or empty."""
    try:
        redis = await _client()
        return (await redis.get(key(organization_id, workflow_id))) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read a reply draft: {}", exc)
        return ""

"""Stop: a person asks Decibyl to stop answering, and keeps what it said.

The screen cannot cancel a worker directly, so it leaves a note in Redis
keyed exactly like the forming reply (``reply_draft.key``) and the worker
reads it between streamed chunks. When it sees the note it raises
``Stopped`` with the text so far; Decibyl records that text as the reply,
marked ``stopped``, so the partial output stays on the thread as a row
rather than vanishing with the draft.

A note nobody reads expires on its own (a stop sent after the reply landed
does nothing), and a Redis failure never ends an answer.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from api.services.workflow import reply_draft

#: Longer than any one reply takes; a stale note must not stop the next one.
TTL_SECONDS = 120


class Stopped(Exception):
    """Raised from inside the stream; ``text`` is what had formed."""

    def __init__(self, text: str) -> None:
        super().__init__("stopped")
        self.text = text


def key(organization_id: int, thread_id: Optional[str] = None) -> str:
    return (
        "reply_stop:"
        + reply_draft.key(organization_id, thread_id=thread_id).split(":", 1)[1]
    )


async def request(organization_id: int, thread_id: Optional[str] = None) -> bool:
    try:
        redis = await reply_draft._client()
        await redis.set(key(organization_id, thread_id), "1", ex=TTL_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not ask Decibyl to stop: {}", exc)
        return False


async def requested(organization_id: int, thread_id: Optional[str] = None) -> bool:
    try:
        redis = await reply_draft._client()
        return bool(await redis.get(key(organization_id, thread_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read a stop note: {}", exc)
        return False


async def clear(organization_id: int, thread_id: Optional[str] = None) -> None:
    try:
        redis = await reply_draft._client()
        await redis.delete(key(organization_id, thread_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not clear a stop note: {}", exc)

"""How a request reaches the worker running a browser, and back.

The browser runs inside one ARQ job (``tasks/browser.py``) for its whole
life; the person's presses arrive on whichever api worker handled the
request. Redis carries the difference, three ways:

- ``browser:cmd:<session>`` -- a list of commands for the job: take over,
  a click or keys while taken over, hand back, stop, an approval or a
  decline. The job drains it between the box's events.
- ``browser:screen:<session>`` -- the latest screenshot, overwritten.
  Never in the database: a picture of somebody's bank page is kept only as
  long as the panel needs it (an hour at most).
- ``browser:outcome:<gate>`` -- what happened when an approved step ran,
  for the approval job waiting to say so on the card.

Text typed during Take over passes through the command list and nowhere
else: it is not logged and not written to the step list.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from api import constants

TTL_SECONDS = 60 * 60

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(constants.REDIS_URL, decode_responses=True)
    return _client


def reset() -> None:
    """Forget the client (a test's event loop ended)."""
    global _client
    _client = None


def _cmd_key(session_uuid: str) -> str:
    return f"browser:cmd:{session_uuid}"


def _screen_key(session_uuid: str) -> str:
    return f"browser:screen:{session_uuid}"


def _outcome_key(gate_id: str) -> str:
    return f"browser:outcome:{gate_id}"


async def push_command(session_uuid: str, command: dict[str, Any]) -> None:
    key = _cmd_key(session_uuid)
    await _redis().rpush(key, json.dumps(command))
    await _redis().expire(key, TTL_SECONDS)


async def pop_commands(session_uuid: str, *, limit: int = 50) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    key = _cmd_key(session_uuid)
    for _ in range(limit):
        raw = await _redis().lpop(key)
        if raw is None:
            break
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


async def set_screen(session_uuid: str, screen: dict[str, Any]) -> None:
    await _redis().set(_screen_key(session_uuid), json.dumps(screen), ex=TTL_SECONDS)


async def get_screen(session_uuid: str) -> dict[str, Any] | None:
    raw = await _redis().get(_screen_key(session_uuid))
    return json.loads(raw) if raw else None


async def drop(session_uuid: str) -> None:
    await _redis().delete(_screen_key(session_uuid), _cmd_key(session_uuid))


async def push_outcome(gate_id: str, outcome: dict[str, Any]) -> None:
    key = _outcome_key(gate_id)
    await _redis().rpush(key, json.dumps(outcome))
    await _redis().expire(key, TTL_SECONDS)


async def wait_outcome(gate_id: str, *, timeout: float) -> dict[str, Any] | None:
    got = await _redis().blpop([_outcome_key(gate_id)], timeout=max(1, int(timeout)))
    if not got:
        return None
    try:
        return json.loads(got[1])
    except ValueError:
        return None

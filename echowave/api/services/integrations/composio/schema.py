"""What a Composio tool takes: its argument schema, fetched once and kept.

A tool attached from the chat carries its slug and nothing else, and until
now that was all the model saw: ``app_gmail_send_email`` with an empty
argument list. The model then guessed the arguments, and the guess failed
at Composio rather than in front of anybody who could fix it. The schema is
published; it just was never read.

It is read here, **on demand and once**. Nothing fetches every schema for
every tool an organisation has: a schema is asked for when a model is about
to call the tool (the thread's ``load_tool``) or when a call opens a node
that carries it, and it is kept for a day in Redis and for the life of the
process in memory. A miss on a live call is answered from the row's own
description, as before, and the fetch runs behind the call so the next one
has it.

Never raises. A schema we could not read is ``None``, and the caller says
what it can with the description alone -- the state every tool was in
before this module existed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import COMPOSIO_BASE_URL, COMPOSIO_TIMEOUT_SECS, REDIS_URL
from api.services.integrations.composio.client import _headers, is_configured

CACHE_PREFIX = "composio:tool_schema:v1:"
CACHE_TTL_SECONDS = 24 * 60 * 60

#: How much of a parameter's description the model is given. Composio's run
#: to paragraphs; the model needs the first sentence.
MAX_PARAMETER_DESCRIPTION = 240
#: How many parameters a tool schema carries into a prompt. Some Composio
#: tools declare forty; the model can be told the rest exist.
MAX_PARAMETERS = 24

_JSON_KEYS = (
    "type",
    "description",
    "enum",
    "items",
    "properties",
    "required",
    "default",
)

#: In-process copy, so a call that opens the same node twice reads once.
_memory: dict[str, Optional[dict[str, Any]]] = {}
_inflight: dict[str, asyncio.Task] = {}


def _key(slug: str) -> str:
    return CACHE_PREFIX + slug.strip().upper()


def _trim(node: Any, depth: int = 0) -> Any:
    """A JSON schema node with only the keys a function schema uses."""
    if not isinstance(node, dict) or depth > 4:
        return node
    kept: dict[str, Any] = {}
    for key in _JSON_KEYS:
        if key not in node:
            continue
        value = node[key]
        if key == "description" and isinstance(value, str):
            value = value.strip()[:MAX_PARAMETER_DESCRIPTION]
        elif key == "properties" and isinstance(value, dict):
            value = {
                name: _trim(child, depth + 1)
                for name, child in list(value.items())[:MAX_PARAMETERS]
            }
        elif key == "items":
            value = _trim(value, depth + 1)
        kept[key] = value
    return kept


def normalise(raw: Any) -> Optional[dict[str, Any]]:
    """The ``{"type": "object", "properties", "required"}`` a function
    schema takes, from whatever shape Composio returned it in."""
    if not isinstance(raw, dict):
        return None
    schema = raw
    for key in ("input_parameters", "inputParameters", "parameters"):
        if isinstance(raw.get(key), dict):
            schema = raw[key]
            break
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    trimmed = _trim({"type": "object", **schema})
    trimmed["type"] = "object"
    trimmed.setdefault("properties", {})
    required = [
        r
        for r in (schema.get("required") or [])
        if isinstance(r, str) and r in trimmed["properties"]
    ]
    trimmed["required"] = required
    return trimmed


async def _cache() -> aioredis.Redis | None:
    try:
        return await aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - a cache is never load-bearing
        logger.debug("Composio schema cache unavailable: {}", exc)
        return None


async def cached(slug: str) -> Optional[dict[str, Any]]:
    """The schema if it is already known, with no network. None otherwise.

    For the call path: a node opening on a live call reads this and nothing
    slower, so a tool whose schema has never been read is offered with its
    description alone rather than making a caller wait on a vendor.
    """
    key = _key(slug)
    if key in _memory:
        return _memory[key]
    cache = await _cache()
    if cache is None:
        return None
    try:
        text = await cache.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read the Composio schema cache: {}", exc)
        return None
    if not text:
        return None
    try:
        schema = json.loads(text)
    except ValueError:
        return None
    _memory[key] = schema
    return schema


async def _fetch(slug: str, timeout_secs: float) -> Optional[dict[str, Any]]:
    if not is_configured():
        return None
    url = f"{COMPOSIO_BASE_URL}/api/v3.1/tools/{slug.strip().upper()}"
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.get(url, headers=_headers())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not read the Composio schema for {}: {}", slug, exc)
        return None
    if response.status_code >= 400:
        logger.warning(
            "Composio schema for {} failed: HTTP {}", slug, response.status_code
        )
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    schema = normalise(body)
    if schema is None:
        logger.warning("Composio schema for {} had no parameters we could read", slug)
    return schema


async def input_schema(
    slug: str, *, timeout_secs: float = COMPOSIO_TIMEOUT_SECS
) -> Optional[dict[str, Any]]:
    """The tool's argument schema, from cache or from Composio.

    One fetch per slug at a time: two turns asking for the same schema in
    the same second share the request rather than both paying for it.
    """
    known = await cached(slug)
    if known is not None:
        return known
    key = _key(slug)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(_fetch(slug, timeout_secs))
        _inflight[key] = task
    try:
        schema = await task
    finally:
        _inflight.pop(key, None)
    if schema is None:
        return None
    _memory[key] = schema
    cache = await _cache()
    if cache is not None:
        try:
            await cache.set(key, json.dumps(schema), ex=CACHE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write the Composio schema cache: {}", exc)
    return schema


def warm(slug: str) -> None:
    """Fetch behind the caller so the next reader finds it. Fire and forget."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    key = _key(slug)
    if key in _memory or key in _inflight:
        return
    loop.create_task(input_schema(slug))


def forget(slug: Optional[str] = None) -> None:
    """Drop the in-process copy. For tests, and for a redeploy that changed
    ``MAX_PARAMETERS``."""
    if slug is None:
        _memory.clear()
    else:
        _memory.pop(_key(slug), None)


def properties_of(schema: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """``(properties, required)`` for a function schema, empty when unknown."""
    if not schema:
        return {}, []
    return dict(schema.get("properties") or {}), list(schema.get("required") or [])


__all__ = [
    "CACHE_PREFIX",
    "CACHE_TTL_SECONDS",
    "MAX_PARAMETERS",
    "cached",
    "forget",
    "input_schema",
    "normalise",
    "properties_of",
    "warm",
]

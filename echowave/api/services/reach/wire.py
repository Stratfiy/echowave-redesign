"""Talking to an outside MCP server: probe, list its tools, call one.

One short session per call rather than a held connection: a chat turn is
seconds apart from the next, and a session held open per person per server
is a resource that leaks the day a worker restarts mid-turn. The cost is an
``initialize`` per call, which these servers answer quickly.

Three outcomes, kept distinct because the caller does different things:

* :class:`NeedsSignIn` -- the server answered 401; the person signs in
  (OAuth) or gives a token. Nothing was done.
* :class:`ToolRefused` -- the server ran the request and said no
  (``isError``). Nothing was done, and the reason is the server's.
* :class:`WireError` -- the connection failed or timed out. For a read,
  that is "unavailable"; for a write it means *we do not know whether it
  happened* (the order may have been placed), and the card says so instead
  of retrying.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx

#: How long a call may take from the thread.
TIMEOUT_SECS = 20.0


class NeedsSignIn(Exception):
    def __init__(self, resource_metadata: str | None = None) -> None:
        super().__init__("The server wants you to sign in.")
        self.resource_metadata = resource_metadata


class ToolRefused(Exception):
    pass


class WireError(Exception):
    pass


def _headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _resource_metadata(header: str | None) -> str | None:
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key.strip().lower().endswith("resource_metadata"):
            return value.strip().strip('"') or None
    return None


async def probe(url: str, token: str | None = None) -> None:
    """Raise :class:`NeedsSignIn` if the server wants credentials we lack.

    A plain ``initialize`` over HTTP, so a 401 is read as a 401: inside the
    SDK's task group it surfaces as a cancellation that cannot be told
    apart from a dead server (see ``mcp_tool_session``).
    """
    body = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "decibyl", "version": "1"},
        },
    }
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECS, follow_redirects=False
        ) as client:
            response = await client.post(
                url,
                json=body,
                headers={
                    **_headers(token),
                    "Accept": "application/json, text/event-stream",
                },
            )
    except httpx.HTTPError as exc:
        raise WireError(
            f"The server could not be reached: {type(exc).__name__}"
        ) from exc
    if response.status_code == 401:
        raise NeedsSignIn(_resource_metadata(response.headers.get("WWW-Authenticate")))
    if response.status_code >= 400:
        raise WireError(f"The server answered {response.status_code}.")


async def _session(url: str, token: str | None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    return streamablehttp_client(
        url,
        headers=_headers(token) or None,
        timeout=timedelta(seconds=TIMEOUT_SECS),
        sse_read_timeout=timedelta(seconds=TIMEOUT_SECS),
    ), ClientSession


async def list_tools(url: str, token: str | None = None) -> list[dict[str, Any]]:
    """``[{name, description, input_schema}]`` as the server declares them."""
    transport, session_class = await _session(url, token)

    async def _go() -> list[dict[str, Any]]:
        async with transport as (read, write, _):
            async with session_class(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema or {"type": "object"},
                    }
                    for tool in listed.tools
                ]

    try:
        return await asyncio.wait_for(_go(), TIMEOUT_SECS)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        _raise_for(exc, "The server's tools could not be read")


def _status_in(exc: BaseException, seen: set[int] | None = None) -> int | None:
    """The HTTP status buried in what the SDK raised, if there is one. Its
    task group wraps a 401 in an exception group; a refused request is not
    a lost one, and must not read as "we do not know if it ran"."""
    seen = seen or set()
    if id(exc) in seen:
        return None
    seen.add(id(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    for inner in getattr(exc, "exceptions", ()) or ():
        found = _status_in(inner, seen)
        if found is not None:
            return found
    for inner in (exc.__cause__, exc.__context__):
        if inner is not None:
            found = _status_in(inner, seen)
            if found is not None:
                return found
    return None


def _raise_for(exc: BaseException, what: str) -> None:
    status = _status_in(exc)
    if status == 401:
        raise NeedsSignIn() from exc
    if status is not None and 400 <= status < 500:
        raise ToolRefused(f"The server refused the request ({status}).") from exc
    raise WireError(f"{what}: {type(exc).__name__}") from exc


def _result_data(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    text = "".join(
        getattr(part, "text", "") or "" for part in getattr(result, "content", []) or []
    )
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


async def call_tool(
    url: str, token: str | None, name: str, arguments: dict[str, Any]
) -> Any:
    """The tool's result: parsed JSON when it is JSON, else its text."""
    transport, session_class = await _session(url, token)

    async def _go() -> Any:
        async with transport as (read, write, _):
            async with session_class(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments=arguments or {})

    try:
        result = await asyncio.wait_for(_go(), TIMEOUT_SECS)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        _raise_for(exc, "The server did not answer")
    data = _result_data(result)
    if getattr(result, "isError", False):
        raise ToolRefused(str(data)[:300] or "The server refused.")
    return data


__all__ = [
    "NeedsSignIn",
    "TIMEOUT_SECS",
    "ToolRefused",
    "WireError",
    "call_tool",
    "list_tools",
    "probe",
]

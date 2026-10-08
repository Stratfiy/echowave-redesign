"""The GET routes a sweep walks, read from the running API's own OpenAPI.

The list is never written down here: a route added tomorrow is swept
tomorrow, with no edit to this suite. Only routes with no path parameter are
walked -- a path parameter needs an id, and the journeys cover ids.

Required query parameters are filled where the name says what they are (a
search box gets the marker, so a search route is searched for exactly the
thing that must not leak); a route whose required parameters cannot be
guessed is still called, and a 422 from it is an answer, not a failure.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pace

#: Query parameter names that are a free-text search: filled with the marker.
SEARCH_PARAMS = re.compile(r"^(q|query|search|term|text|keyword|keywords)$", re.I)

#: Bodies are read up to this much (the privacy sweep searches them); an event
#: stream is closed after its headers so it cannot hold the sweep open.
STREAM_READ_BYTES = 8 * 1024 * 1024


#: Which flag guards which route, written from the app itself by
#: ``python -m scripts.dump_route_flags`` and kept current by
#: ``api/tests/test_e2e_route_flags_current.py``. A flagged-off route answers
#: a bare 404 by design; this is how the sweeps can name the flag instead.
ROUTE_FLAGS: dict[str, dict[str, list[str]]] = json.loads(
    (Path(__file__).parent / "route_flags.json").read_text()
)


def flags_for(path: str, method: str = "GET") -> list[str]:
    return ROUTE_FLAGS.get(path, {}).get(method, [])


@dataclass(frozen=True)
class Route:
    path: str
    params: tuple[tuple[str, str], ...]

    @property
    def id(self) -> str:
        return self.path.removeprefix("/api/v1")


def _fill(name: str, schema: dict, marker: str) -> str | None:
    if SEARCH_PARAMS.match(name):
        return marker
    kind = schema.get("type")
    fmt = schema.get("format")
    if name == "date" or fmt == "date":
        return dt.date.today().isoformat()
    if name in {"timezone", "tz"}:
        return "Asia/Kolkata"
    if "enum" in schema and schema["enum"]:
        return str(schema["enum"][0])
    if kind == "boolean":
        return "false"
    return None


def get_routes(api: str, marker: str = "e2e") -> list[Route]:
    spec = httpx.get(f"{api}/openapi.json", timeout=30).json()
    routes: list[Route] = []
    for path, operations in sorted(spec.get("paths", {}).items()):
        get = operations.get("get")
        if not get or "{" in path:
            continue
        params: list[tuple[str, str]] = []
        for parameter in get.get("parameters", []):
            if parameter.get("in") != "query":
                continue
            name = parameter["name"]
            schema = parameter.get("schema") or {}
            if not parameter.get("required") and not SEARCH_PARAMS.match(name):
                continue
            value = _fill(name, schema, marker)
            if value is not None:
                params.append((name, value))
        routes.append(Route(path=path, params=tuple(params)))
    return routes


@dataclass
class Answer:
    status: int
    seconds: float
    body: str
    retry_after: float = 0.0


def read(http: httpx.Client, base: str, route: Route, timeout: float) -> Answer:
    """GET a route, timing it to the end of the (bounded) body. A 429 is
    waited out and asked again (see ``pace``), never returned as an answer."""
    for _ in range(pace.RETRIES_ON_429):
        answer = _read_once(http, base, route, timeout)
        if answer.status != 429:
            return answer
        time.sleep(answer.retry_after)
    return answer


def _read_once(http: httpx.Client, base: str, route: Route, timeout: float) -> Answer:
    pace.wait_turn()
    started = time.monotonic()
    try:
        with http.stream(
            "GET", f"{base}{route.path}", params=list(route.params), timeout=timeout
        ) as response:
            chunks: list[bytes] = []
            size = 0
            if response.status_code == 429:
                return Answer(429, 0.0, "", pace.retry_after(response.headers))
            if "text/event-stream" in response.headers.get("content-type", ""):
                # The headers are the answer; the stream may stay quiet.
                return Answer(response.status_code, time.monotonic() - started, "")
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= STREAM_READ_BYTES:
                    break
            body = b"".join(chunks).decode("utf-8", errors="replace")
            return Answer(response.status_code, time.monotonic() - started, body)
    except httpx.TimeoutException:
        return Answer(0, time.monotonic() - started, "timed out")

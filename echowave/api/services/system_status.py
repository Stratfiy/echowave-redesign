"""The staff system strip (ADMIN-2, A7): is the platform itself all right?

`/health` says the API process answers; `/health/workers` (behind the devops
secret) says whether the background worker beats. Staff had neither on a
screen, so "are calls being costed, is Redis up, which build is this" was an
SSH session. This gathers the same signals, plus the database, the ARQ queue
and our provider balances, into one answer a superadmin page can poll.

**Every probe is time-boxed** (``PROBE_TIMEOUT_SECONDS``) and run concurrently,
and none raises: a probe that hangs or throws becomes a row that says so. A
status page that goes blank because one vendor is slow would hide exactly the
outage it exists to show.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

#: The most any single probe may take, in seconds.
PROBE_TIMEOUT_SECONDS = 2.0

#: ARQ's default queue -- ``tasks/arq.py`` sets no ``queue_name``.
ARQ_QUEUE_KEY = "arq:queue"

UNKNOWN = "unknown"


async def _probe(
    name: str, fn: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """Run one probe under the time box; never raises."""
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(fn(), timeout=PROBE_TIMEOUT_SECONDS)
        ok = result.pop("ok", True)
        return {
            "ok": ok,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            **result,
        }
    except TimeoutError:
        return {
            "ok": False,
            "latency_ms": None,
            "detail": f"No answer within {PROBE_TIMEOUT_SECONDS:g} s.",
        }
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        logger.warning("System probe {} failed: {}", name, exc)
        return {
            "ok": False,
            "latency_ms": None,
            # The class, not the message: messages can carry connection
            # strings, and this is rendered on a page.
            "detail": f"{type(exc).__name__} while probing {name}.",
        }


def build_info() -> dict[str, str]:
    """Which build is running. ``APP_VERSION`` and ``GIT_SHA`` are build
    arguments when the deploy sets them; otherwise the version falls back to
    ``pyproject.toml`` and the SHA to ``unknown``."""
    from api.constants import APP_VERSION

    version = os.getenv("APP_VERSION") or APP_VERSION or UNKNOWN
    sha = os.getenv("GIT_SHA") or UNKNOWN
    return {"version": version, "git_sha": sha}


async def _database() -> dict[str, Any]:
    from sqlalchemy import text

    from api.db import db_client

    async with db_client.async_session() as session:
        await session.execute(text("SELECT 1"))
    return {}


def _redis_client():
    import redis.asyncio as aioredis

    from api.constants import REDIS_URL

    return aioredis.from_url(REDIS_URL)


async def _redis() -> dict[str, Any]:
    client = _redis_client()
    try:
        pong = await client.ping()
    finally:
        await client.aclose()
    return {"ok": bool(pong)}


async def _queue() -> dict[str, Any]:
    client = _redis_client()
    try:
        length = await client.zcard(ARQ_QUEUE_KEY)
    finally:
        await client.aclose()
    return {"length": int(length or 0)}


async def _worker() -> dict[str, Any]:
    from api.services.worker_health import worker_health

    health = await worker_health()
    # alive is tri-state; only True is ok, and None keeps its meaning
    # ("no beat on record") in the detail rather than reading as dead.
    return {
        "ok": health.get("alive") is True,
        "alive": health.get("alive"),
        "last_seen": health.get("last_seen"),
        "age_seconds": health.get("age_seconds"),
        "detail": health.get("detail"),
    }


async def _balances() -> dict[str, Any]:
    from api.db import db_client
    from api.services.configuration import provider_balance

    async with db_client.async_session() as session:
        balances = await provider_balance.read_all(session)
    rows = [
        {
            "provider": b.provider,
            "status": b.status,
            "kind": b.kind,
            "remaining": b.remaining,
            "currency": b.currency,
            "needs_attention": b.needs_attention,
            "detail": b.detail,
        }
        for b in balances
    ]
    attention = sum(1 for r in rows if r["needs_attention"])
    return {"ok": attention == 0, "needs_attention": attention, "providers": rows}


#: Which probes decide the overall status. Provider balances are shown but do
#: not turn the strip red on their own: an empty vendor account is a task for
#: today, not a platform outage.
CORE_PROBES = ("database", "redis", "worker")


async def snapshot() -> dict[str, Any]:
    """Every probe, concurrently, each inside its own time box."""
    names = ("database", "redis", "queue", "worker", "provider_balances")
    fns = (_database, _redis, _queue, _worker, _balances)
    results = await asyncio.gather(*(_probe(n, f) for n, f in zip(names, fns)))
    probes = dict(zip(names, results))
    status = "ok" if all(probes[n]["ok"] for n in CORE_PROBES) else "degraded"
    return {
        "status": status,
        "build": build_info(),
        "probe_timeout_seconds": PROBE_TIMEOUT_SECONDS,
        **probes,
    }

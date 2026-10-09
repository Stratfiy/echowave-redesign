"""The two things the detectors need that nothing else records.

**Provider errors.** A provider failing is written to a log line and, for a
call, onto that one run's ``extra["pipeline_error"]``. Neither can be summed
cheaply over the last fifteen minutes, so the existing error-handling points
also bump a counter here: one Redis hash per minute, a field per series.

* ``call:n`` -- pipelines started; ``call:<component>:err`` -- runs on which
  that component (stt, tts, llm, telephony, other) raised an error frame,
  once per run per component however many frames it raised.
* ``llm_direct:n`` / ``llm_direct:err`` -- model turns through the builder
  client (Decibyl's replies, the builder, routing), and how many failed.
* ``dial:n`` / ``dial:err`` -- outbound dial requests to a carrier.

**Scheduled ticks completing.** The worker heartbeat says the worker is
alive; it cannot say that the routines tick has been raising every minute
since Tuesday. Each watched tick stamps the time it last *completed*.

Recording is behind the ``ops_alerts`` flag, never raises and costs one
Redis round trip: measurement is worth less than the call being measured.
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from api.services import features
from api.services.ops_alerts import store

FLAG = "ops_alerts"

#: How long a minute's counters live. Twice the longest window read.
BUCKET_TTL_SECONDS = 2 * 3600
#: How long a "this run already counted for this component" marker lives.
SEEN_TTL_SECONDS = 3 * 3600

#: What a pipeline error is attributed to. ``other`` is a bucket, not a
#: drop: an error from a processor nobody classified still counts.
COMPONENTS = ("stt", "llm", "tts", "telephony", "other")

#: The scheduled ticks watched, and what each is called in an alert. A name
#: here whose task is not decorated with ``stamped`` never completes, and
#: is reported as not running -- loud, which is the right way to be wrong.
TICKS: dict[str, str] = {
    "fire_due_routines": "Routines",
    "deliver_due_reminders": "Today reminders",
    "care_medicine_tick": "Medicine reminder calls",
    "call_when_done_tick": "Call-me-when-done calls",
}


def _on() -> bool:
    try:
        return features.is_on(FLAG)
    except Exception:  # noqa: BLE001
        return False


def minute_bucket(now: datetime | None = None) -> int:
    return int((now or datetime.now(UTC)).timestamp() // 60)


def component_of(processor_name: str | None) -> str:
    """Which provider kind a pipeline processor belongs to."""
    name = (processor_name or "").upper()
    if "STT" in name or "TRANSCRI" in name:
        return "stt"
    if "TTS" in name:
        return "tts"
    if "LLM" in name or "REALTIME" in name or "MULTIMODAL" in name:
        return "llm"
    if (
        "TRANSPORT" in name
        or "SERIALIZER" in name
        or "WEBSOCKET" in name
        or "TELEPHONY" in name
    ):
        return "telephony"
    return "other"


async def _incr(
    fields: dict[str, int], *, now: datetime | None = None, client=None
) -> None:
    bucket = store.key("perr", minute_bucket(now))
    async with store.connection(client) as redis:
        pipe = redis.pipeline()
        for field, amount in fields.items():
            pipe.hincrby(bucket, field, amount)
        pipe.expire(bucket, BUCKET_TTL_SECONDS)
        await pipe.execute()


async def _safely(work: Awaitable[Any], what: str) -> None:
    try:
        await work
    except Exception as exc:  # noqa: BLE001 - see module docstring
        logger.warning("ops_alerts: could not record {}: {}", what, type(exc).__name__)


async def pipeline_started(*, now: datetime | None = None, client=None) -> None:
    if not _on():
        return
    await _safely(_incr({"call:n": 1}, now=now, client=client), "a call start")


async def pipeline_error(
    workflow_run_id: int | None,
    processor_name: str | None,
    *,
    now: datetime | None = None,
    client=None,
) -> None:
    """A provider raised an error frame on this run."""
    if not _on():
        return
    component = component_of(processor_name)

    async def work():
        async with store.connection(client) as redis:
            if workflow_run_id is not None:
                first = await redis.set(
                    store.key("perr_seen", workflow_run_id, component),
                    "1",
                    ex=SEEN_TTL_SECONDS,
                    nx=True,
                )
                if not first:
                    return
            await _incr({f"call:{component}:err": 1}, now=now, client=redis)

    await _safely(work(), "a pipeline error")


async def direct_llm(*, ok: bool, now: datetime | None = None, client=None) -> None:
    """A model turn through the builder client, and whether it failed."""
    if not _on():
        return
    await _safely(
        _incr(
            {"llm_direct:n": 1, "llm_direct:err": 0 if ok else 1},
            now=now,
            client=client,
        ),
        "a model turn",
    )


async def dial(*, ok: bool, now: datetime | None = None, client=None) -> None:
    """An outbound dial request to a carrier, and whether it was refused."""
    if not _on():
        return
    await _safely(
        _incr({"dial:n": 1, "dial:err": 0 if ok else 1}, now=now, client=client),
        "a dial",
    )


async def window_counts(
    minutes: int, *, now: datetime | None = None, client=None
) -> dict[str, int]:
    """Every counter summed over the last ``minutes`` buckets, this one included."""
    current = minute_bucket(now)
    totals: dict[str, int] = {}
    async with store.connection(client) as redis:
        pipe = redis.pipeline()
        for bucket in range(current - minutes + 1, current + 1):
            pipe.hgetall(store.key("perr", bucket))
        for row in await pipe.execute():
            for field, value in (row or {}).items():
                name = store.text(field) or ""
                totals[name] = totals.get(name, 0) + int(store.text(value) or 0)
    return totals


# --- scheduled ticks -----------------------------------------------------------


async def mark_tick(name: str, *, now: datetime | None = None, client=None) -> None:
    if not _on():
        return

    async def work():
        async with store.connection(client) as redis:
            await redis.set(
                store.key("tick", name),
                (now or datetime.now(UTC)).isoformat(),
                ex=2 * 86_400,
            )

    await _safely(work(), f"the {name} tick")


async def last_ticks(client=None) -> dict[str, datetime | None]:
    out: dict[str, datetime | None] = {}
    async with store.connection(client) as redis:
        values = await redis.mget([store.key("tick", name) for name in TICKS])
    for name, raw in zip(TICKS, values):
        try:
            out[name] = datetime.fromisoformat(store.text(raw)) if raw else None
        except ValueError:
            out[name] = None
    return out


def stamped(name: str) -> Callable:
    """Decorate a scheduled task so its completion is stamped.

    Only a return stamps: a tick that raises every minute looks, from here,
    exactly like a tick that is not scheduled at all -- which is the point.
    ``functools.wraps`` keeps the name ARQ registers the cron under.
    """

    def decorate(fn: Callable[..., Awaitable[Any]]):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            result = await fn(*args, **kwargs)
            await mark_tick(name)
            return result

        return wrapper

    return decorate

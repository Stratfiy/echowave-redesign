"""Write down every Auto routing decision.

Until this existed a decision lived and died in the one request that made it:
it was stamped on a chat session, logged by Decibyl's thread, and nowhere else,
so "why did Auto pick Opus for this workspace all week" had no answer.

One function, :func:`decision`, is the whole surface. Today it writes a
structured log line and bumps a per-day counter (Redis, the same shape as the
Laya shadow counters in ``services/ops/laya_eval.py``). The metering recorder
(``billing/model_usage.py``) records tokens and vendor units, not choices, so a
decision does not fit it yet; when a table for decisions exists, only this
function changes and no caller does.

Not ``learning_events``: those are what a person taught or corrected (PR
#596). A routing decision is the platform's, not the person's.

The text of the message is never recorded -- only its kind, the model and why.
Recording never costs the reply: it is a log line and a background task, and
a failure of either is a debug line.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from api import constants

COUNTER_KEY_PREFIX = "decibyl:auto_route:"
COUNTER_TTL_SECONDS = 35 * 86400

_PENDING: set[asyncio.Task] = set()


def counter_field(
    kind: str, provider: str, model: str, source: str, reason: str
) -> str:
    """One counter per (kind, model, who decided, why)."""
    return f"{kind}|{provider}/{model}|{source}|{reason}"


def decision(
    *,
    organization_id: int | None,
    feature: str,
    kind: str,
    provider: str,
    model: str,
    source: str,
    reason: str,
    detail: str | None = None,
) -> None:
    """Record one decision. ``source`` is rules, laya or fallback (Laya
    abstained and the rules decided); ``reason`` is why this model, from
    ``routing.models``."""
    logger.info(
        "auto_route org={} feature={} kind={} model={}/{} source={} reason={}{}",
        organization_id,
        feature,
        kind,
        provider,
        model,
        source,
        reason,
        f" detail={detail}" if detail else "",
    )
    try:
        task = asyncio.get_running_loop().create_task(
            _count(counter_field(kind, provider, model, source, reason))
        )
    except RuntimeError:
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def _day() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


async def _count(field: str, *, client: Any = None) -> None:
    own = client is None
    try:
        if own:
            import redis.asyncio as aioredis

            client = aioredis.from_url(constants.REDIS_URL)
        key = f"{COUNTER_KEY_PREFIX}{_day()}"
        pipe = client.pipeline()
        pipe.hincrby(key, field, 1)
        pipe.expire(key, COUNTER_TTL_SECONDS)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Auto routing counter not written: {}", type(exc).__name__)
    finally:
        if own and client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

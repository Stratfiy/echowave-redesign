"""Making a machine allowed to dial a human without it becoming a menace.

The public trigger endpoint already exists and works: an external system POSTs
a phone number and some context, and an agent rings it. That is the right
primitive, and for a person clicking a button it is enough. For a machine it is
not, for two reasons that only appear in production.

**A sender that does not hear back retries.** Every webhook sender ever
written does. A timeout on our side, a dropped response, a 502 from a proxy --
the event is delivered again, and the second delivery is indistinguishable from
a second alarm. The customer's phone rings twice about one pump. It is not a
small annoyance: it is the behaviour that gets an integration switched off, and
the person switching it off is the one we most wanted to keep.

So an event may carry an ``event_id``. The first delivery dials; every
redelivery inside the window returns the same run and dials nothing. The sender
gets a success either way, which is what stops it retrying again.

**A sender that is stuck does not stop.** A threshold that oscillates, a
firmware loop, a sensor left in a test rig -- these fire every few seconds, for
days, and nothing in the request looks wrong. At the platform's default rate
limit that is six hundred calls a minute. The customer would be woken all
night, and because this platform holds the provider keys, we would pay for
every one of them.

So a workflow may only be triggered so many times in an hour. The cap is not a
rate limit on the API -- it is a statement that no honest alarm needs to ring
a human eleven times before breakfast.

Both live in Redis rather than Postgres: they are short-lived counters read on
a hot path, and losing them to a flush costs one duplicated call rather than
anything durable. Neither ever raises into the route. A Redis that is down must
not stop a genuine alarm reaching a person -- it means we dial, which is the
failure the customer would choose.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

#: One key per (workflow, event). Holds the run id the first delivery created.
DEDUPE_PREFIX = "trigger:event:"

#: One key per workflow, counting triggers in the current hour.
RATE_PREFIX = "trigger:rate:"

#: How long a delivered event is remembered. A day, because a sender that
#: retries at all usually retries with backoff over hours, and because an
#: operator re-firing the same alarm id tomorrow means a new alarm.
DEDUPE_TTL_SECONDS = 24 * 60 * 60

#: Triggers per workflow per hour before the platform stops dialling. Twenty
#: is far above any real escalation pattern and far below a loop: a pump that
#: genuinely needs attention twenty times in an hour needs an engineer, not
#: another phone call.
DEFAULT_MAX_TRIGGERS_PER_HOUR = 20

RATE_WINDOW_SECONDS = 60 * 60


class TriggerRateLimited(Exception):
    """The workflow has been triggered too often this hour."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f"This agent has already been triggered {limit} times in the last "
            "hour and will not place another call. Check whether the system "
            "sending these events is stuck."
        )


async def _client() -> aioredis.Redis | None:
    try:
        return await aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as exception:  # noqa: BLE001
        logger.warning(f"Trigger safety unavailable, dialling anyway: {exception}")
        return None


async def already_delivered(workflow_uuid: str, event_id: str | None) -> int | None:
    """The run this event already created, if it has been delivered before.

    ``None`` means dial: either the sender did not supply an ``event_id``, or
    this is the first time we have seen it, or Redis could not tell us. The
    last of those is deliberate -- an alarm that cannot be de-duplicated should
    still reach a human.
    """
    if not event_id:
        return None
    client = await _client()
    if client is None:
        return None
    try:
        stored = await client.get(f"{DEDUPE_PREFIX}{workflow_uuid}:{event_id}")
    except Exception as exception:  # noqa: BLE001
        logger.warning(f"Could not check for a duplicate event: {exception}")
        return None
    if stored is None:
        return None
    try:
        return int(stored)
    except (TypeError, ValueError):
        return None


async def remember_delivery(
    workflow_uuid: str, event_id: str | None, workflow_run_id: int
) -> None:
    """Record which run an event produced, so a redelivery returns it."""
    if not event_id:
        return
    client = await _client()
    if client is None:
        return
    try:
        await client.set(
            f"{DEDUPE_PREFIX}{workflow_uuid}:{event_id}",
            str(workflow_run_id),
            ex=DEDUPE_TTL_SECONDS,
        )
    except Exception as exception:  # noqa: BLE001
        # The call has already been placed. Failing here would turn a
        # bookkeeping problem into a second call.
        logger.warning(f"Could not record the delivered event: {exception}")


async def count_trigger(
    workflow_uuid: str, limit: int = DEFAULT_MAX_TRIGGERS_PER_HOUR
) -> None:
    """Count this trigger, and refuse it if the workflow is over its hour.

    Raises :class:`TriggerRateLimited`. Counts before dialling rather than
    after, so a burst arriving together cannot all pass the check and then all
    dial.
    """
    client = await _client()
    if client is None:
        return
    key = f"{RATE_PREFIX}{workflow_uuid}"
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # Only the first increment sets the window, so the hour runs from
            # the first trigger rather than sliding forward with each one --
            # otherwise a sender at the limit would hold the key alive forever
            # and never recover.
            pipe.expire(key, RATE_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
    except Exception as exception:  # noqa: BLE001
        logger.warning(f"Could not count this trigger, allowing it: {exception}")
        return
    if int(count) > limit:
        logger.warning(
            f"Workflow {workflow_uuid} triggered {count} times this hour; "
            f"refusing at a limit of {limit}."
        )
        raise TriggerRateLimited(limit)

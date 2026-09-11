"""A machine is allowed to ring a human. Twice is a bug; all night is a menace.

The public trigger endpoint already worked. What it lacked was everything that
only matters once the caller is a program rather than a person:

* Every webhook sender ever written retries on a timeout, and without an event
  id the redelivery is a second alarm about the same pump.
* A threshold that oscillates or a sensor left in a test rig fires for days,
  and nothing in any single request looks wrong.

Both failure modes are the normal case in production, not the pathological
one, which is why the tests here are mostly about what must NOT happen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import triggered_calls
from api.services.workflow.triggered_calls import (
    DEFAULT_MAX_TRIGGERS_PER_HOUR,
    TriggerRateLimited,
    already_delivered,
    count_trigger,
    remember_delivery,
)


class _Redis:
    """Enough Redis to exercise the decisions."""

    def __init__(self, store=None, fail=False):
        self.store = store or {}
        self.fail = fail
        self.expiries = {}

    async def get(self, key):
        if self.fail:
            raise OSError("redis is down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise OSError("redis is down")
        self.store[key] = value

    def pipeline(self, transaction=True):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.count = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def incr(self, key):
        self.key = key

    def expire(self, key, seconds, nx=False):
        self.redis.expiries[key] = seconds

    async def execute(self):
        if self.redis.fail:
            raise OSError("redis is down")
        current = int(self.redis.store.get(self.key, 0)) + 1
        self.redis.store[self.key] = str(current)
        return [current, True]


def _with(redis):
    return patch.object(triggered_calls, "_client", AsyncMock(return_value=redis))


class TestARedeliveryDoesNotRingTwice:
    async def test_the_first_delivery_dials(self):
        with _with(_Redis()):
            assert await already_delivered("wf", "alarm-1") is None

    async def test_a_redelivery_returns_the_first_run(self):
        redis = _Redis()
        with _with(redis):
            await remember_delivery("wf", "alarm-1", 4242)
            assert await already_delivered("wf", "alarm-1") == 4242

    async def test_a_different_event_still_dials(self):
        redis = _Redis()
        with _with(redis):
            await remember_delivery("wf", "alarm-1", 4242)
            assert await already_delivered("wf", "alarm-2") is None

    async def test_the_same_event_on_another_agent_still_dials(self):
        """Two agents watching the same plant share event ids."""
        redis = _Redis()
        with _with(redis):
            await remember_delivery("wf-a", "alarm-1", 1)
            assert await already_delivered("wf-b", "alarm-1") is None

    async def test_a_sender_that_supplies_no_id_is_never_deduplicated(self):
        """Opt-in: a caller who did not ask for it must not have calls
        silently swallowed."""
        redis = _Redis()
        with _with(redis):
            await remember_delivery("wf", None, 7)
            assert await already_delivered("wf", None) is None
            assert redis.store == {}


class TestAStuckSenderIsStopped:
    async def test_it_allows_calls_up_to_the_limit(self):
        redis = _Redis()
        with _with(redis):
            for _ in range(DEFAULT_MAX_TRIGGERS_PER_HOUR):
                await count_trigger("wf")

    async def test_it_refuses_the_one_after(self):
        redis = _Redis()
        with _with(redis):
            for _ in range(DEFAULT_MAX_TRIGGERS_PER_HOUR):
                await count_trigger("wf")
            with pytest.raises(TriggerRateLimited):
                await count_trigger("wf")

    async def test_the_refusal_says_what_to_check(self):
        """An operator reading this must know to look at their sender."""
        redis = _Redis()
        with _with(redis):
            for _ in range(DEFAULT_MAX_TRIGGERS_PER_HOUR):
                await count_trigger("wf")
            with pytest.raises(TriggerRateLimited) as caught:
                await count_trigger("wf")
        assert "stuck" in str(caught.value)

    async def test_one_busy_agent_does_not_silence_another(self):
        redis = _Redis()
        with _with(redis):
            for _ in range(DEFAULT_MAX_TRIGGERS_PER_HOUR + 1):
                try:
                    await count_trigger("noisy")
                except TriggerRateLimited:
                    pass
            await count_trigger("quiet")

    async def test_the_window_is_set_once_not_extended(self):
        """Otherwise a sender at the limit holds the key alive forever and the
        agent never recovers."""
        redis = _Redis()
        with _with(redis):
            for _ in range(3):
                await count_trigger("wf")
        assert redis.expiries["trigger:rate:wf"] == triggered_calls.RATE_WINDOW_SECONDS


class TestRedisBeingDownNeverSilencesAnAlarm:
    """The failure the customer would choose: dial, and risk a duplicate."""

    async def test_a_dedupe_check_that_fails_dials(self):
        with _with(_Redis(fail=True)):
            assert await already_delivered("wf", "alarm-1") is None

    async def test_no_client_at_all_dials(self):
        with patch.object(triggered_calls, "_client", AsyncMock(return_value=None)):
            assert await already_delivered("wf", "alarm-1") is None
            await count_trigger("wf")

    async def test_a_counter_that_fails_does_not_refuse_the_call(self):
        with _with(_Redis(fail=True)):
            await count_trigger("wf")

    async def test_failing_to_record_never_raises_after_the_call_was_placed(self):
        with _with(_Redis(fail=True)):
            await remember_delivery("wf", "alarm-1", 4242)

    async def test_a_corrupt_stored_value_dials_rather_than_crashing(self):
        redis = _Redis({"trigger:event:wf:alarm-1": "not-a-number"})
        with _with(redis):
            assert await already_delivered("wf", "alarm-1") is None


class TestTheEndpointAcceptsIt:
    def test_the_request_carries_an_optional_event_id(self):
        from api.routes.public_agent import TriggerCallRequest

        field = TriggerCallRequest.model_fields["event_id"]
        assert field.default is None, "a caller who omits it must still work"

    def test_a_call_can_still_be_placed_without_one(self):
        from api.routes.public_agent import TriggerCallRequest

        assert TriggerCallRequest(phone_number="+919840012345").event_id is None

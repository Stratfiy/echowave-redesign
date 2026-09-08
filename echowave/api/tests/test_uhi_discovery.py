"""Holding a caller while a broadcast answers.

UHI discovery has no response to the request that starts it: the gateway fans
the search out and providers call *us* back, over seconds, on whatever worker
the load balancer picked. Every test here is one way that goes wrong with a
person on the line:

* the wait outlasting what a caller will sit through,
* a reply landing before anybody is waiting for it,
* the callback arriving on a different process from the call,
* one provider's malformed answer losing everybody else's,
* Redis being down turning "found nothing" into a dropped call.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from api.services.integrations.uhi.discovery import (
    MAX_DEADLINE_SECONDS,
    DiscoveryCollector,
)


class _FakeRedis:
    """Enough Redis to test the collector, including the pipeline it uses."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.expiries: dict[str, int] = {}
        self.fail_on_read = False

    async def lrange(self, key, start, end):
        if self.fail_on_read:
            raise ConnectionError("redis went away")
        return list(self.lists.get(key, []))

    async def delete(self, key):
        self.lists.pop(key, None)

    async def aclose(self):
        pass

    def pipeline(self, transaction=True):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def rpush(self, key, value):
                self.ops.append(("rpush", key, value))

            def expire(self, key, ttl):
                self.ops.append(("expire", key, ttl))

            async def execute(self):
                for op in self.ops:
                    if op[0] == "rpush":
                        outer.lists.setdefault(op[1], []).append(op[2])
                    else:
                        outer.expiries[op[1]] = op[2]
                self.ops = []

        return _Pipe()


def _collector(fake: _FakeRedis) -> DiscoveryCollector:
    c = DiscoveryCollector()
    c._redis = fake  # noqa: SLF001 - the seam this test exists to use
    return c


def _reply(name: str) -> dict:
    return {"message": {"catalog": {"descriptor": {"name": name}}}}


@pytest.mark.asyncio
class TestTheDeadlineBoundsTheSilence:
    async def test_it_returns_what_arrived_rather_than_waiting_for_everyone(self):
        """A provider that never answers is normal. Waiting for it is how the
        caller is lost."""
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m1", _reply("Apollo"))

        result = await c.collect("m1", deadline_seconds=0.3)

        assert len(result.replies) == 1
        assert result.reason == "deadline"

    async def test_it_does_not_overshoot_the_deadline(self):
        """Overshooting is the silence this class exists to bound, and the
        poll interval is the easy way to do it by accident."""
        c = _collector(_FakeRedis())

        result = await c.collect("m2", deadline_seconds=0.3, poll_interval=5.0)

        assert result.waited_seconds < 1.0

    async def test_a_configured_deadline_cannot_hold_the_line_forever(self):
        """A misconfigured 30-second wait is a dropped call, not a slow one."""
        c = _collector(_FakeRedis())
        # Not actually waited out: the cap is asserted on the value, because
        # waiting ten seconds in a test is its own bad idea.
        assert MAX_DEADLINE_SECONDS <= 10.0

        result = await c.collect("m3", deadline_seconds=0.1)
        assert result.waited_seconds < MAX_DEADLINE_SECONDS

    async def test_finding_nothing_is_a_result_not_an_error(self):
        c = _collector(_FakeRedis())
        result = await c.collect("m4", deadline_seconds=0.2)
        assert result.replies == []
        assert result.found_any is False


@pytest.mark.asyncio
class TestAnsweringEarly:
    async def test_enough_replies_stop_the_wait(self):
        """Two or three answer quickly and the rest never do. Sitting out the
        full deadline for the ones that will not come is silence for nothing."""
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m5", _reply("Apollo"))
        await c.record_reply("m5", _reply("Fortis"))

        result = await c.collect("m5", deadline_seconds=5.0, min_results=2)

        assert result.reason == "enough"
        assert result.waited_seconds < 1.0

    async def test_min_results_of_zero_waits_the_whole_deadline(self):
        """Which is right when the agent means to compare everything."""
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m6", _reply("Apollo"))

        result = await c.collect("m6", deadline_seconds=0.3, min_results=0)

        assert result.reason == "deadline"


@pytest.mark.asyncio
class TestTheRaceThatMatters:
    async def test_a_reply_that_lands_before_anybody_waits_is_not_lost(self):
        """The first provider can answer before the caller has finished the
        sentence that triggered the search. A pub/sub subscriber that does not
        exist yet misses it; a list does not."""
        fake = _FakeRedis()
        c = _collector(fake)

        await c.record_reply("m7", _reply("Early"))
        result = await c.collect("m7", deadline_seconds=0.2)

        assert [
            r["message"]["catalog"]["descriptor"]["name"] for r in result.replies
        ] == ["Early"]

    async def test_a_reply_arriving_mid_wait_is_picked_up(self):
        fake = _FakeRedis()
        c = _collector(fake)

        async def late():
            await asyncio.sleep(0.1)
            await c.record_reply("m8", _reply("Late"))

        task = asyncio.create_task(late())
        result = await c.collect("m8", deadline_seconds=1.0, min_results=1)
        await task

        assert result.reason == "enough"
        assert result.replies[0]["message"]["catalog"]["descriptor"]["name"] == "Late"

    async def test_a_callback_on_another_worker_still_reaches_the_waiter(self):
        """The web worker taking the callback is almost never the one holding
        the call. Two collector instances, one Redis — the version of this
        that used a dict would pass on one worker and drop every reply in
        production."""
        shared = _FakeRedis()
        holding_the_call = _collector(shared)
        took_the_callback = _collector(shared)

        await took_the_callback.record_reply("m9", _reply("OtherWorker"))
        result = await holding_the_call.collect("m9", deadline_seconds=0.2)

        assert len(result.replies) == 1


@pytest.mark.asyncio
class TestItDegradesRatherThanBreaks:
    async def test_one_malformed_reply_does_not_lose_the_others(self):
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m10", _reply("Good"))
        fake.lists["uhi:discovery:m10"].append("{not json")
        fake.lists["uhi:discovery:m10"].append(json.dumps(["a list, not an object"]))

        result = await c.collect("m10", deadline_seconds=0.2)

        assert len(result.replies) == 1

    async def test_redis_failing_mid_wait_ends_the_wait(self):
        """Rather than looping until the deadline against a dead connection
        while the caller listens to nothing."""
        fake = _FakeRedis()
        c = _collector(fake)
        fake.fail_on_read = True

        result = await c.collect("m11", deadline_seconds=5.0)

        assert result.reason == "unavailable"
        assert result.waited_seconds < 1.0

    async def test_a_failed_write_does_not_raise_into_the_callback_route(self):
        """A provider that gets a 500 retries into a search whose caller hung
        up two minutes ago. One lost reply is a shorter list."""

        class _Broken(_FakeRedis):
            def pipeline(self, transaction=True):
                raise ConnectionError("gone")

        c = _collector(_Broken())
        await c.record_reply("m12", _reply("Anything"))  # must not raise


@pytest.mark.asyncio
class TestHousekeeping:
    async def test_every_push_refreshes_the_expiry(self):
        """A search still receiving replies must not expire underneath the
        waiter."""
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m13", _reply("A"))
        await c.record_reply("m13", _reply("B"))
        assert fake.expiries["uhi:discovery:m13"] > 0

    async def test_a_finished_search_can_be_dropped(self):
        fake = _FakeRedis()
        c = _collector(fake)
        await c.record_reply("m14", _reply("A"))
        await c.discard("m14")
        assert "uhi:discovery:m14" not in fake.lists

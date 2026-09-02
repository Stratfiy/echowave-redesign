"""The guards on missed-call callback.

This is the one path in the platform where an inbound event causes an outbound
call, so it is the one path that can build a loop. Every test here exists
because of a specific way the customer ends up paying for calls nobody wanted:
redial impatience, a caller that never stops, and two of our own agents finding
each other.

The scoring of these tests is deliberately behavioural — they drive `authorise`
and assert on what it permits — rather than asserting that particular Redis
commands were issued. A guard that is correct about Redis and wrong about
callbacks is not a guard.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from api.services.telephony import missed_call


class FakeRedis:
    """Enough Redis to test the guard, with the atomicity that matters.

    `set(nx=True)` and `incr` are the two operations the guard's correctness
    rests on, and both are atomic in real Redis. They are atomic here too —
    single-threaded and non-awaiting inside the critical section — which is
    what lets the concurrency test below mean something.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key, seconds):
        self.expiries[key] = seconds
        return True

    def drop(self, key_fragment: str) -> None:
        """Simulate a TTL elapsing, without sleeping through it."""
        for key in [k for k in self.store if key_fragment in k]:
            del self.store[key]


@pytest.fixture
def guard():
    g = missed_call._Guard()
    g._redis = FakeRedis()
    return g


@dataclass
class Row:
    address_normalized: str | None = None
    callback_workflow_id: int | None = None


class TestCallbackMode:
    def test_a_number_with_a_callback_workflow_is_in_callback_mode(self):
        assert missed_call.is_callback_number(Row(callback_workflow_id=7))

    def test_a_number_without_one_is_not(self):
        assert not missed_call.is_callback_number(Row(callback_workflow_id=None))

    def test_a_row_that_predates_the_column_is_not(self):
        """A phone-number row loaded from somewhere that does not carry the
        column at all must read as 'not callback mode', not explode. The
        alternative is an AttributeError inside an inbound webhook, which the
        carrier sees as a 500 and retries."""

        class Older:
            address_normalized = "919876543210"

        assert not missed_call.is_callback_number(Older())


class TestLoopGuard:
    @pytest.mark.asyncio
    async def test_we_never_call_our_own_number_back(self, guard):
        """Two agents talking to each other, billed to the customer, until
        somebody notices. Pointing a test agent at the callback number is the
        obvious way to try the feature, so this is the likely accident, not the
        exotic one."""
        with pytest.raises(missed_call.CallbackLoop):
            await missed_call.authorise(
                organization_id=1,
                caller="919876543210",
                our_numbers={"919876543210", "918888888888"},
                guard=guard,
            )

    @pytest.mark.asyncio
    async def test_the_loop_guard_does_not_block_a_real_caller(self, guard):
        await missed_call.authorise(
            organization_id=1,
            caller="919999999999",
            our_numbers={"919876543210"},
            guard=guard,
        )

    @pytest.mark.asyncio
    async def test_the_loop_check_runs_before_the_reservation(self, guard):
        """A refused loop must not burn the caller's cooldown or daily
        allowance. If it did, the guard would be reporting a caller as
        rate-limited when the real reason was a misconfiguration, and the log
        would send whoever debugs it in the wrong direction."""
        for _ in range(3):
            with pytest.raises(missed_call.CallbackLoop):
                await missed_call.authorise(
                    organization_id=1,
                    caller="919876543210",
                    our_numbers={"919876543210"},
                    guard=guard,
                )
        assert guard._redis.store == {}

    def test_loop_guard_numbers_uses_the_column_inbound_matches_on(self):
        """Built from address_normalized because that is what the inbound
        lookup compares. A set built from the display column would miss a
        number stored as +91 98765 43210 and compared as 919876543210 — a hole
        in precisely the guard whose absence is hardest to notice."""
        numbers = missed_call.loop_guard_numbers(
            [Row(address_normalized="919876543210"), Row(address_normalized=None)]
        )
        assert numbers == {"919876543210"}


class TestCooldown:
    @pytest.mark.asyncio
    async def test_a_second_call_inside_the_cooldown_is_refused(self, guard):
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        with pytest.raises(missed_call.CallbackTooSoon):
            await missed_call.authorise(
                organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
            )

    @pytest.mark.asyncio
    async def test_the_cooldown_is_per_caller(self, guard):
        """One impatient caller must not stop everyone else's callbacks. This
        is the difference between traffic shaping and an outage."""
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        await missed_call.authorise(
            organization_id=1, caller="918888888888", our_numbers=set(), guard=guard
        )

    @pytest.mark.asyncio
    async def test_the_cooldown_is_per_organization(self, guard):
        """Two accounts can print two different numbers on two different
        hoardings, and the same person may ring both. Sharing a cooldown across
        tenants would let one account's traffic suppress another's."""
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        await missed_call.authorise(
            organization_id=2, caller="919999999999", our_numbers=set(), guard=guard
        )

    @pytest.mark.asyncio
    async def test_the_caller_is_served_again_once_the_cooldown_expires(self, guard):
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        guard._redis.drop("cooldown")
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )

    @pytest.mark.asyncio
    async def test_the_cooldown_key_carries_a_ttl(self, guard):
        """Without an expiry the first callback is also the last one, forever.
        A permanent lock looks exactly like a working cooldown for the first
        five minutes of testing."""
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        cooldown_keys = [k for k in guard._redis.expiries if "cooldown" in k]
        assert cooldown_keys
        assert (
            guard._redis.expiries[cooldown_keys[0]]
            == missed_call.CALLBACK_COOLDOWN_SECONDS
        )


class TestConcurrentRedials:
    @pytest.mark.asyncio
    async def test_three_simultaneous_webhooks_produce_one_callback(self, guard):
        """The reason `reserve` sets the cooldown with SET NX instead of
        reading it and then writing it.

        Tapping redial three times in two seconds produces three inbound
        webhooks that arrive together. A check-then-set lets all three observe
        an empty cooldown before any of them writes it, and places three calls
        to a person who wanted one. Only the reservation closes that window.
        """
        results = await asyncio.gather(
            *[
                missed_call.authorise(
                    organization_id=1,
                    caller="919999999999",
                    our_numbers=set(),
                    guard=guard,
                )
                for _ in range(3)
            ],
            return_exceptions=True,
        )
        allowed = [r for r in results if r is None]
        refused = [r for r in results if isinstance(r, missed_call.CallbackTooSoon)]
        assert len(allowed) == 1
        assert len(refused) == 2


class TestDailyCap:
    @pytest.mark.asyncio
    async def test_a_caller_is_capped_for_the_day(self, guard):
        """The cooldown handles impatience. This handles the number that rings
        all day — a person with nothing better to do, or an autodialler on the
        other side that has our number in a list."""
        for _ in range(missed_call.CALLBACK_DAILY_CAP):
            await missed_call.authorise(
                organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
            )
            guard._redis.drop("cooldown")

        with pytest.raises(missed_call.CallbackCapReached):
            await missed_call.authorise(
                organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
            )

    @pytest.mark.asyncio
    async def test_the_cap_survives_the_cooldown_expiring(self, guard):
        """The two limits are independent. Waiting out the cooldown must not
        also clear the day's count, or the cap is decorative — anyone willing
        to wait five minutes between calls would never hit it."""
        for _ in range(missed_call.CALLBACK_DAILY_CAP):
            await missed_call.authorise(
                organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
            )
            guard._redis.drop("cooldown")

        guard._redis.drop("cooldown")
        with pytest.raises(missed_call.CallbackCapReached):
            await missed_call.authorise(
                organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
            )

    @pytest.mark.asyncio
    async def test_the_day_window_slides_from_the_first_call_not_the_last(
        self, guard
    ):
        """The expiry is set only on the first increment.

        Refreshing the TTL on every call would mean a caller who rings steadily
        never lets the window close, so the cap would hold them at the limit
        permanently instead of releasing them a day after they started. The
        cap is meant to bound a burst, not to ban a person.
        """
        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        guard._redis.drop("cooldown")
        guard._redis.expiries.clear()

        await missed_call.authorise(
            organization_id=1, caller="919999999999", our_numbers=set(), guard=guard
        )
        assert not [k for k in guard._redis.expiries if "daily" in k]


class TestRefusalLogging:
    def test_a_loop_is_louder_than_a_cooldown(self):
        """A cooldown is ordinary traffic shaping. A loop keeps costing money
        until somebody changes a setting, so it must not be filed at the same
        level as routine noise."""
        from loguru import logger

        seen = []
        sink = logger.add(lambda m: seen.append(m.record["level"].name), level="INFO")
        try:
            missed_call.log_refusal("91999", missed_call.CallbackTooSoon("soon"))
            missed_call.log_refusal("91999", missed_call.CallbackLoop("loop"))
        finally:
            logger.remove(sink)

        assert seen == ["INFO", "WARNING"]


class TestRefusalsAreOutcomes:
    def test_every_refusal_is_catchable_as_one_type(self):
        """The inbound webhook swallows these — the caller has already hung up,
        so there is nobody to tell and a 500 only makes the carrier retry a
        webhook whose job was to decline. That only works if one except clause
        covers all of them."""
        for exc in (
            missed_call.CallbackTooSoon,
            missed_call.CallbackCapReached,
            missed_call.CallbackLoop,
        ):
            assert issubclass(exc, missed_call.CallbackRefused)

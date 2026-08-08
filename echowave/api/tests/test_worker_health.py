"""Whether the background worker is alive.

The failure this exists to catch is the quiet one: uvicorn and the ARQ worker
share a container, so when the worker dies every endpoint keeps answering 200
while calls stop being costed and invoices stop being issued. No error is
raised anywhere.

Two properties carry the weight:

* **Absence of work is not the signal.** A night with no calls produces no
  costing either, so the worker has to say it is alive rather than have it
  inferred.
* **"Never started" and "died an hour ago" are different findings.** They need
  different responses — one is a deployment that was never wired up, the other
  is an incident with a timestamp — so the check is tri-state and never
  collapses them into a boolean.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from api.services import worker_health as wh


class _FakeRedis:
    """Enough of redis.asyncio for the heartbeat: one key, no expiry logic."""

    def __init__(self, value=None, *, fail=False):
        self.value = value
        self.fail = fail
        self.set_calls: list[tuple] = []
        self.closed = False

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis is down")
        self.set_calls.append((key, value, ex))
        self.value = value

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis is down")
        return self.value

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
class TestRecordingAHeartbeat:
    async def test_it_writes_the_current_time_with_an_expiry(self):
        fake = _FakeRedis()
        now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            await wh.record_heartbeat(now=now)

        key, value, expiry = fake.set_calls[0]
        assert key == wh.HEARTBEAT_KEY
        assert value == now.isoformat()
        assert expiry == wh.HEARTBEAT_TTL_SECONDS

    async def test_the_expiry_outlives_the_staleness_threshold_by_far(self):
        """An expiring key would answer 'the worker is gone' and destroy the
        more useful answer — when it stopped."""
        assert wh.HEARTBEAT_TTL_SECONDS > wh.WORKER_HEARTBEAT_STALE_AFTER_SECONDS * 10

    async def test_a_redis_failure_does_not_take_the_worker_down(self):
        """A liveness probe that can kill the process would cause the outage it
        is there to report."""
        fake = _FakeRedis(fail=True)
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            await wh.record_heartbeat()  # must not raise

    async def test_the_connection_is_released(self):
        fake = _FakeRedis()
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            await wh.record_heartbeat()
        assert fake.closed


@pytest.mark.asyncio
class TestReadingWorkerHealth:
    async def _health(self, stored, *, age_seconds=0, fail=False):
        now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        value = None
        if stored is not None:
            value = (now - timedelta(seconds=age_seconds)).isoformat()
        fake = _FakeRedis(value if stored is not None else None, fail=fail)
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            return await wh.worker_health(now=now)

    async def test_a_recent_heartbeat_is_alive(self):
        health = await self._health(True, age_seconds=30)
        assert health["alive"] is True

    async def test_a_beat_just_inside_the_window_is_still_alive(self):
        health = await self._health(
            True, age_seconds=wh.WORKER_HEARTBEAT_STALE_AFTER_SECONDS
        )
        assert health["alive"] is True

    async def test_a_beat_past_the_window_is_dead(self):
        health = await self._health(
            True, age_seconds=wh.WORKER_HEARTBEAT_STALE_AFTER_SECONDS + 1
        )
        assert health["alive"] is False

    async def test_a_dead_worker_reports_when_it_was_last_seen(self):
        """The first question anyone debugging asks, and what lines the failure
        up against a deploy or an OOM kill."""
        health = await self._health(True, age_seconds=3600)
        assert health["last_seen"] == "2026-03-10T11:00:00+00:00"
        assert health["age_seconds"] == 3600.0

    async def test_a_dead_worker_says_what_has_silently_stopped(self):
        health = await self._health(True, age_seconds=3600)
        detail = health["detail"].lower()
        assert "costed" in detail
        assert "api keeps answering" in detail

    async def test_no_heartbeat_on_record_is_unknown_not_dead(self):
        """A deployment that never started the worker and one whose worker died
        need different responses."""
        health = await self._health(None)
        assert health["alive"] is None
        assert health["last_seen"] is None

    async def test_an_unreachable_redis_is_unknown_not_dead(self):
        """We cannot see the worker. That is not the same as the worker being
        gone, and reporting it as dead would send someone restarting a healthy
        container."""
        health = await self._health(True, fail=True)
        assert health["alive"] is None
        assert "redis" in health["detail"].lower()

    async def test_a_corrupt_heartbeat_value_is_unknown(self):
        fake = _FakeRedis(b"not-a-timestamp")
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            health = await wh.worker_health()
        assert health["alive"] is None

    async def test_clock_skew_does_not_produce_a_negative_age(self):
        """A worker slightly ahead of the API would otherwise render as
        'last seen in 3 seconds'."""
        health = await self._health(True, age_seconds=-3)
        assert health["age_seconds"] == 0.0
        assert health["alive"] is True

    async def test_a_naive_timestamp_is_read_as_utc(self):
        """Defensive: every writer stores an aware timestamp, but a naive one
        would otherwise raise inside the subtraction and report unknown on a
        worker that is plainly alive."""
        now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        fake = _FakeRedis(datetime(2026, 3, 10, 11, 59, 30).isoformat())
        with patch.object(wh.aioredis, "from_url", return_value=fake):
            health = await wh.worker_health(now=now)
        assert health["alive"] is True
        assert health["age_seconds"] == 30.0


@pytest.mark.asyncio
class TestTheWorkerIsPartOfBillingReadiness:
    """A dead worker is what makes the billing numbers stale, so the billing
    assessment has to see it — otherwise a deployment whose worker stopped
    yesterday reports billing as ready."""

    async def test_a_dead_worker_blocks_billing_readiness(self, async_session):
        from api.services.billing import readiness as billing_readiness

        with patch(
            "api.services.worker_health.worker_health",
            AsyncMock(
                return_value={"alive": False, "detail": "No heartbeat for 3600s."}
            ),
        ):
            assessment = await billing_readiness.assess(async_session)

        checks = {c.key: c for c in assessment.checks}
        assert checks["background_worker_alive"].status == "action_required"
        assert checks["background_worker_alive"] in assessment.blocking

    async def test_a_live_worker_does_not_block(self, async_session):
        from api.services.billing import readiness as billing_readiness

        with patch(
            "api.services.worker_health.worker_health",
            AsyncMock(return_value={"alive": True, "detail": "Running."}),
        ):
            assessment = await billing_readiness.assess(async_session)

        checks = {c.key: c for c in assessment.checks}
        assert checks["background_worker_alive"].status == "ready"

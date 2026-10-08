"""Stream ops, handoff 35: infrastructure health, where silence is never green.

What these defend: the overall status is ``ok`` only when every core signal
was measured ok; an unknown, unconfigured or missing core signal makes it
``unknown``; a hung or failing probe becomes an ``unknown`` row rather than
an exception or a pass; pool and queue thresholds; stale evidence reads as
overdue; and the HTTP route is a 404 while ``ops_console`` is off and
refuses a non-staff caller.
"""

from __future__ import annotations

import asyncio

import pytest

from api import constants
from api.db.models import UserModel
from api.enums import StaffRole
from api.services import features
from api.services.ops import infra_health
from api.services.ops.infra_health import (
    DEGRADED,
    DOWN,
    NOT_CONFIGURED,
    OK,
    UNKNOWN,
    Signal,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", False)
    yield
    features.clear_snapshot()


def _signals(**states):
    return {name: Signal(name, state, "") for name, state in states.items()}


def test_ok_only_when_every_core_signal_is_ok():
    core = {name: OK for name in infra_health.CORE_SIGNALS}
    assert infra_health.overall(_signals(**core)) == OK
    assert infra_health.overall(_signals(**{**core, "worker": UNKNOWN})) == UNKNOWN
    assert (
        infra_health.overall(_signals(**{**core, "redis": NOT_CONFIGURED})) == UNKNOWN
    )
    assert infra_health.overall(_signals(**{**core, "queue": DEGRADED})) == DEGRADED
    assert (
        infra_health.overall(_signals(**{**core, "database": DOWN, "queue": DEGRADED}))
        == DOWN
    )


def test_a_missing_core_signal_is_unknown_not_ok():
    core = {name: OK for name in infra_health.CORE_SIGNALS if name != "worker"}
    assert infra_health.overall(_signals(**core)) == UNKNOWN


class _Pool:
    def __init__(self, size, out, overflow=0):
        self._size, self._out, self._overflow = size, out, overflow

    def size(self):
        return self._size

    def checkedout(self):
        return self._out

    def overflow(self):
        return self._overflow


def test_pool_thresholds(monkeypatch):
    monkeypatch.setattr(constants, "DB_POOL_MAX_OVERFLOW", 10)
    assert infra_health.pool_signal(_Pool(10, 2)).status == OK
    assert infra_health.pool_signal(_Pool(10, 17)).status == DEGRADED
    assert infra_health.pool_signal(_Pool(10, 20, 10)).status == DOWN
    assert infra_health.pool_signal(object()).status == UNKNOWN


def test_queue_thresholds():
    assert infra_health.queue_signal(3, 2.0).status == OK
    assert infra_health.queue_signal(3, None).status == OK
    assert infra_health.queue_signal(3, 900.0).status == DEGRADED
    assert infra_health.queue_signal(10_000, 1.0).status == DEGRADED


def test_evidence_age():
    assert infra_health.classify_age(None, max_age=10, what="x")[0] == UNKNOWN
    assert infra_health.classify_age(5, max_age=10, what="x")[0] == OK
    assert infra_health.classify_age(50, max_age=10, what="x")[0] == DEGRADED


@pytest.mark.asyncio
async def test_a_hung_or_failing_probe_is_unknown(monkeypatch):
    monkeypatch.setattr(infra_health, "PROBE_TIMEOUT_SECONDS", 0.01)

    async def hang():
        await asyncio.sleep(1)

    async def boom():
        raise RuntimeError("postgres://user:pw@host")

    hung = await infra_health._timed("worker", hang)
    failed = await infra_health._timed("redis", boom)
    assert hung.status == UNKNOWN
    assert failed.status == UNKNOWN
    assert "pw@host" not in failed.detail


@pytest.mark.asyncio
async def test_snapshot_with_everything_measured_ok(monkeypatch):
    async def ok(name):
        return Signal(name, OK, "fine")

    probes = tuple((name, (lambda n=name: ok(n))) for name, _ in infra_health.PROBES)
    monkeypatch.setattr(infra_health, "PROBES", probes)
    snap = await infra_health.snapshot()
    assert snap["status"] == OK
    assert {s["name"] for s in snap["signals"]} == {name for name, _ in probes}
    assert all(s["observed_at"] for s in snap["signals"])


@pytest.mark.asyncio
async def test_monitoring_unconfigured_is_said_out_loud(monkeypatch):
    monkeypatch.setattr(constants, "POSTHOG_API_KEY", None)
    monkeypatch.setattr("api.observability.sentry.enabled", lambda: False)
    signal = await infra_health._monitoring()
    assert signal.status == NOT_CONFIGURED
    assert "sentry" in signal.detail and "posthog" in signal.detail


async def _user(session, slug, role):
    user = UserModel(provider_id=f"ops-health-{slug}", staff_role=role)
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.mark.asyncio
async def test_route_is_hidden_while_off_and_staff_only(
    db_session, async_session, as_user, monkeypatch
):
    staff = await _user(async_session, "support", StaffRole.SUPPORT.value)
    plain = await _user(async_session, "plain", None)
    async with as_user(staff) as client:
        assert (await client.get("/api/v1/admin/ops/health")).status_code == 404
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", True)

    async def fake_snapshot():
        return {"status": UNKNOWN, "signals": []}

    monkeypatch.setattr(infra_health, "snapshot", fake_snapshot)
    async with as_user(plain) as client:
        assert (await client.get("/api/v1/admin/ops/health")).status_code == 403
    async with as_user(staff) as client:
        response = await client.get("/api/v1/admin/ops/health")
    assert response.status_code == 200
    assert response.json()["status"] == UNKNOWN

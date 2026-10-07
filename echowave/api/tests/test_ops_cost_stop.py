"""Stream ops, handoff 15 H: operators can stop runaway cost.

What these defend: nothing is checked while ``cost_stop`` is off; a stop
refuses new work for its scope only; an expired stop decides nothing; an
unreadable state fails open; the evaluator engages a stop exactly when a
ceiling is crossed and does not re-engage one already standing; an
unconfigured ceiling is reported rather than treated as safe; and the
run-start gate refuses with ``cost_stopped``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api import constants
from api.services import features
from api.services.ops import cost_stop
from api.tests.support.fake_redis import FakeRedis


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "COST_STOP_ENABLED", False)
    monkeypatch.setattr(constants, "COST_STOP_PLATFORM_HOURLY_PAISE", 0)
    monkeypatch.setattr(constants, "COST_STOP_ORG_HOURLY_PAISE", 0)
    yield
    features.clear_snapshot()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "COST_STOP_ENABLED", True)


@pytest.mark.asyncio
async def test_off_means_never_stopped():
    redis = FakeRedis()
    await cost_stop.engage(
        scope="platform", reason="x", engaged_by="auto", client=redis
    )
    assert (await cost_stop.check(1, client=redis)).stopped is False


@pytest.mark.asyncio
async def test_an_organization_stop_is_scoped(on):
    redis = FakeRedis()
    await cost_stop.engage(
        scope="organization",
        organization_id=7,
        reason="loop",
        engaged_by="user:1",
        client=redis,
    )
    stopped = await cost_stop.check(7, client=redis)
    assert stopped.stopped and stopped.error_code == "cost_stopped"
    assert (await cost_stop.check(8, client=redis)).stopped is False
    assert await cost_stop.release(
        scope="organization", organization_id=7, client=redis
    )
    assert (await cost_stop.check(7, client=redis)).stopped is False


@pytest.mark.asyncio
async def test_a_platform_stop_reaches_everyone_and_an_expired_one_nobody(on):
    redis = FakeRedis()
    await cost_stop.engage(
        scope="platform", reason="spike", engaged_by="auto", client=redis
    )
    assert (await cost_stop.check(1, client=redis)).stopped
    assert (await cost_stop.check(None, client=redis)).stopped
    await cost_stop.engage(
        scope="platform",
        reason="spike",
        engaged_by="auto",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        client=redis,
    )
    assert (await cost_stop.check(1, client=redis)).stopped is False


@pytest.mark.asyncio
async def test_unreadable_state_fails_open(on):
    assert (await cost_stop.check(1, client=FakeRedis(fail=True))).stopped is False


class Spend:
    def __init__(self, platform=0, orgs=()):
        self.platform = platform
        self.orgs = list(orgs)

    async def platform_paise(self, session, since):
        return self.platform

    async def top_organizations(self, session, since, limit):
        return self.orgs[:limit]


@pytest.mark.asyncio
async def test_evaluate_reports_unconfigured_rather_than_safe(
    db_session, async_session, on
):
    result = await cost_stop.evaluate(
        async_session, source=Spend(10**9), client=FakeRedis()
    )
    assert result == {"skipped": "not_configured"}
    status = await cost_stop.status(client=FakeRedis())
    assert status["configured"] is False


@pytest.mark.asyncio
async def test_evaluate_engages_at_the_ceiling_once(
    db_session, async_session, on, monkeypatch
):
    monkeypatch.setattr(constants, "COST_STOP_PLATFORM_HOURLY_PAISE", 100_000)
    monkeypatch.setattr(constants, "COST_STOP_ORG_HOURLY_PAISE", 20_000)
    redis = FakeRedis()
    source = Spend(platform=50_000, orgs=[(3, 30_000), (4, 19_000)])
    first = await cost_stop.evaluate(async_session, source=source, client=redis)
    assert [s["organization_id"] for s in first["engaged"]] == [3]
    assert (await cost_stop.check(3, client=redis)).stopped
    assert not (await cost_stop.check(4, client=redis)).stopped
    second = await cost_stop.evaluate(async_session, source=source, client=redis)
    assert second["engaged"] == []
    source.platform = 150_000
    third = await cost_stop.evaluate(async_session, source=source, client=redis)
    assert [s["scope"] for s in third["engaged"]] == ["platform"]
    assert (await cost_stop.check(4, client=redis)).stopped


@pytest.mark.asyncio
async def test_the_run_start_gate_refuses_while_stopped(monkeypatch, on):
    from api.services import quota_service

    class _Workflow:
        id = 11
        user_id = 1
        workflow_configurations = {}

    async def get_workflow(*_a, **_k):
        return _Workflow()

    async def fake_check(organization_id, **_kwargs):
        return cost_stop.Verdict(
            stopped=True, error_code=cost_stop.ERROR_CODE, message=cost_stop.MESSAGE
        )

    monkeypatch.setattr(quota_service.db_client, "get_workflow", get_workflow)

    async def get_user_by_id(_id):
        return object()

    monkeypatch.setattr(quota_service.db_client, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(cost_stop, "check", fake_check)
    result = await quota_service.authorize_workflow_run_start(
        workflow_id=11, organization_id=5
    )
    assert result.has_quota is False
    assert result.error_code == "cost_stopped"

"""Stream ops, handoff 15 H / 34: evidence of drills and reviews, and the
console routes that read it.

What these defend: evidence carries numbers, not data (content-named and
nested metrics are dropped); unknown kinds and outcomes are refused; the
summary lists every kind, with ``None`` for one never recorded; a failed
drill reads as degraded on the health page; only a superadmin can record;
and the Laya and cost-stop routes answer while ``ops_console`` is on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api import constants
from api.db.models import UserModel
from api.enums import StaffRole
from api.services import features
from api.services.ops import evidence, infra_health


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", False)
    yield
    features.clear_snapshot()


@pytest.mark.asyncio
async def test_evidence_is_numbers_not_data(db_session, async_session):
    row = await evidence.record(
        async_session,
        kind="restore_drill",
        outcome="passed",
        summary="Restored 812 tables for ops@example.com",
        metrics={
            "restore_seconds": 41,
            "ledger_rows": 1200,
            "drift_rows": 0,
            "rows": [{"email": "a@b.c"}],
            "email": "a@b.c",
        },
    )
    assert row.metrics["restore_seconds"] == 41
    assert "rows" not in row.metrics and "email" not in row.metrics
    assert "ops@example.com" not in row.summary
    with pytest.raises(evidence.EvidenceError):
        await evidence.record(
            async_session, kind="vibes", outcome="passed", summary="x"
        )
    with pytest.raises(evidence.EvidenceError):
        await evidence.record(
            async_session, kind="backup", outcome="great", summary="x"
        )


@pytest.mark.asyncio
async def test_summary_names_every_kind(db_session, async_session):
    summary = await evidence.summary(async_session)
    assert set(summary) == set(evidence.KINDS)
    assert summary["capacity_review"] is None


@pytest.mark.asyncio
async def test_a_failed_or_stale_drill_is_not_healthy(db_session, async_session):
    missing = await infra_health._restore_drill()
    assert missing.status == infra_health.UNKNOWN
    await evidence.record(
        async_session,
        kind="restore_drill",
        outcome="failed",
        summary="ledger drift 3 rows",
        metrics={"drift_rows": 3},
    )
    failed = await infra_health._restore_drill()
    assert failed.status == infra_health.DEGRADED
    await evidence.record(
        async_session,
        kind="restore_drill",
        outcome="passed",
        summary="ok",
        occurred_at=datetime.now(UTC) - timedelta(days=90),
    )
    stale = await infra_health._restore_drill()
    assert stale.status == infra_health.DEGRADED
    await evidence.record(
        async_session, kind="restore_drill", outcome="passed", summary="ok"
    )
    fresh = await infra_health._restore_drill()
    assert fresh.status == infra_health.OK


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.mark.asyncio
async def test_routes(db_session, async_session, as_user, monkeypatch):
    monkeypatch.setattr(constants, "OPS_CONSOLE_ENABLED", True)
    support = UserModel(
        provider_id="ops-ev-support", staff_role=StaffRole.SUPPORT.value
    )
    admin = UserModel(provider_id="ops-ev-admin", staff_role=StaffRole.SUPERADMIN.value)
    async_session.add_all([support, admin])
    await async_session.flush()
    body = {
        "kind": "capacity_review",
        "outcome": "passed",
        "summary": "knee at 40",
        "metrics": {"knee_concurrency": 40, "p95_ms": 900},
    }
    async with as_user(support) as client:
        assert (
            await client.post("/api/v1/admin/ops/evidence", json=body)
        ).status_code == 403
        listed = await client.get("/api/v1/admin/ops/evidence")
        assert listed.status_code == 200
        assert listed.json()["latest"]["capacity_review"] is None
        assert (await client.get("/api/v1/admin/ops/cost-stop")).status_code == 200
        laya = await client.get("/api/v1/admin/ops/laya")
        assert laya.status_code == 200
        assert "breaker" in laya.json()["shadow"]
    async with as_user(admin) as client:
        made = await client.post("/api/v1/admin/ops/evidence", json=body)
        assert made.status_code == 200
        bad = await client.post(
            "/api/v1/admin/ops/evidence", json={**body, "kind": "nope"}
        )
        assert bad.status_code == 400
        listed = await client.get("/api/v1/admin/ops/evidence?kind=capacity_review")
        assert (
            listed.json()["latest"]["capacity_review"]["metrics"]["knee_concurrency"]
            == 40
        )

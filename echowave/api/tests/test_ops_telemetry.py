"""Stream ops, handoff 35-36: ops and cost events through the controls
event catalogue's outbox.

What these defend: ops events are catalogue entries and an unknown name
fails loudly; redaction runs before the catalogue's strict check, so a
sensitive or unknown property is dropped rather than the event refused;
nothing is written unless both ``server_analytics`` and the catalogue's
``event_catalogue`` are on; the event joins the caller's transaction (it
exists only if the receipt does); outbox health reads the shared outbox;
and the ops sweep no longer delivers analytics (controls' cron does).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api import constants
from api.db.controls_models import AnalyticsOutboxModel
from api.services import features
from api.services.events import catalogue
from api.services.ops import telemetry


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", False)
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", False)
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-pseudonym-key")
    yield
    features.clear_snapshot()


@pytest.fixture
def both_on(monkeypatch):
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)


def test_ops_events_are_catalogue_entries_owned_by_the_server():
    for name in ("ops_command_executed", "cost_stop_engaged", "cost_stop_released"):
        spec = catalogue.get(name)
        assert spec.owner == catalogue.SERVER and spec.domain == "ops"
        assert not (spec.allowed & catalogue.FORBIDDEN_PROPERTIES)
    usage = catalogue.get("usage_cost_recorded").allowed
    assert {"provider_cost_paise", "charged_paise", "uncosted_items"} <= usage


def test_unknown_event_name_is_refused():
    with pytest.raises(telemetry.UnknownEvent):
        telemetry.prepare("button_clicked", {})


def test_redaction_runs_before_the_strict_check():
    props = telemetry.prepare(
        "ops_command_executed",
        {
            "status": "failed",
            "command": "agent.pause",
            "reason_code": "HTTP 429 from vendor for asha@example.com",
            "prompt": "secret plans",
            "not_in_the_catalogue": 3,
        },
    )
    assert props == {
        "status": "failed",
        "command": "agent.pause",
        "reason_code": "rate_limited",
    }


def test_a_missing_reason_stays_null_not_other():
    assert telemetry.prepare("task_completed", {"reason_code": None}) == {
        "reason_code": None
    }


@pytest.mark.asyncio
async def test_nothing_is_written_unless_both_switches_are_on(
    db_session, async_session, monkeypatch
):
    assert await telemetry.record(async_session, "cost_stop_engaged") is None
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    assert await telemetry.record(async_session, "cost_stop_engaged") is None
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", False)
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
    assert await telemetry.record(async_session, "cost_stop_engaged") is None
    await async_session.flush()
    # Only this event: other streams' tests share the outbox table.
    assert (
        await async_session.scalars(
            select(AnalyticsOutboxModel).where(
                AnalyticsOutboxModel.name == "cost_stop_engaged"
            )
        )
    ).all() == []


@pytest.mark.asyncio
async def test_record_writes_one_pseudonymous_row_and_health_sees_it(
    db_session, async_session, both_on
):
    before = await telemetry.outbox_health(async_session)
    event_id = await telemetry.record(
        async_session,
        "ops_command_executed",
        user_id=5,
        workspace_id=9,
        task_id="ops-12",
        occurred_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
        properties={"status": "succeeded", "command": "laya.rollback"},
    )
    assert event_id
    row = await async_session.scalar(
        select(AnalyticsOutboxModel).where(AnalyticsOutboxModel.event_id == event_id)
    )
    assert row.name == "ops_command_executed"
    env = row.envelope
    assert env["user_id"].startswith("u_") and env["workspace_id"].startswith("w_")
    assert "9" != env["workspace_id"]
    assert env["task_id"] == "ops-12"
    assert env["properties"] == {"status": "succeeded", "command": "laya.rollback"}
    health = await telemetry.outbox_health(async_session)
    # Relative to before: other streams' tests share the outbox table.
    assert health["pending"] == before["pending"] + 1
    assert health["stuck"] == before["stuck"]


@pytest.mark.asyncio
async def test_costing_a_run_records_usage_cost_in_the_same_transaction(
    db_session, async_session, monkeypatch
):
    from api.db.models import (
        OrganizationModel,
        UserModel,
        WorkflowModel,
        WorkflowRunModel,
    )
    from api.services.billing.costing import cost_workflow_run

    user = UserModel(provider_id="ops-tel-cost-user")
    org = OrganizationModel(provider_id="ops-tel-cost-org", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
    await async_session.flush()
    workflow = WorkflowModel(
        name="wf",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()

    async def run():
        row = WorkflowRunModel(
            name="run",
            workflow_id=workflow.id,
            mode="twilio",
            usage_info={"call_duration_seconds": 60},
            cost_info={},
            initial_context={},
            gathered_context={},
            is_completed=True,
            created_at=datetime.now(UTC),
            answered_at=datetime.now(UTC),
        )
        async_session.add(row)
        await async_session.flush()
        return row

    def mine(run_id: int):
        # Only this run's events: other streams' tests share the outbox table.
        return select(AnalyticsOutboxModel).where(
            AnalyticsOutboxModel.envelope["task_id"].as_string() == f"run-{run_id}"
        )

    off = await run()
    await cost_workflow_run(async_session, off.id)
    assert (await async_session.scalars(mine(off.id))).all() == []

    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
    on = await run()
    await cost_workflow_run(async_session, on.id)
    rows = (await async_session.scalars(mine(on.id))).all()
    assert [r.name for r in rows] == ["usage_cost_recorded"]
    env = rows[0].envelope
    assert env["task_id"] == f"run-{on.id}"
    assert env["properties"]["cost_status"] == "costed"
    assert env["properties"]["currency"] == "INR"
    assert env["properties"]["duration_ms"] == 60_000


@pytest.mark.asyncio
async def test_the_sweep_is_a_no_op_with_every_flag_off(db_session, async_session):
    from api.tasks.ops import sweep_ops

    out = await sweep_ops({})
    assert out == {"cost_stop": {"skipped": "off"}}

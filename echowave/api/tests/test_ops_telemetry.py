"""Stream ops, handoff 35-36: server-owned events through a durable outbox.

What these defend: the envelope has every field the handoff names, nullable
ones explicitly null; an event name outside the catalogue fails loudly; the
user id is pseudonymous and absent without a key; nothing is written while
``server_analytics`` is off; the outbox row commits with the caller's
transaction; delivery sends ``event_id`` as PostHog's uuid (dedupe), marks
rows delivered, counts failures, and holds everything without a pseudonym
key rather than sending reversible ids.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api import constants
from api.db.ops_models import AnalyticsOutboxModel
from api.services import features
from api.services.ops import telemetry


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", False)
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-pseudonym-key")
    yield
    features.clear_snapshot()


class FakePostHog:
    def __init__(self, fail_on: set[str] | None = None):
        self.sent: list[dict] = []
        self.fail_on = fail_on or set()

    def capture(self, **kwargs):
        if kwargs["event"] in self.fail_on:
            raise TimeoutError("posthog slow")
        self.sent.append(kwargs)


def test_envelope_has_every_field_and_explicit_nulls():
    envelope = telemetry.build(
        "task_completed",
        user_id=7,
        workspace_id=3,
        properties={"channel": "web", "duration_ms": 120, "prompt": "secret"},
    )
    payload = envelope.payload()
    for name in (
        "event_id",
        "schema_version",
        "occurred_at",
        "environment",
        "release",
        "user_id",
        "workspace_id",
        "task_id",
        "trace_id",
        "configuration_version",
    ):
        assert name in payload
    assert payload["task_id"] is None
    assert payload["trace_id"] is None
    assert payload["user_id"].startswith("u_") and "7" != payload["user_id"]
    assert payload["occurred_at"].endswith("+00:00")
    assert payload["properties"]["channel"] == "web"
    assert "prompt" not in payload["properties"]
    assert payload["properties"]["_redacted"] == ["prompt"]


def test_unknown_event_name_is_refused():
    with pytest.raises(telemetry.UnknownEvent):
        telemetry.build("button_clicked")


def test_pseudonym_is_stable_keyed_and_absent_without_a_key(monkeypatch):
    first = telemetry.pseudonymous_id(42)
    assert first == telemetry.pseudonymous_id(42)
    assert first != telemetry.pseudonymous_id(43)
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "another-key")
    assert telemetry.pseudonymous_id(42) != first
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "")
    assert telemetry.pseudonymous_id(42) is None


def test_free_text_reason_becomes_a_code():
    envelope = telemetry.build(
        "task_failed",
        properties={"reason_code": "HTTP 429 from vendor for asha@example.com"},
    )
    assert envelope.properties["reason_code"] == "rate_limited"


@pytest.mark.asyncio
async def test_nothing_is_written_while_off(db_session, async_session):
    assert await telemetry.record(async_session, "task_started", workspace_id=1) is None
    await async_session.flush()
    rows = (await async_session.scalars(select(AnalyticsOutboxModel))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_record_and_dispatch_once_with_the_event_id_as_uuid(
    db_session, async_session, monkeypatch
):
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    event_id = await telemetry.record(
        async_session,
        "approval_granted",
        user_id=5,
        workspace_id=None,
        occurred_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
        properties={"channel": "whatsapp"},
    )
    await async_session.commit()
    client = FakePostHog()
    counts = await telemetry.dispatch_pending(async_session, client=client)
    assert counts["sent"] == 1
    assert client.sent[0]["uuid"] == event_id
    assert client.sent[0]["event"] == "approval_granted"
    assert client.sent[0]["properties"]["channel"] == "whatsapp"
    assert "groups" not in client.sent[0]
    # Delivered rows are not sent again.
    again = await telemetry.dispatch_pending(async_session, client=client)
    assert again["sent"] == 0
    assert len(client.sent) == 1
    health = await telemetry.outbox_health(async_session)
    assert health["pending"] == 0


@pytest.mark.asyncio
async def test_a_failed_delivery_is_counted_and_retried(
    db_session, async_session, monkeypatch
):
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    await telemetry.record(async_session, "task_failed", workspace_id=9)
    await telemetry.record(async_session, "task_completed", workspace_id=9)
    await async_session.commit()
    client = FakePostHog(fail_on={"task_failed"})
    counts = await telemetry.dispatch_pending(async_session, client=client)
    assert counts == {"sent": 1, "failed": 1, "skipped": 0}
    stuck = await async_session.scalar(
        select(AnalyticsOutboxModel).where(AnalyticsOutboxModel.event == "task_failed")
    )
    assert stuck.delivered_at is None
    assert stuck.attempts == 1
    assert stuck.last_error_code == "timeout"
    sent = client.sent[0]
    assert sent["groups"] == {"organization": "9"}


@pytest.mark.asyncio
async def test_without_a_pseudonym_key_the_outbox_is_held(
    db_session, async_session, monkeypatch
):
    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    await telemetry.record(async_session, "task_started", workspace_id=2)
    await async_session.commit()
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "")
    client = FakePostHog()
    counts = await telemetry.dispatch_pending(async_session, client=client)
    assert counts["skipped"] == -1
    assert client.sent == []


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

    off = await run()
    await cost_workflow_run(async_session, off.id)
    assert (await async_session.scalars(select(AnalyticsOutboxModel))).all() == []

    monkeypatch.setattr(constants, "SERVER_ANALYTICS_ENABLED", True)
    on = await run()
    await cost_workflow_run(async_session, on.id)
    rows = (await async_session.scalars(select(AnalyticsOutboxModel))).all()
    assert [r.event for r in rows] == ["usage_cost_recorded"]
    payload = rows[0].payload
    assert payload["workspace_id"] == org.id
    assert payload["task_id"] == f"run-{on.id}"
    assert payload["properties"]["status"] == "costed"
    assert payload["properties"]["duration_ms"] == 60_000


@pytest.mark.asyncio
async def test_the_sweep_is_a_no_op_with_every_flag_off(db_session, async_session):
    from api.tasks.ops import sweep_ops

    out = await sweep_ops({})
    assert out["cost_stop"] == {"skipped": "off"}
    assert out["analytics"]["skipped"] == -1
    assert "commands" not in out


def test_a_missing_reason_stays_null_not_other():
    envelope = telemetry.build("task_completed", properties={"reason_code": None})
    assert envelope.properties["reason_code"] is None

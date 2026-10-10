"""Operational alerts and the daily cost summary (services/ops_alerts).

Real Postgres (the transactional ``async_session``) and real Redis, under a
key prefix of its own per test. What these defend:

* each detector fires on a seeded spike and stays quiet below its minimum;
* one mail per incident key per cooldown, a reminder after it, a resolved
  mail once the condition has been clear twice, nothing resolved by a
  detector that could not measure;
* nothing secret, no address and no full phone number leaves in a mail;
* the cost summary's figures come from seeded billing rows, revenue lines
  are not spend, and usage with no rate says "no rate" instead of free;
* the flag off means the job, the counters and the routes do nothing;
* the routes are staff only, and the summaries superadmin only.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis

from api import constants
from api.db.image_models import GeneratedImageModel
from api.db.models import (
    CallCostItemModel,
    DataLookupCostModel,
    ModelUsageModel,
    OrganizationModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.db.shell_models import WaitlistRequestModel
from api.enums import StaffRole, WorkflowRunMode, WorkflowRunState
from api.services import features
from api.services.messaging import email
from api.services.messaging.email import SendResult
from api.services.ops_alerts import (
    detectors,
    incidents,
    metering,
    notify,
    runner,
    signals,
    store,
    summary,
)
from api.services.ops_alerts import thresholds as t
from api.services.ops_alerts.incidents import Evaluation, Finding
from api.services.worker_health import HEARTBEAT_KEY, record_heartbeat
from api.tasks import ops_alerts as job

# Mid-afternoon IST on a Thursday, well inside one IST day.
NOW = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)


# --- fixtures --------------------------------------------------------------------


@pytest.fixture
async def redis():
    client = aioredis.from_url(constants.REDIS_URL)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def _isolated(monkeypatch, redis):
    prefix = f"test:ops_alerts:{uuid.uuid4().hex}:"
    monkeypatch.setattr(store, "KEY_PREFIX", prefix)
    monkeypatch.setattr(constants, "OPS_ALERTS_ENABLED", False)
    monkeypatch.setattr(constants, "OPS_ALERT_EMAILS", ["ops@example.com"])
    features.clear_snapshot()
    saved_heartbeat = await redis.get(HEARTBEAT_KEY)
    yield
    async for name in redis.scan_iter(match=prefix + "*"):
        await redis.delete(name)
    if saved_heartbeat is None:
        await redis.delete(HEARTBEAT_KEY)
    else:
        await redis.set(HEARTBEAT_KEY, saved_heartbeat)
    features.clear_snapshot()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "OPS_ALERTS_ENABLED", True)


@pytest.fixture
def mails(monkeypatch):
    sent: list[dict] = []

    async def fake_send(*, to, subject, body_text, sender="notifications", **_):
        sent.append({"to": to, "subject": subject, "body": body_text, "sender": sender})
        return SendResult(ok=True)

    monkeypatch.setattr(email, "send_email", fake_send)
    monkeypatch.setattr(email, "email_is_configured", lambda: True)
    return sent


async def _org(session, slug: str, name: str | None = None):
    org = OrganizationModel(
        provider_id=f"org-{slug}-{uuid.uuid4().hex[:6]}",
        quota_decibyl_tokens=0,
        billing_name=name,
    )
    session.add(org)
    await session.flush()
    workflow = WorkflowModel(name=f"Agent {slug}", organization_id=org.id, user_id=None)
    session.add(workflow)
    await session.flush()
    return org, workflow


async def _run(
    session, workflow, *, when=NOW, mode=WorkflowRunMode.PLIVO.value, **fields
):
    run = WorkflowRunModel(
        name="call",
        workflow_id=workflow.id,
        mode=mode,
        state=WorkflowRunState.COMPLETED.value,
        created_at=when,
        **fields,
    )
    session.add(run)
    await session.flush()
    return run


async def _cost(session, run, component, *, units, cost, rate=1, when=None):
    session.add(
        CallCostItemModel(
            workflow_run_id=run.id,
            component=component,
            provider="vendor",
            model="m",
            units=units,
            unit_rate_mpaise=rate,
            cost_paise=cost,
            provider_cost_paise=cost,
            created_at=when or run.created_at,
        )
    )
    await session.flush()


# --- the flag ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_does_nothing(db_session, async_session, redis, mails):
    assert await job.run_ops_alerts(now=NOW) == {"skipped": "flag_off"}
    assert await job.send_daily_cost_summary(now=NOW) == {"skipped": "flag_off"}
    await signals.pipeline_started(client=redis)
    await signals.pipeline_error(1, "DeepgramSTTService#0", client=redis)
    await signals.direct_llm(ok=False, client=redis)
    await signals.mark_tick("fire_due_routines", client=redis)
    assert [k async for k in redis.scan_iter(match=store.KEY_PREFIX + "*")] == []
    assert mails == []


# --- calls ---------------------------------------------------------------------------


async def _calls(session, workflow, *, answered=0, failed=0, unknown=0, at=NOW):
    when = at - timedelta(minutes=5)
    for _ in range(answered):
        await _run(session, workflow, when=when, is_completed=True, billable_seconds=40)
    for _ in range(failed):
        await _run(
            session,
            workflow,
            when=when,
            is_completed=True,
            gathered_context={"mapped_call_disposition": "failed"},
        )
    for _ in range(unknown):
        await _run(session, workflow, when=when, is_completed=True)


@pytest.mark.asyncio
async def test_call_failure_spike_fires(db_session, async_session):
    _, workflow = await _org(async_session, "calls", "Clinic")
    await _calls(async_session, workflow, answered=5, failed=5, unknown=2)
    # Browser calls and runs still ringing are not carrier outcomes.
    await _run(
        async_session,
        workflow,
        when=NOW - timedelta(minutes=2),
        mode="webrtc",
        is_completed=True,
    )
    await _run(
        async_session, workflow, when=NOW - timedelta(minutes=1), is_completed=False
    )
    evaluation = await detectors.detect_calls(async_session, now=NOW)
    (finding,) = evaluation.findings
    assert finding.key == "calls:failure_spike"
    assert finding.severity == incidents.CRITICAL
    assert finding.metrics["finished"] == 12
    assert finding.metrics["carrier_failed"] == 5
    assert finding.metrics["unknown"] == 2
    assert finding.metrics["in_progress"] == 1
    assert "Clinic" in finding.detail


@pytest.mark.asyncio
async def test_call_failures_below_the_minimum_count_are_quiet(
    db_session, async_session
):
    _, workflow = await _org(async_session, "fewcalls")
    await _calls(async_session, workflow, failed=t.CALL_FAILURE_MIN_CALLS - 1)
    assert (await detectors.detect_calls(async_session, now=NOW)).findings == []


@pytest.mark.asyncio
async def test_call_failures_below_the_share_are_quiet(db_session, async_session):
    _, workflow = await _org(async_session, "okcalls")
    await _calls(async_session, workflow, answered=18, failed=2)
    assert (await detectors.detect_calls(async_session, now=NOW)).findings == []


@pytest.mark.asyncio
async def test_calls_outside_the_window_do_not_count(db_session, async_session):
    _, workflow = await _org(async_session, "oldcalls")
    await _calls(async_session, workflow, failed=20, at=NOW - timedelta(minutes=40))
    assert (await detectors.detect_calls(async_session, now=NOW)).findings == []


# --- providers -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_error_spike_fires_per_component(redis, on):
    for _ in range(20):
        await signals.pipeline_started(now=NOW, client=redis)
    for run_id in range(1, 7):
        await signals.pipeline_error(
            run_id, "DeepgramSTTService#0", now=NOW, client=redis
        )
        # The same run complaining again is still one failed run.
        await signals.pipeline_error(
            run_id, "DeepgramSTTService#0", now=NOW, client=redis
        )
    # Two TTS errors: under the minimum count, whatever the rate.
    await signals.pipeline_error(1, "CartesiaTTSService#0", now=NOW, client=redis)
    await signals.pipeline_error(2, "CartesiaTTSService#0", now=NOW, client=redis)
    for ok in [False] * 5 + [True] * 5:
        await signals.direct_llm(ok=ok, now=NOW, client=redis)
    evaluation = await detectors.detect_providers(now=NOW, client=redis)
    keys = {f.key: f for f in evaluation.findings}
    assert set(keys) == {"provider:stt:calls", "provider:llm:direct"}
    assert keys["provider:stt:calls"].metrics == {
        "errors": 6,
        "attempts": 20,
        "share": 0.3,
    }


@pytest.mark.asyncio
async def test_provider_errors_below_the_minimum_are_quiet(redis, on):
    for run_id in range(t.PROVIDER_ERROR_MIN_COUNT - 1):
        await signals.pipeline_error(
            run_id, "OpenAILLMService#0", now=NOW, client=redis
        )
    for _ in range(t.PROVIDER_ERROR_MIN_COUNT - 1):
        await signals.dial(ok=False, now=NOW, client=redis)
    assert (await detectors.detect_providers(now=NOW, client=redis)).findings == []


@pytest.mark.asyncio
async def test_old_provider_errors_fall_out_of_the_window(redis, on):
    long_ago = NOW - timedelta(minutes=t.PROVIDER_WINDOW_MINUTES + 1)
    for run_id in range(10):
        await signals.pipeline_error(
            run_id, "DeepgramSTTService#0", now=long_ago, client=redis
        )
    assert (await detectors.detect_providers(now=NOW, client=redis)).findings == []


def test_an_unclassified_processor_is_counted_not_dropped():
    assert signals.component_of("SomethingNewService#0") == "other"
    assert signals.component_of(None) == "other"
    series = detectors.provider_series({"call:n": 3, "call:brandnew:err": 2})
    assert [s["key"] for s in series] == ["provider:brandnew:calls"]


@pytest.mark.asyncio
async def test_builder_turns_are_counted_once_and_failures_as_failures(
    monkeypatch, redis, on
):
    from api.services.agent_builder import client as builder

    async def failing(**_):
        raise builder.BuilderClientError("down")

    async def brain(exc, **_):
        return builder.ModelReply(text="from the brain", tool_calls=[], usage=None)

    monkeypatch.setattr(builder, "_complete", failing)
    monkeypatch.setattr(builder, "_fallback_brain", brain)
    reply = await builder.complete(
        provider="openai", model="m", api_key="k", system="", conversation=[], tools=[]
    )
    assert reply.text == "from the brain"
    counts = await signals.window_counts(1, client=redis)
    assert counts == {"llm_direct:n": 1, "llm_direct:err": 1}


# --- background jobs ----------------------------------------------------------------


@pytest.fixture
def no_queue(monkeypatch):
    async def none():
        return None

    monkeypatch.setattr(detectors, "_queue_oldest_due_seconds", none)


async def _ticks(redis, at):
    for name in signals.TICKS:
        await signals.mark_tick(name, now=at, client=redis)


@pytest.mark.asyncio
async def test_a_dead_worker_is_one_incident_not_five(redis, on, no_queue):
    await record_heartbeat(now=NOW - timedelta(minutes=30))
    await _ticks(redis, NOW - timedelta(minutes=30))
    worker, ticks = await detectors.detect_jobs(now=NOW, client=redis)
    assert [f.key for f in worker.findings] == ["jobs:worker"]
    assert worker.findings[0].severity == incidents.CRITICAL
    # Ticks are not judged while the worker is down, and nothing they had
    # open may resolve on that.
    assert ticks.findings == [] and ticks.ok is False


@pytest.mark.asyncio
async def test_a_stuck_tick_fires_while_the_worker_is_alive(redis, on, no_queue):
    await record_heartbeat(now=NOW)
    await _ticks(redis, NOW - timedelta(minutes=1))
    await signals.mark_tick(
        "fire_due_routines",
        now=NOW - timedelta(minutes=t.TICK_STALE_MINUTES + 5),
        client=redis,
    )
    worker, ticks = await detectors.detect_jobs(now=NOW, client=redis)
    assert worker.findings == []
    assert [f.key for f in ticks.findings] == ["jobs:tick:fire_due_routines"]


@pytest.mark.asyncio
async def test_a_tick_never_seen_waits_out_the_grace(redis, on, no_queue):
    await record_heartbeat(now=NOW)
    _, early = await detectors.detect_jobs(now=NOW, client=redis)
    assert early.findings == []
    later = NOW + timedelta(minutes=t.TICK_NEVER_SEEN_GRACE_MINUTES + 1)
    await record_heartbeat(now=later)
    _, ticks = await detectors.detect_jobs(now=later, client=redis)
    assert {f.key for f in ticks.findings} == {f"jobs:tick:{n}" for n in signals.TICKS}
    assert "has not completed once" in ticks.findings[0].detail


@pytest.mark.asyncio
async def test_a_backed_up_queue_fires(redis, on, monkeypatch):
    async def old():
        return (t.QUEUE_OLDEST_DUE_MINUTES + 5) * 60.0

    monkeypatch.setattr(detectors, "_queue_oldest_due_seconds", old)
    await record_heartbeat(now=NOW)
    await _ticks(redis, NOW)
    _, ticks = await detectors.detect_jobs(now=NOW, client=redis)
    assert [f.key for f in ticks.findings] == ["jobs:queue"]


@pytest.mark.asyncio
async def test_watched_ticks_are_stamped_on_completion(redis, on):
    from api.tasks.call_when_done import call_when_done_tick
    from api.tasks.care import care_medicine_tick
    from api.tasks.routines import fire_due_routines
    from api.tasks.today import deliver_due_reminders

    tasks = {
        "fire_due_routines": fire_due_routines,
        "deliver_due_reminders": deliver_due_reminders,
        "care_medicine_tick": care_medicine_tick,
        "call_when_done_tick": call_when_done_tick,
    }
    # Every watched tick is decorated, under the name ARQ schedules it by.
    assert set(tasks) == set(signals.TICKS)
    for name, fn in tasks.items():
        assert fn.__name__ == name and hasattr(fn, "__wrapped__")

    calls = []

    @signals.stamped("fire_due_routines")
    async def ok(_ctx):
        calls.append(1)

    @signals.stamped("deliver_due_reminders")
    async def boom(_ctx):
        raise RuntimeError("tick failed")

    await ok(None)
    with pytest.raises(RuntimeError):
        await boom(None)
    stamps = await signals.last_ticks(redis)
    assert stamps["fire_due_routines"] is not None
    assert stamps["deliver_due_reminders"] is None


# --- spend ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_anomaly_fires_for_the_workspace_not_the_platform(
    db_session, async_session, redis
):
    hot, hot_agent = await _org(async_session, "hot", "Runaway Ltd")
    _, calm_agent = await _org(async_session, "calm")
    today = metering.ist_day(NOW)
    for back in range(1, t.SPEND_BASELINE_DAYS + 1):
        start, _ = metering.day_window(today - timedelta(days=back))
        run = await _run(async_session, hot_agent, when=start + timedelta(hours=6))
        await _cost(async_session, run, "llm_input", units=1000, cost=10_000)
    run = await _run(async_session, hot_agent, when=NOW - timedelta(hours=1))
    await _cost(async_session, run, "llm_input", units=1000, cost=100_000)
    # A new workspace's first day, under the floor: not an incident.
    small = await _run(async_session, calm_agent, when=NOW - timedelta(hours=1))
    await _cost(
        async_session, small, "tts", units=500, cost=t.SPEND_ANOMALY_MIN_ORG_PAISE - 1
    )

    evaluation = await detectors.detect_spend(async_session, now=NOW, client=redis)
    assert [f.key for f in evaluation.findings] == [f"spend:org:{hot.id}"]
    finding = evaluation.findings[0]
    assert finding.metrics == {"today_paise": 100_000, "average_paise": 10_000}
    assert "Runaway Ltd" in finding.title and "10.0x" in finding.detail


def test_spend_needs_both_the_multiple_and_the_floor():
    floor = t.SPEND_ANOMALY_MIN_ORG_PAISE
    assert detectors.is_anomalous(floor, 0, floor)
    assert not detectors.is_anomalous(floor - 1, 0, floor)
    assert not detectors.is_anomalous(floor * 2, floor, floor)  # only 2x
    assert detectors.is_anomalous(floor * 3, floor, floor)


# --- invites -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_requests_waiting_too_long(db_session, async_session):
    async_session.add_all(
        [
            WaitlistRequestModel(
                email=f"old-{uuid.uuid4().hex[:6]}@example.com",
                status="waitlisted",
                created_at=NOW - timedelta(hours=t.INVITE_WAIT_HOURS + 2),
            ),
            WaitlistRequestModel(
                email=f"decided-{uuid.uuid4().hex[:6]}@example.com",
                status="invited",
                created_at=NOW - timedelta(hours=40),
            ),
        ]
    )
    await async_session.flush()
    (finding,) = (await detectors.detect_invites(async_session, now=NOW)).findings
    assert finding.key == "invites:waiting"
    assert finding.metrics == {"count": 1, "oldest_hours": t.INVITE_WAIT_HOURS + 2}
    assert "@" not in finding.detail


@pytest.mark.asyncio
async def test_fresh_invite_requests_are_quiet(db_session, async_session):
    async_session.add(
        WaitlistRequestModel(
            email=f"new-{uuid.uuid4().hex[:6]}@example.com",
            status="waitlisted",
            created_at=NOW - timedelta(hours=1),
        )
    )
    await async_session.flush()
    assert (await detectors.detect_invites(async_session, now=NOW)).findings == []


# --- incidents: dedupe, cooldown, resolve --------------------------------------------


def _finding(detail="12 of 20 calls failed."):
    return Finding(key="calls:failure_spike", title="Call failures", detail=detail)


@pytest.mark.asyncio
async def test_one_mail_per_key_per_cooldown_then_a_reminder(redis, mails):
    firing = Evaluation("calls", [_finding()])
    first = await incidents.apply(firing, now=NOW, client=redis)
    assert first == [{"key": "calls:failure_spike", "action": "opened", "mailed": True}]
    for minutes in (5, 10, 30, t.ALERT_COOLDOWN_MINUTES - 1):
        assert (
            await incidents.apply(
                firing, now=NOW + timedelta(minutes=minutes), client=redis
            )
            == []
        )
    assert len(mails) == 1 and mails[0]["sender"] == "notifications"
    later = NOW + timedelta(minutes=t.ALERT_COOLDOWN_MINUTES + 1)
    reminded = await incidents.apply(firing, now=later, client=redis)
    assert reminded == [
        {"key": "calls:failure_spike", "action": "reminded", "mailed": True}
    ]
    assert mails[1]["subject"].startswith("[Decibyl ops] Still happening:")
    (open_one,) = await incidents.open_incidents(redis)
    assert open_one["notified"] is True


@pytest.mark.asyncio
async def test_resolves_after_two_clear_readings_with_one_mail(redis, mails):
    await incidents.apply(Evaluation("calls", [_finding()]), now=NOW, client=redis)
    clear = Evaluation("calls", [])
    assert (
        await incidents.apply(clear, now=NOW + timedelta(minutes=5), client=redis) == []
    )
    assert len(await incidents.open_incidents(redis)) == 1
    resolved = await incidents.apply(
        clear, now=NOW + timedelta(minutes=10), client=redis
    )
    assert resolved == [
        {"key": "calls:failure_spike", "action": "resolved", "mailed": True}
    ]
    assert await incidents.open_incidents(redis) == []
    assert mails[-1]["subject"] == "[Decibyl ops] Resolved: Call failures"
    (past,) = await incidents.history(client=redis)
    assert past["resolved_at"]

    # Back inside the cooldown: open on the page, no second mail.
    reopened = await incidents.apply(
        Evaluation("calls", [_finding()]), now=NOW + timedelta(minutes=15), client=redis
    )
    assert reopened == [
        {"key": "calls:failure_spike", "action": "opened", "mailed": False}
    ]
    assert len(mails) == 2


@pytest.mark.asyncio
async def test_a_detector_that_could_not_measure_resolves_nothing(redis, mails):
    await incidents.apply(Evaluation("calls", [_finding()]), now=NOW, client=redis)
    for minutes in (5, 10, 15):
        await incidents.apply(
            Evaluation("calls", ok=False),
            now=NOW + timedelta(minutes=minutes),
            client=redis,
        )
    assert len(await incidents.open_incidents(redis)) == 1
    # And another detector's clear reading is not this one's.
    await incidents.apply(
        Evaluation("spend"), now=NOW + timedelta(minutes=20), client=redis
    )
    await incidents.apply(
        Evaluation("spend"), now=NOW + timedelta(minutes=25), client=redis
    )
    assert len(await incidents.open_incidents(redis)) == 1
    assert len(mails) == 1


@pytest.mark.asyncio
async def test_a_failed_send_is_retried_next_time(redis, monkeypatch):
    sent = []

    async def flaky(*, to, subject, body_text, **_):
        sent.append(subject)
        return SendResult(ok=len(sent) > 1, error="smtp down")

    monkeypatch.setattr(email, "send_email", flaky)
    monkeypatch.setattr(email, "email_is_configured", lambda: True)
    firing = Evaluation("calls", [_finding()])
    first = await incidents.apply(firing, now=NOW, client=redis)
    assert first[0]["mailed"] is False
    second = await incidents.apply(firing, now=NOW + timedelta(minutes=5), client=redis)
    assert second[0] == {
        "key": "calls:failure_spike",
        "action": "reminded",
        "mailed": True,
    }


@pytest.mark.asyncio
async def test_two_evaluators_in_one_window_evaluate_once(redis, on):
    seen = []

    async def fake_run(name, now, client):
        seen.append(name)
        return [Evaluation(name)]

    await runner.evaluate(("calls",), now=NOW, client=redis, run=fake_run)
    second = await runner.evaluate(("calls",), now=NOW, client=redis, run=fake_run)
    assert seen == ["calls"]
    assert second == {"calls": {"skipped": "evaluated_elsewhere"}}


@pytest.mark.asyncio
async def test_a_failing_detector_does_not_stop_the_rest(redis, on, mails):
    async def fake_run(name, now, client):
        if name == "calls":
            raise RuntimeError("db down")
        return [Evaluation(name, [Finding(key=f"{name}:x", title=name, detail="d")])]

    out = await runner.evaluate(
        ("calls", "invites"), now=NOW, client=redis, lock=False, run=fake_run
    )
    assert out["calls"][0]["ok"] is False
    assert out["invites"][0]["firing"] == ["invites:x"]
    assert len(mails) == 1


@pytest.mark.asyncio
async def test_the_job_runs_every_detector_end_to_end(
    db_session, async_session, redis, on, mails, no_queue
):
    _, workflow = await _org(async_session, "e2e")
    now = datetime.now(UTC)
    await _calls(async_session, workflow, failed=12, at=now)
    await record_heartbeat(now=now)
    out = await job.run_ops_alerts(now=now)
    assert set(out) == set(runner.ALL)
    assert out["calls"][0]["firing"] == ["calls:failure_spike"]
    assert any(
        m["subject"].startswith("[Decibyl ops, urgent] Call failures") for m in mails
    )


# --- masking -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secret_address_or_full_number_leaves_in_a_mail(redis, mails):
    detail = (
        "Caller +91 98765 43210 and 9876501234 failed; key sk-abcdefghijklmnopqrstuvwxyz0123 "
        "token=supersecretvalue for owner@example.com; run 4521 on 2026-10-08."
    )
    await incidents.apply(
        Evaluation("calls", [_finding(detail)]), now=NOW, client=redis
    )
    body = mails[0]["body"]
    for leaked in (
        "98765 43210",
        "9876501234",
        "abcdefghijklmnop",
        "supersecretvalue",
        "owner@example.com",
    ):
        assert leaked not in body
    assert "…3210" in body and "…1234" in body
    assert "run 4521" in body and "2026-10-08" in body
    (shown,) = await incidents.open_incidents(redis)
    assert "9876501234" not in incidents.as_public(shown)["detail"]


@pytest.mark.asyncio
async def test_unset_recipients_fall_back_to_superadmins(
    db_session, async_session, monkeypatch
):
    monkeypatch.setattr(constants, "OPS_ALERT_EMAILS", [])
    async_session.add(
        UserModel(
            provider_id=f"sa-{uuid.uuid4().hex[:6]}",
            email="Founder@Example.com",
            staff_role=StaffRole.SUPERADMIN.value,
        )
    )
    await async_session.flush()
    assert "founder@example.com" in await notify.operator_emails()


# --- the daily cost summary --------------------------------------------------------


async def _seed_day(session):
    """Yesterday's metered spend across every source billing writes."""
    day = metering.ist_day(NOW) - timedelta(days=1)
    start, _ = metering.day_window(day)
    at = start + timedelta(hours=10)
    acme, front_desk = await _org(session, "acme", "Acme Clinic")
    beta, reminders = await _org(session, "beta", "Beta Traders")
    call = await _run(
        session, front_desk, when=at, billable_seconds=60, is_completed=True
    )
    await _cost(session, call, "llm_input", units=12_000, cost=500)
    await _cost(session, call, "stt", units=120, cost=200)
    # Revenue, not spend.
    await _cost(session, call, "platform", units=60, cost=999)
    other = await _run(
        session,
        reminders,
        when=at,
        is_completed=True,
        gathered_context={"mapped_call_disposition": "busy"},
    )
    # Ten minutes of telephony settled with no rate on file.
    await _cost(session, other, "telephony", units=600, cost=0, rate=0)
    session.add_all(
        [
            ModelUsageModel(
                organization_id=acme.id,
                feature="decibyl_reply",
                provider="nobody-priced-this",
                model="mystery",
                prompt_tokens=5_000,
                completion_tokens=1_000,
                created_at=at,
            ),
            ModelUsageModel(
                organization_id=acme.id,
                feature="decibyl_reply:byok",
                provider="openai",
                model="m",
                prompt_tokens=99_999,
                created_at=at,
            ),
            GeneratedImageModel(
                image_uuid=f"img_{uuid.uuid4().hex}",
                organization_id=beta.id,
                workflow_id=reminders.id,
                kind="generated",
                provider="gemini",
                key_source="platform",
                storage_key="k",
                storage_backend="minio",
                vendor_cost_paise=300,
                cost_source="rate_card",
                created_at=at,
            ),
            GeneratedImageModel(
                image_uuid=f"img_{uuid.uuid4().hex}",
                organization_id=beta.id,
                kind="generated",
                provider="openai",
                key_source="byok",
                storage_key="k2",
                storage_backend="minio",
                vendor_cost_paise=1_000,
                created_at=at,
            ),
            DataLookupCostModel(
                organization_id=acme.id,
                provider="search",
                kind="search",
                requests=2,
                vendor_cost_paise=50,
                ref_id=uuid.uuid4().hex,
                created_at=at,
            ),
        ]
    )
    rental = RecurringChargeModel(
        organization_id=beta.id,
        charge_type="number_rental",
        resource_id=1,
        started_at=at,
        cost_paise=1_500,
    )
    session.add(rental)
    await session.flush()
    session.add(
        RecurringChargePeriodModel(
            recurring_charge_id=rental.id,
            organization_id=beta.id,
            period_start=at,
            period_end=at + timedelta(days=30),
            charged_paise=2_000,
            cost_paise=1_500,
            charged_at=at,
        )
    )
    # The week before: Rs 7 a day on average.
    for back in range(1, t.SPEND_BASELINE_DAYS + 1):
        s, _ = metering.day_window(day - timedelta(days=back))
        earlier = await _run(session, front_desk, when=s + timedelta(hours=5))
        await _cost(session, earlier, "tts", units=100, cost=700)
    await session.flush()
    return day, acme, beta, front_desk, reminders


@pytest.mark.asyncio
async def test_the_summary_is_billing_s_numbers(db_session, async_session):
    day, acme, beta, front_desk, reminders = await _seed_day(async_session)
    report = await summary.build(async_session, day)
    lines = {line["key"]: line for line in report["lines"]}

    # 500 + 200 + 300 + 50 + 1,500; no platform fee, no own-key image.
    assert report["total_paise"] == 2_550
    assert "platform" not in lines
    assert lines["llm_input"]["cost_paise"] == 500
    assert lines["stt"] == {
        **lines["stt"],
        "units": 120,
        "cost_paise": 200,
        "no_rate": False,
    }
    assert lines["image"]["units"] == 1 and lines["image"]["cost_paise"] == 300
    assert lines["numbers"]["cost_paise"] == 1_500
    assert lines["data"]["cost_paise"] == 50

    # Usage with no rate is shown as usage with no rate, never as free.
    assert lines["telephony"]["no_rate"] is True
    assert lines["telephony"]["cost_paise"] == 0 and lines["telephony"]["units"] == 600
    assert (
        lines["llm_output"]["no_rate"] is True and lines["llm_output"]["units"] == 1_000
    )

    assert [o["organization_id"] for o in report["top_organizations"]] == [
        beta.id,
        acme.id,
    ]
    assert report["top_organizations"][0]["cost_paise"] == 1_800
    assert report["top_agents"][0]["agent_id"] == front_desk.id
    assert report["top_agents"][0]["organization"] == "Acme Clinic"
    assert report["average_paise"] == 700
    assert report["delta_paise"] == 1_850
    assert report["calls"]["answered"] == 1 and report["calls"]["not_connected"] == 1
    assert report["runs"]["carrier_calls"] == 2
    assert report["active_organizations"] == 2

    subject, body = summary.compose(report)
    assert "Rs 25.50" in subject
    assert "Telephony: no rate   10.0 min" in body
    assert "Language model output: no rate   1,000 tokens" in body
    assert "1. Beta Traders" in body
    assert "+264%" in body
    assert "Not counted as spend" in body


@pytest.mark.asyncio
async def test_the_summary_is_mailed_once_and_kept_for_the_page(
    db_session, async_session, redis, on, mails
):
    day, *_ = await _seed_day(async_session)
    first = await summary.send_for(async_session, day, client=redis)
    again = await summary.send_for(async_session, day, client=redis)
    assert first["sent"] is True and first["total_paise"] == 2_550
    assert again == {"day": day.isoformat(), "sent": False, "skipped": "already_sent"}
    assert len(mails) == 1 and mails[0]["subject"].startswith(
        "[Decibyl ops] Cost summary"
    )

    week = await summary.recent(
        async_session, today=day + timedelta(days=1), client=redis
    )
    assert len(week) == t.DAILY_SUMMARY_HISTORY_DAYS
    assert week[0]["day"] == day.isoformat() and week[0]["mailed_at"]
    assert week[1]["mailed_at"] is None and week[1]["total_paise"] == 700


@pytest.mark.asyncio
async def test_the_job_mails_yesterday(db_session, async_session, on, mails):
    day, *_ = await _seed_day(async_session)
    out = await job.send_daily_cost_summary(now=NOW)
    assert out["day"] == day.isoformat() and out["sent"] is True


# --- the staff routes ----------------------------------------------------------------


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.mark.asyncio
async def test_routes_are_staff_only_and_dark_while_off(
    db_session, async_session, as_user, monkeypatch, redis
):
    member = UserModel(provider_id=f"m-{uuid.uuid4().hex[:6]}")
    support = UserModel(
        provider_id=f"s-{uuid.uuid4().hex[:6]}", staff_role=StaffRole.SUPPORT.value
    )
    admin = UserModel(
        provider_id=f"a-{uuid.uuid4().hex[:6]}", staff_role=StaffRole.SUPERADMIN.value
    )
    async_session.add_all([member, support, admin])
    await async_session.flush()

    async with as_user(admin) as client:
        assert (await client.get("/api/v1/admin/ops/alerts")).status_code == 404

    monkeypatch.setattr(constants, "OPS_ALERTS_ENABLED", True)
    await incidents.apply(
        Evaluation("calls", [_finding("call to 9876501234")]), now=NOW, client=redis
    )
    async with as_user(member) as client:
        assert (await client.get("/api/v1/admin/ops/alerts")).status_code == 403
    async with as_user(support) as client:
        listed = await client.get("/api/v1/admin/ops/alerts")
        assert listed.status_code == 200
        body = listed.json()
        assert [i["key"] for i in body["open"]] == ["calls:failure_spike"]
        assert "9876501234" not in json.dumps(body)
        assert {tick["name"] for tick in body["ticks"]} == set(signals.TICKS)
        assert body["thresholds"]["CALL_FAILURE_SHARE"] == t.CALL_FAILURE_SHARE
        assert (
            await client.get("/api/v1/admin/ops/alerts/daily-summaries")
        ).status_code == 403
    async with as_user(admin) as client:
        summaries = await client.get("/api/v1/admin/ops/alerts/daily-summaries")
        assert summaries.status_code == 200
        assert len(summaries.json()["summaries"]) == t.DAILY_SUMMARY_HISTORY_DAYS

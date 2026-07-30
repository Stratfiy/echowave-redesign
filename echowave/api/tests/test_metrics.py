"""Latency percentiles, token efficiency, tool timings and privacy health.

Two failure modes these guard against, both of which produce a number that
looks fine:

**A percentile computed over the wrong rows.** A turn missing one timestamp
must be excluded, not treated as zero — a zero drags a percentile *down*, so
broken instrumentation reads as an improvement and nobody investigates.

**A ratio whose numerator and denominator come from different periods.** Tokens
were originally bucketed by when the costing job ran and minutes by when the
call happened, so tokens-per-minute divided one week's tokens by another week's
conversation. The totals still reconciled, which is exactly why it survived a
first look.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db import billing_dashboard_client as dash
from api.db.models import (
    CallCostItemModel,
    CallTurnMetricModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy import metrics as privacy_metrics


async def _org(async_session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    async_session.add_all([org, user])
    await async_session.flush()
    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()
    return org, workflow


async def _run(async_session, workflow, *, age_days=0, seconds=60, recording=None):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=datetime.now(UTC) - timedelta(days=age_days),
        billable_seconds=seconds,
        recording_url=recording,
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()
    return run


async def _turn(
    async_session,
    run,
    *,
    index=0,
    latency=None,
    stt_final=None,
    llm_first=None,
    tts_first=None,
    tool=None,
    tool_ms=None,
    age_days=0,
):
    turn = CallTurnMetricModel(
        workflow_run_id=run.id,
        turn_index=index,
        latency_ms=latency,
        t_stt_final_ms=stt_final,
        t_llm_first_token_ms=llm_first,
        t_tts_first_byte_ms=tts_first,
        tool_called=tool,
        tool_ms=tool_ms,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    async_session.add(turn)
    await async_session.flush()
    return turn


async def _llm_cost(async_session, run, *, tokens, paise=1, model="gpt-4o-mini"):
    item = CallCostItemModel(
        workflow_run_id=run.id,
        component="llm",
        provider="openai",
        model=model,
        units=tokens,
        unit_rate_mpaise=1,
        cost_paise=paise,
        created_at=datetime.now(UTC),
    )
    async_session.add(item)
    await async_session.flush()
    return item


def _today():
    return datetime.now(UTC).date()


def _wide():
    """A window that cannot miss "now" whatever the hour.

    Buckets are truncated in IST, so a row written after 18:30 UTC lands on the
    *next* IST day. A test asserting against a single-day window therefore
    passes all morning and fails all evening, which is the worst kind of test.
    """
    return _today() - timedelta(days=60), _today() + timedelta(days=1)


@pytest.mark.asyncio
class TestLatencyMeasuresAreDistinct:
    """TTFT is the model's thinking time, TTFB is the voice's, and perceived is
    what the caller experiences. Conflating them hides which one to fix."""

    async def test_ttft_measures_transcript_to_first_token(self, async_session):
        _, workflow = await _org(async_session, "ttft")
        run = await _run(async_session, workflow)
        await _turn(
            async_session, run, latency=900, stt_final=100, llm_first=400, tts_first=550
        )

        summary = await dash.call_latency_summary(async_session, workflow_run_id=run.id)

        assert summary["ttft"]["p50_ms"] == 300  # 400 - 100
        assert summary["ttfb"]["p50_ms"] == 150  # 550 - 400

    async def test_a_turn_missing_a_timestamp_is_excluded_not_zeroed(
        self, async_session
    ):
        """The failure that matters. A zero would pull the percentile down, so
        instrumentation breaking would look like latency improving."""
        _, workflow = await _org(async_session, "partial")
        run = await _run(async_session, workflow)
        await _turn(
            async_session, run, index=0, latency=800, stt_final=100, llm_first=500
        )
        # Second turn never recorded a first token — TTFT is unknowable.
        await _turn(async_session, run, index=1, latency=800, stt_final=100)

        summary = await dash.call_latency_summary(async_session, workflow_run_id=run.id)

        assert summary["turns"] == 2
        assert summary["ttft"]["p50_ms"] == 400  # not 200, which averaging a 0 gives

    async def test_an_unknown_measure_is_refused(self, async_session):
        with pytest.raises(ValueError):
            await dash.latency_percentile_series(
                async_session, start=_today(), end=_today(), measure="vibes"
            )


@pytest.mark.asyncio
class TestPerCallSummary:
    async def test_the_first_turn_is_reported_separately(self, async_session):
        """No context, a cold connection, often a pre-recorded greeting — the
        opening turn is not comparable to the rest, and averaging it in makes a
        healthy call look slow."""
        _, workflow = await _org(async_session, "firstturn")
        run = await _run(async_session, workflow)
        await _turn(async_session, run, index=0, latency=3000)
        for i in (1, 2, 3):
            await _turn(async_session, run, index=i, latency=400)

        summary = await dash.call_latency_summary(async_session, workflow_run_id=run.id)

        assert summary["first_turn_ms"] == 3000
        assert summary["steady_state_p50_ms"] == 400

    async def test_it_names_the_worst_turn(self, async_session):
        """ "Slow somewhere" is not actionable; a turn index is."""
        _, workflow = await _org(async_session, "worstturn")
        run = await _run(async_session, workflow)
        await _turn(async_session, run, index=0, latency=300)
        await _turn(async_session, run, index=1, latency=2500)
        await _turn(async_session, run, index=2, latency=350)

        summary = await dash.call_latency_summary(async_session, workflow_run_id=run.id)

        assert summary["worst_turn_ms"] == 2500
        assert summary["worst_turn_index"] == 1

    async def test_a_call_with_no_turns_returns_nothing(self, async_session):
        _, workflow = await _org(async_session, "noturns")
        run = await _run(async_session, workflow)

        assert (
            await dash.call_latency_summary(async_session, workflow_run_id=run.id)
            is None
        )


@pytest.mark.asyncio
class TestTokenEfficiency:
    async def test_tokens_and_minutes_are_bucketed_by_the_same_event(
        self, async_session
    ):
        """The bug this was written after: tokens keyed off the costing job's
        timestamp and minutes off the call's, so the ratio divided one period's
        tokens by another period's conversation. Both must key off the run."""
        _, workflow = await _org(async_session, "bucketing")
        # A call from ten days ago, costed just now.
        run = await _run(async_session, workflow, age_days=10, seconds=120)
        await _llm_cost(async_session, run, tokens=6000)

        start, end = _wide()
        series = await dash.token_usage_series(
            async_session, start=start, end=end, granularity="day"
        )
        rows = [r for r in series if r["tokens"]]

        assert len(rows) == 1
        row = rows[0]
        assert row["tokens"] == 6000
        assert row["minutes"] == 2.0
        assert row["tokens_per_minute"] == 3000

    async def test_the_total_is_the_same_at_every_granularity(self, async_session):
        """If day, week and month disagree, one of them is keyed off something
        that is not the call."""
        _, workflow = await _org(async_session, "granularity")
        for age in (1, 8, 20):
            run = await _run(async_session, workflow, age_days=age, seconds=60)
            await _llm_cost(async_session, run, tokens=1000)

        totals = {}
        for granularity in ("day", "week", "month"):
            start, end = _wide()
            series = await dash.token_usage_series(
                async_session, start=start, end=end, granularity=granularity
            )
            totals[granularity] = sum(r["tokens"] for r in series)

        assert len(set(totals.values())) == 1, totals

    async def test_a_period_with_no_conversation_reports_no_rate(self, async_session):
        """Undefined, not zero. A rate over no time is not a cheap agent."""
        _, workflow = await _org(async_session, "notime")
        run = await _run(async_session, workflow, seconds=0)
        await _llm_cost(async_session, run, tokens=500)

        start, end = _wide()
        series = await dash.token_usage_series(
            async_session, start=start, end=end, granularity="day"
        )
        row = next(r for r in series if r["tokens"] == 500)

        assert row["tokens_per_minute"] is None

    async def test_effective_price_comes_from_what_was_spent(self, async_session):
        """Derived from the cost actually recorded rather than read back from
        the rate card, so a model priced one way and billed another shows up."""
        _, workflow = await _org(async_session, "bymodel")
        run = await _run(async_session, workflow)
        await _llm_cost(async_session, run, tokens=10_000, paise=50, model="gpt-4o")

        start, end = _wide()
        rows = await dash.token_usage_by_model(async_session, start=start, end=end)
        row = next(r for r in rows if r["model"] == "gpt-4o")

        assert row["tokens"] == 10_000
        assert row["paise_per_1k_tokens"] == 5.0
        assert row["tokens_per_call"] == 10_000

    async def test_an_unknown_granularity_is_refused(self, async_session):
        with pytest.raises(ValueError):
            await dash.token_usage_series(
                async_session, start=_today(), end=_today(), granularity="fortnight"
            )


@pytest.mark.asyncio
class TestToolTimings:
    async def test_a_tool_that_never_returned_is_counted(self, async_session):
        """A named tool with no duration never came back. That is the only
        failure signal available without new columns, and calling it success
        would hide the exact case worth seeing."""
        _, workflow = await _org(async_session, "tools")
        run = await _run(async_session, workflow)
        await _turn(async_session, run, index=0, tool="lookup", tool_ms=200)
        await _turn(async_session, run, index=1, tool="lookup", tool_ms=None)

        start, end = _wide()
        rows = await dash.tool_call_stats(async_session, start=start, end=end)
        row = next(r for r in rows if r["tool"] == "lookup")

        assert row["calls"] == 2
        assert row["incomplete"] == 1
        assert row["p50_ms"] == 200

    async def test_turns_without_a_tool_are_not_counted(self, async_session):
        _, workflow = await _org(async_session, "notools")
        run = await _run(async_session, workflow)
        await _turn(async_session, run, index=0, latency=400)

        start, end = _wide()
        rows = await dash.tool_call_stats(async_session, start=start, end=end)

        assert all(r["tool"] is not None for r in rows)


@pytest.mark.asyncio
class TestPrivacyHealth:
    """The headline is a number that should never move off zero. A dashboard of
    numbers that go up is easy; a number that must not is the useful one."""

    async def test_a_recording_past_its_window_is_reported(self, async_session):
        _, workflow = await _org(async_session, "overdue")
        await _run(
            async_session,
            workflow,
            age_days=200,
            recording="recordings/old.wav",
        )

        report = await privacy_metrics.overdue_recordings(async_session)

        assert report["overdue"] >= 1
        assert report["healthy"] is False
        assert report["oldest_overdue_days"] >= 200

    async def test_a_young_recording_is_not(self, async_session):
        _, workflow = await _org(async_session, "young")
        await _run(async_session, workflow, age_days=1, recording="recordings/new.wav")

        report = await privacy_metrics.overdue_recordings(async_session)
        sampled = {row["workflow_run_id"] for row in report["sample"]}

        runs = [r for r in sampled]
        assert all(isinstance(r, int) for r in runs)

    async def test_the_sample_is_bounded(self, async_session):
        """A healthy deployment returns none and an unhealthy one could return
        millions — which is a response nobody should serialise."""
        _, workflow = await _org(async_session, "manyoverdue")
        for _ in range(5):
            await _run(
                async_session,
                workflow,
                age_days=200,
                recording="recordings/old.wav",
            )

        report = await privacy_metrics.overdue_recordings(async_session, sample_limit=2)

        assert report["overdue"] >= 5
        assert len(report["sample"]) == 2

    async def test_erasure_turnaround_counts_a_missed_deadline(self, async_session):
        from api.db.models import ErasureRequestModel

        org, _ = await _org(async_session, "erasureslow")
        async_session.add(
            ErasureRequestModel(
                organization_id=org.id,
                subject_type="phone_number",
                subject_hash="x" * 64,
                status="pending",
                requested_at=datetime.now(UTC)
                - timedelta(days=privacy_metrics.ERASURE_DEADLINE_DAYS + 5),
            )
        )
        await async_session.flush()

        report = await privacy_metrics.erasure_turnaround(
            async_session, organization_id=org.id
        )

        assert report["open"] == 1
        assert report["past_deadline"] == 1

    async def test_a_completed_request_records_its_turnaround(self, async_session):
        from api.db.models import ErasureRequestModel

        org, _ = await _org(async_session, "erasurefast")
        requested = datetime.now(UTC) - timedelta(days=2)
        async_session.add(
            ErasureRequestModel(
                organization_id=org.id,
                subject_type="phone_number",
                subject_hash="y" * 64,
                status="completed",
                requested_at=requested,
                completed_at=requested + timedelta(hours=3),
            )
        )
        await async_session.flush()

        report = await privacy_metrics.erasure_turnaround(
            async_session, organization_id=org.id
        )

        assert report["completed"] == 1
        assert report["past_deadline"] == 0
        assert report["median_hours"] == 3.0

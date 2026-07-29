"""Per-turn latency: collection in the pipeline, and persistence.

Nothing wrote call_turn_metrics outside the seed script, so the Latency screen
and the per-turn chart on the call receipt were empty in production.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from api.db.models import (
    CallTurnMetricModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing.turn_metrics import persist_turn_metrics
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from pipecat.metrics.metrics import TTFBMetricsData


class TestTurnCollection:
    def test_a_turn_carries_its_latency_and_stage_timings(self):
        agg = PipelineMetricsAggregator()

        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="DeepgramSTTService#0", value=0.130)
        )
        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="OpenAILLMService#0", value=0.205)
        )
        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="ElevenLabsTTSService#0", value=0.150)
        )
        agg.record_turn_latency(0.72)

        (turn,) = agg.get_turn_metrics()
        assert turn["turn_index"] == 0
        assert turn["latency_ms"] == 720
        # Laid end to end from the user stopping: STT 130, then LLM 205, then
        # TTS 150; audio out is the measured latency, so playback is the
        # residual 720 - 485 = 235.
        assert turn["t_user_stopped_ms"] == 0
        assert turn["t_stt_final_ms"] == 130
        assert turn["t_llm_first_token_ms"] == 335
        assert turn["t_tts_first_byte_ms"] == 485
        assert turn["t_audio_out_ms"] == 720

    def test_stage_timings_do_not_leak_between_turns(self):
        """Each turn closes its own stages; the next starts clean."""
        agg = PipelineMetricsAggregator()

        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="OpenAILLMService#0", value=0.200)
        )
        agg.record_turn_latency(0.5)
        # Second turn reports no stage metrics at all.
        agg.record_turn_latency(0.6)

        first, second = agg.get_turn_metrics()
        assert first["t_llm_first_token_ms"] == 200
        # Second turn measured no stages, so every mark collapses to the origin.
        assert second["t_llm_first_token_ms"] == 0
        assert second["t_tts_first_byte_ms"] == 0
        assert second["turn_index"] == 1

    def test_the_first_byte_of_a_turn_wins(self):
        """A later frame from the same stage belongs to the next response."""
        agg = PipelineMetricsAggregator()
        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="OpenAILLMService#0", value=0.200)
        )
        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="OpenAILLMService#0", value=0.900)
        )
        agg.record_turn_latency(0.5)

        (turn,) = agg.get_turn_metrics()
        assert turn["t_llm_first_token_ms"] == 200

    def test_an_unrecognised_processor_is_ignored_not_guessed(self):
        agg = PipelineMetricsAggregator()
        agg._handle_ttfb_metrics(
            TTFBMetricsData(processor="SomeOtherProcessor#0", value=0.400)
        )
        agg.record_turn_latency(0.5)

        (turn,) = agg.get_turn_metrics()
        # Ignored entirely: every stage mark stays at the origin.
        assert turn["t_stt_final_ms"] == 0
        assert turn["t_tts_first_byte_ms"] == 0
        assert turn["latency_ms"] == 500

    def test_a_call_with_no_turns_reports_none(self):
        assert PipelineMetricsAggregator().get_turn_metrics() == []


async def _run(async_session, slug: str) -> WorkflowRunModel:
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
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

    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=True,
        created_at=datetime.now(UTC),
    )
    async_session.add(run)
    await async_session.flush()
    return run


class TestPersistence:
    async def test_turns_are_written_in_order(self, async_session):
        run = await _run(async_session, "persist")
        written = await persist_turn_metrics(
            async_session,
            workflow_run_id=run.id,
            turns=[
                {"turn_index": 0, "latency_ms": 700, "t_llm_first_token_ms": 200},
                {"turn_index": 1, "latency_ms": 900, "t_llm_first_token_ms": 260},
            ],
        )
        await async_session.flush()

        assert written == 2
        rows = (
            await async_session.execute(
                select(CallTurnMetricModel)
                .where(CallTurnMetricModel.workflow_run_id == run.id)
                .order_by(CallTurnMetricModel.turn_index)
            )
        ).scalars().all()
        assert [r.latency_ms for r in rows] == [700, 900]
        assert [r.t_llm_first_token_ms for r in rows] == [200, 260]

    async def test_a_retried_teardown_replaces_rather_than_doubling(
        self, async_session
    ):
        """Double-counted turns would skew every percentile on the Latency page."""
        run = await _run(async_session, "retry")
        turns = [{"turn_index": 0, "latency_ms": 700}]

        await persist_turn_metrics(
            async_session, workflow_run_id=run.id, turns=turns
        )
        await async_session.flush()
        await persist_turn_metrics(
            async_session, workflow_run_id=run.id, turns=turns
        )
        await async_session.flush()

        rows = (
            await async_session.execute(
                select(CallTurnMetricModel).where(
                    CallTurnMetricModel.workflow_run_id == run.id
                )
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_a_missing_stage_stays_null_rather_than_zero(self, async_session):
        """Zero would read as 'instant' on the breakdown chart; NULL reads as
        'not measured', which is the truth."""
        run = await _run(async_session, "nulls")
        await persist_turn_metrics(
            async_session,
            workflow_run_id=run.id,
            turns=[{"turn_index": 0, "latency_ms": 700, "t_llm_first_token_ms": 200}],
        )
        await async_session.flush()

        row = (
            await async_session.execute(
                select(CallTurnMetricModel).where(
                    CallTurnMetricModel.workflow_run_id == run.id
                )
            )
        ).scalar_one()
        assert row.t_llm_first_token_ms == 200
        assert row.t_tts_first_byte_ms is None
        assert row.t_stt_final_ms is None

    async def test_no_turns_writes_nothing(self, async_session):
        run = await _run(async_session, "empty")
        assert (
            await persist_turn_metrics(
                async_session, workflow_run_id=run.id, turns=[]
            )
            == 0
        )

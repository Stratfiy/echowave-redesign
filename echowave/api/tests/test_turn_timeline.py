"""The turn timeline: what each stage of a turn actually cost.

The screen this feeds is the one somebody opens when latency regresses, to
decide which stage to go and fix. Every failure mode below produces a chart
that looks perfectly reasonable and points at the wrong stage.

**Endpointing reported as zero.** Nothing measured it, so the timeline pinned
the release mark to the moment the user stopped. The stage that is usually the
largest in the turn rendered as costing nothing, and its time reappeared in
whichever bucket was computed as the remainder.

**Stages laid end to end.** Chaining per-stage first-byte durations assumes the
stages ran back to back. Any gap between them is invisible, and lands in the
remainder along with everything else.

**The remainder called "playback".** It holds network transit, queueing and
those gaps. Named after one of its parts, it sends the reader to optimise audio
output when the time is somewhere else.
"""

from dataclasses import dataclass

import pytest

from api.services.pipecat.pipeline_metrics_aggregator import (
    PipelineMetricsAggregator,
)


@dataclass
class _Ttfb:
    processor: str
    start_time: float
    duration_secs: float
    model: str | None = None


@dataclass
class _Breakdown:
    """The shape pipecat's ``on_latency_breakdown`` hands us."""

    ttfb: list
    user_turn_start_time: float | None = None
    user_turn_secs: float | None = None
    text_aggregation: object | None = None
    function_calls: tuple = ()


def _aggregator() -> PipelineMetricsAggregator:
    # Constructed directly: the timeline logic is pure arithmetic over what the
    # observer reports, and it is worth testing without a pipeline around it.
    return PipelineMetricsAggregator()


ORIGIN = 1_000_000.0


def _turn(agg) -> dict:
    return agg._turns[-1]


class TestEndpointingIsMeasuredNotAssumed:
    def test_the_release_mark_is_null_until_the_breakdown_arrives(self):
        """Null, not zero. A zero would render as "endpointing is free" — both
        false and the opposite of actionable, since it is usually the largest
        stage in the turn."""
        agg = _aggregator()
        agg.record_turn_latency(1.2)

        assert _turn(agg)["t_endpoint_fired_ms"] is None

    def test_the_breakdown_supplies_the_release_mark(self):
        agg = _aggregator()
        agg._turn_stage_ttfb = {"stt_ms": 0.05, "llm_ms": 0.3, "tts_ms": 0.2}
        agg.record_turn_latency(1.5)
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=0.8)
        )

        assert _turn(agg)["t_endpoint_fired_ms"] == 800

    def test_a_release_mark_past_the_end_of_the_turn_is_clamped(self):
        """It cannot have been released after the bot was already talking. The
        alternative is every later stage going negative, which the chart would
        render as stages of impossible length."""
        agg = _aggregator()
        agg.record_turn_latency(1.0)
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=5.0)
        )

        assert _turn(agg)["t_endpoint_fired_ms"] == 1000

    def test_a_breakdown_without_a_turn_measurement_is_ignored(self):
        """The opening greeting emits a breakdown with no user turn behind it.
        Applying it would attribute the greeting's timings to whichever real
        turn happened to be last."""
        agg = _aggregator()
        agg.record_turn_latency(1.0)
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=0.4)
        )
        before = dict(_turn(agg))

        # A second breakdown with no turn closed between them — the greeting.
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=9.9)
        )
        assert _turn(agg) == before

    def test_a_breakdown_with_no_turn_at_all_does_not_raise(self):
        agg = _aggregator()
        agg.record_turn_breakdown(_Breakdown(ttfb=[], user_turn_secs=0.5))
        assert agg._turns == []


class TestStagesSitOnARealTimeline:
    def test_marks_come_from_wall_clock_starts_not_from_chaining(self):
        """The stages here have gaps between them: STT ends at 300ms, but the
        LLM does not begin until 500ms. Chaining durations would place the LLM
        mark at 300+400=700ms and lose the gap entirely."""
        agg = _aggregator()
        agg._turn_stage_ttfb = {"stt_ms": 0.3, "llm_ms": 0.4, "tts_ms": 0.1}
        agg.record_turn_latency(1.5)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                ttfb=[
                    _Ttfb("MySTTService", ORIGIN + 0.0, 0.3),
                    _Ttfb("MyLLMService", ORIGIN + 0.5, 0.4),
                    _Ttfb("MyTTSService", ORIGIN + 1.0, 0.1),
                ],
            )
        )
        turn = _turn(agg)

        assert turn["t_stt_final_ms"] == 300
        assert turn["t_llm_first_token_ms"] == 900
        assert turn["t_tts_first_byte_ms"] == 1100

    def test_the_gap_between_stages_is_visible_as_stage_duration(self):
        """The point of the real timeline: the 200ms of dead air between STT
        finishing and the LLM starting shows up somewhere a reader can see it,
        rather than silently inflating the remainder."""
        agg = _aggregator()
        agg.record_turn_latency(1.5)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                ttfb=[
                    _Ttfb("MySTTService", ORIGIN + 0.0, 0.3),
                    _Ttfb("MyLLMService", ORIGIN + 0.5, 0.4),
                ],
            )
        )
        turn = _turn(agg)

        assert turn["t_llm_first_token_ms"] - turn["t_stt_final_ms"] == 600

    def test_an_unrecognised_processor_leaves_its_stage_alone(self):
        """A wrong attribution is worse than a gap: a gap is visible on the
        chart and a misattributed number is not."""
        agg = _aggregator()
        agg._turn_stage_ttfb = {"llm_ms": 0.4}
        agg.record_turn_latency(1.0)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                ttfb=[_Ttfb("SomeMysteryProcessor", ORIGIN, 0.9)],
            )
        )

        # The provisional chained mark survives; the mystery processor's 900ms
        # was not adopted as anything.
        assert _turn(agg)["t_llm_first_token_ms"] == 400

    def test_the_earliest_of_several_processors_of_a_kind_wins(self):
        """The caller waits on the first byte, not the last one."""
        agg = _aggregator()
        agg.record_turn_latency(2.0)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                ttfb=[
                    _Ttfb("FallbackTTSService", ORIGIN + 1.2, 0.2),
                    _Ttfb("PrimaryTTSService", ORIGIN + 0.6, 0.1),
                ],
            )
        )

        assert _turn(agg)["t_tts_first_byte_ms"] == 700

    def test_marks_never_run_backwards(self):
        """Overlapping stages — a TTS that began streaming before the LLM had
        finished — cannot be drawn on a flat timeline. The later mark wins, so
        a stage can read as instant but never as negative."""
        agg = _aggregator()
        agg.record_turn_latency(2.0)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                ttfb=[
                    _Ttfb("MySTTService", ORIGIN + 0.0, 0.5),
                    # Finishes before STT did.
                    _Ttfb("MyLLMService", ORIGIN + 0.0, 0.2),
                    _Ttfb("MyTTSService", ORIGIN + 0.9, 0.1),
                ],
            )
        )
        turn = _turn(agg)

        marks = [
            turn["t_user_stopped_ms"],
            turn["t_endpoint_fired_ms"],
            turn["t_stt_final_ms"],
            turn["t_llm_first_token_ms"],
            turn["t_tts_first_byte_ms"],
            turn["t_audio_out_ms"],
        ]
        assert marks == sorted(marks), marks

    def test_every_stage_sits_after_the_turn_was_released(self):
        """Nothing downstream can have finished before the turn reached it. A
        stage mark earlier than the release is an artefact, and letting it
        through would make endpointing look like it overlapped the LLM."""
        agg = _aggregator()
        agg.record_turn_latency(2.0)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.9,
                ttfb=[_Ttfb("MySTTService", ORIGIN + 0.1, 0.1)],
            )
        )
        turn = _turn(agg)

        assert turn["t_stt_final_ms"] >= turn["t_endpoint_fired_ms"] == 900


class TestTheTurnStillAddsUp:
    def test_audio_out_is_never_before_the_last_stage(self):
        agg = _aggregator()
        agg.record_turn_latency(0.5)
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.0,
                # A TTS first byte later than the measured latency: the clamp
                # keeps the remainder at zero rather than negative.
                ttfb=[_Ttfb("MyTTSService", ORIGIN + 0.4, 0.4)],
            )
        )
        turn = _turn(agg)

        assert turn["t_audio_out_ms"] >= turn["t_tts_first_byte_ms"]

    def test_perceived_latency_is_untouched_by_the_breakdown(self):
        """It is the one figure the percentile queries run over, and it comes
        from the observer directly. The breakdown redistributes time within the
        turn; it must never change how long the turn was."""
        agg = _aggregator()
        agg.record_turn_latency(1.234)
        before = _turn(agg)["latency_ms"]
        agg.record_turn_breakdown(
            _Breakdown(
                user_turn_start_time=ORIGIN,
                user_turn_secs=0.5,
                ttfb=[_Ttfb("MyLLMService", ORIGIN + 0.6, 0.2)],
            )
        )

        assert _turn(agg)["latency_ms"] == before == 1234

    def test_token_usage_survives_the_amendment(self):
        agg = _aggregator()
        agg._turn_tokens = {"prompt_tokens": 900, "completion_tokens": 40}
        agg.record_turn_latency(1.0)
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=0.3)
        )
        turn = _turn(agg)

        assert turn["prompt_tokens"] == 900
        assert turn["completion_tokens"] == 40

    def test_turns_are_indexed_in_order(self):
        agg = _aggregator()
        for _ in range(3):
            agg.record_turn_latency(1.0)
            agg.record_turn_breakdown(
                _Breakdown(ttfb=[], user_turn_start_time=ORIGIN, user_turn_secs=0.2)
            )

        assert [t["turn_index"] for t in agg._turns] == [0, 1, 2]

    @pytest.mark.parametrize("missing", ["user_turn_secs", "user_turn_start_time"])
    def test_a_partial_breakdown_degrades_rather_than_raises(self, missing):
        """Losing latency instrumentation must never cost the call anything, so
        a breakdown missing either half leaves what it cannot improve."""
        agg = _aggregator()
        agg._turn_stage_ttfb = {"llm_ms": 0.4}
        agg.record_turn_latency(1.0)
        fields = {"user_turn_start_time": ORIGIN, "user_turn_secs": 0.3}
        fields[missing] = None
        agg.record_turn_breakdown(
            _Breakdown(ttfb=[_Ttfb("MyLLMService", ORIGIN + 0.5, 0.1)], **fields)
        )
        turn = _turn(agg)

        assert turn["latency_ms"] == 1000
        assert turn["t_audio_out_ms"] >= 1000


# ---------------------------------------------------------------------------
# The query side. The arithmetic above decides what gets stored; these decide
# what a NULL release mark does to the medians drawn from it.
# ---------------------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from api.db import billing_dashboard_client as dash  # noqa: E402
from api.db.models import (  # noqa: E402
    CallTurnMetricModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)


async def _run_row(async_session, slug: str):
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
    run = WorkflowRunModel(
        name=f"run-{slug}",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=datetime.now(UTC),
        billable_seconds=60,
        initial_context={},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()
    return run


async def _metric(
    async_session, run, *, index=0, endpoint=None, stt=None, latency=1000
):
    async_session.add(
        CallTurnMetricModel(
            workflow_run_id=run.id,
            turn_index=index,
            t_user_stopped_ms=0,
            t_endpoint_fired_ms=endpoint,
            t_stt_final_ms=stt,
            t_llm_first_token_ms=700,
            t_tts_first_byte_ms=850,
            t_audio_out_ms=latency,
            latency_ms=latency,
            created_at=datetime.now(UTC),
        )
    )
    await async_session.flush()


def _window():
    today = datetime.now(UTC).date()
    return today - timedelta(days=60), today + timedelta(days=1)


@pytest.mark.asyncio
class TestStageMedians:
    async def test_an_unmeasured_release_reports_none_not_zero(self, async_session):
        """The whole point. A window with no measured endpointing must say so —
        a zero would render as a stage that costs nothing, which is the most
        misleading thing this chart could claim."""
        run = await _run_row(async_session, "no-endpoint")
        await _metric(async_session, run, endpoint=None, stt=None)
        start, end = _window()

        medians = await dash.pipeline_stage_medians(async_session, start=start, end=end)
        assert medians["endpointing_ms"] is None
        assert medians["stt_ms"] is None

    async def test_unmeasured_turns_do_not_drag_the_median_down(self, async_session):
        """Turns without a release mark are excluded rather than counted as
        zero. Counting them would halve the median of the largest stage in the
        turn every time instrumentation lapsed."""
        run = await _run_row(async_session, "mixed")
        for i in range(3):
            await _metric(async_session, run, index=i, endpoint=600, stt=650)
        for i in range(3, 6):
            await _metric(async_session, run, index=i, endpoint=None, stt=None)
        start, end = _window()

        medians = await dash.pipeline_stage_medians(async_session, start=start, end=end)
        assert medians["endpointing_ms"] == 600

    async def test_coverage_says_how_much_of_the_window_was_measured(
        self, async_session
    ):
        """So a median over a handful of rows is not read as the whole picture
        — which matters most in exactly the window where instrumentation
        changed partway through."""
        run = await _run_row(async_session, "coverage")
        await _metric(async_session, run, index=0, endpoint=500, stt=550)
        await _metric(async_session, run, index=1, endpoint=None, stt=None)
        start, end = _window()

        medians = await dash.pipeline_stage_medians(async_session, start=start, end=end)
        assert medians["turns"] == 2
        assert medians["endpointing_measured_turns"] == 1
        assert medians["endpointing_coverage"] == 0.5

    async def test_the_trailing_bucket_is_not_called_playback(self, async_session):
        """It holds transport, network transit and queueing as well. Naming the
        remainder after one of its parts is how somebody optimises the wrong
        thing."""
        run = await _run_row(async_session, "residual")
        await _metric(async_session, run, endpoint=400, stt=500, latency=1200)
        start, end = _window()

        medians = await dash.pipeline_stage_medians(async_session, start=start, end=end)
        assert "playback_ms" not in medians
        assert medians["unattributed_ms"] == 350  # 1200 - 850

    async def test_an_empty_window_does_not_divide_by_zero(self, async_session):
        start = datetime.now(UTC).date() - timedelta(days=4000)
        medians = await dash.pipeline_stage_medians(
            async_session, start=start, end=start + timedelta(days=1)
        )
        assert medians["endpointing_coverage"] is None
        assert medians["endpointing_ms"] is None

"""Persisting a call receipt, and rolling calls up into dashboard figures."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.db.models import (
    CallCostItemModel,
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, CreditLedgerKind, RateUnit
from api.services.billing.costing import cost_workflow_run, current_balance_paise
from api.services.billing.rollup import ist_day_bounds_utc, refresh_daily_rollup
from api.services.billing.usage import (
    provider_from_processor,
    usage_items_from_usage_info,
)


async def _org_with_workflow(async_session, slug: str):
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
    return org, workflow


async def _run(
    async_session,
    workflow,
    *,
    usage_info=None,
    created_at=None,
    billable_seconds=None,
    is_completed=True,
    answered=True,
):
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        usage_info=usage_info or {},
        cost_info={},
        initial_context={},
        gathered_context={},
        is_completed=is_completed,
        billable_seconds=billable_seconds,
        created_at=created_at or datetime.now(UTC),
        answered_at=(created_at or datetime.now(UTC)) if answered else None,
    )
    async_session.add(run)
    await async_session.flush()
    return run


class TestUsageExtraction:
    @pytest.mark.parametrize(
        "processor,expected",
        [
            ("DeepgramSTTService", "deepgram"),
            ("DograhLLMService", "dograh"),
            ("SarvamTTSService", "sarvam"),
            ("DograhFluxSTTService", "dograh"),
            ("", "unknown"),
        ],
    )
    def test_provider_is_derived_from_the_processor_name(self, processor, expected):
        assert provider_from_processor(processor) == expected

    def test_llm_quantity_is_prompt_plus_completion_tokens(self):
        items = usage_items_from_usage_info(
            {
                "llm": {
                    "OpenAILLMService|||gpt-4": {
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                        "total_tokens": 1000,
                    }
                }
            }
        )
        assert len(items) == 1
        assert items[0].component is CostComponent.LLM
        assert items[0].provider == "openai"
        assert items[0].quantity == 1000

    def test_zero_usage_produces_no_line(self):
        items = usage_items_from_usage_info(
            {"tts": {"SarvamTTSService|||x": 0}, "stt": {"DeepgramSTTService|||y": 0}}
        )
        assert items == ()

    def test_malformed_usage_info_is_survivable(self):
        """Bad data must not take down post-call processing."""
        assert usage_items_from_usage_info(None) == ()
        assert usage_items_from_usage_info({"llm": "not-a-dict"}) == ()
        assert usage_items_from_usage_info({"tts": {"k": "abc"}}) == ()


@pytest.mark.asyncio
class TestCostWorkflowRun:
    async def test_persists_itemised_receipt_and_snapshot(self, async_session):
        _org, workflow = await _org_with_workflow(async_session, "receipt")
        async_session.add(
            ProviderRateModel(
                provider="openai",
                component=CostComponent.LLM.value,
                unit=RateUnit.THOUSAND_TOKENS.value,
                rate_mpaise=12_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "llm": {
                    "OpenAILLMService|||gpt-4": {
                        "prompt_tokens": 500,
                        "completion_tokens": 500,
                    }
                },
                "call_duration_seconds": 90,
            },
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert cost is not None
        assert cost.billable_minutes == 2  # ceil(90/60)
        assert cost.platform_fee_paise == 400  # default ₹2.00/min
        assert cost.total_provider_cost_paise == 12  # 1k tokens @ 12_000 mpaise
        assert cost.total_charged_paise == 412

        items = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert {i.component for i in items} == {"llm", "platform"}
        # The receipt reconciles against the stored total.
        assert sum(i.cost_paise for i in items) == run.total_charged_paise

        assert run.billable_seconds == 90
        assert run.platform_rate_mpaise_applied == 200_000
        assert run.total_provider_cost_paise == 12
        assert run.costed_at is not None

    async def test_byok_run_has_only_a_platform_line(self, async_session):
        """No provider rates configured means no pass-through cost."""
        _org, workflow = await _org_with_workflow(async_session, "byok")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert cost.total_provider_cost_paise == 0
        assert cost.total_charged_paise == 200
        items = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert [i.component for i in items] == ["platform"]

    async def test_uses_the_account_rate_in_force_at_call_time(self, async_session):
        """A rate raised after the call must not change what the call cost."""
        org, workflow = await _org_with_workflow(async_session, "historic")
        call_time = datetime(2026, 3, 1, tzinfo=UTC)
        async_session.add_all(
            [
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=100_000,  # ₹1.00/min back then
                    effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                    effective_to=datetime(2026, 6, 1, tzinfo=UTC),
                ),
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=300_000,  # ₹3.00/min now
                    effective_from=datetime(2026, 6, 1, tzinfo=UTC),
                ),
            ]
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={"call_duration_seconds": 60},
            created_at=call_time,
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert cost.platform_rate_mpaise == 100_000
        assert cost.total_charged_paise == 100

    async def test_costing_is_idempotent(self, async_session):
        """Post-call work is retried; a retry must not double-charge."""
        org, workflow = await _org_with_workflow(async_session, "idempotent")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )

        first = await cost_workflow_run(async_session, run.id)
        second = await cost_workflow_run(async_session, run.id)

        assert first is not None
        assert second is None  # already costed, skipped

        item_count = await async_session.scalar(
            select(func.count())
            .select_from(CallCostItemModel)
            .where(CallCostItemModel.workflow_run_id == run.id)
        )
        assert item_count == 1  # not duplicated

        ledger_count = await async_session.scalar(
            select(func.count())
            .select_from(CreditLedgerModel)
            .where(CreditLedgerModel.ref_id == str(run.id))
        )
        assert ledger_count == 1
        assert (
            await current_balance_paise(async_session, organization_id=org.id) == -200
        )

    async def test_recosting_replaces_rather_than_appends(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "recost")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )
        await cost_workflow_run(async_session, run.id)

        run.billable_seconds = 180
        await cost_workflow_run(async_session, run.id, recost=True)

        items = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert len(items) == 1
        assert run.total_charged_paise == 600  # 3 min @ ₹2.00

        # The ledger reflects the corrected figure, not both attempts.
        assert (
            await current_balance_paise(async_session, organization_id=org.id) == -600
        )

    async def test_usage_without_a_rate_is_reported_not_costed_at_zero(
        self, async_session
    ):
        _org, workflow = await _org_with_workflow(async_session, "unpriced")
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "tts": {"MysteryTTSService|||v1": 5000},
                "call_duration_seconds": 60,
            },
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert len(cost.uncosted) == 1
        assert cost.uncosted[0].provider == "mystery"
        assert cost.total_provider_cost_paise == 0

    async def test_debit_is_recorded_against_the_ledger(self, async_session):
        _org, workflow = await _org_with_workflow(async_session, "ledger")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 120}
        )

        await cost_workflow_run(async_session, run.id)

        entry = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.ref_id == str(run.id))
        )
        assert entry.kind == CreditLedgerKind.USAGE.value
        assert entry.delta_paise == -400
        assert entry.ref_type == "workflow_run"


class TestISTDayBounds:
    def test_an_ist_day_starts_at_1830_utc_the_previous_day(self):
        """IST is UTC+5:30, so an IST day is offset — not a UTC day.

        Bucketing by UTC day would split an Indian working day across two rows.
        """
        start, end = ist_day_bounds_utc(date(2026, 7, 29))
        assert start == datetime(2026, 7, 28, 18, 30, tzinfo=UTC)
        assert end == datetime(2026, 7, 29, 18, 30, tzinfo=UTC)


@pytest.mark.asyncio
class TestDailyRollup:
    async def test_aggregates_costed_calls_for_an_ist_day(self, async_session):
        org, workflow = await _org_with_workflow(async_session, "rollup")
        day = date(2026, 7, 29)
        start, _ = ist_day_bounds_utc(day)

        for _ in range(3):
            run = await _run(
                async_session,
                workflow,
                usage_info={"call_duration_seconds": 90},
                created_at=start + timedelta(hours=2),
            )
            await cost_workflow_run(async_session, run.id)

        await refresh_daily_rollup(async_session, day=day, organization_id=org.id)

        row = await async_session.scalar(
            select(DailyOrganizationRollupModel).where(
                DailyOrganizationRollupModel.organization_id == org.id,
                DailyOrganizationRollupModel.day == day,
            )
        )
        assert row.calls == 3
        assert row.answered_calls == 3
        assert row.completed_calls == 3
        assert row.billable_seconds == 270
        assert row.billable_minutes == 6  # ceil(90/60) per call, then summed
        assert row.charged_paise == 1200  # 3 * 2 min * ₹2.00
        assert row.margin_paise == row.charged_paise - row.provider_cost_paise

    async def test_a_late_evening_ist_call_lands_on_the_right_day(self, async_session):
        """23:00 IST is 17:30 UTC the same day — the easy case to get right.

        The hard case is the other side of the boundary, covered below.
        """
        org, workflow = await _org_with_workflow(async_session, "ist-evening")
        day = date(2026, 7, 29)
        _start, end = ist_day_bounds_utc(day)

        run = await _run(
            async_session,
            workflow,
            usage_info={"call_duration_seconds": 60},
            created_at=end - timedelta(minutes=30),  # 23:30 IST
        )
        await cost_workflow_run(async_session, run.id)

        await refresh_daily_rollup(async_session, day=day, organization_id=org.id)
        row = await async_session.scalar(
            select(DailyOrganizationRollupModel).where(
                DailyOrganizationRollupModel.organization_id == org.id,
                DailyOrganizationRollupModel.day == day,
            )
        )
        assert row is not None and row.calls == 1

    async def test_a_call_just_after_ist_midnight_rolls_to_the_next_day(
        self, async_session
    ):
        """00:30 IST on the 30th is 19:00 UTC on the 29th.

        Bucketing by UTC day would file this under the 29th. It belongs to the
        30th.
        """
        org, workflow = await _org_with_workflow(async_session, "ist-midnight")
        next_day = date(2026, 7, 30)
        next_start, _ = ist_day_bounds_utc(next_day)

        run = await _run(
            async_session,
            workflow,
            usage_info={"call_duration_seconds": 60},
            created_at=next_start + timedelta(minutes=30),
        )
        await cost_workflow_run(async_session, run.id)

        await refresh_daily_rollup(
            async_session, day=date(2026, 7, 29), organization_id=org.id
        )
        await refresh_daily_rollup(async_session, day=next_day, organization_id=org.id)

        rows = (
            await async_session.scalars(
                select(DailyOrganizationRollupModel).where(
                    DailyOrganizationRollupModel.organization_id == org.id
                )
            )
        ).all()
        by_day = {r.day: r.calls for r in rows}
        assert by_day.get(next_day) == 1
        assert by_day.get(date(2026, 7, 29), 0) == 0

    async def test_refresh_is_idempotent(self, async_session):
        """Re-running must converge, not double-count."""
        org, workflow = await _org_with_workflow(async_session, "rollup-idem")
        day = date(2026, 7, 29)
        start, _ = ist_day_bounds_utc(day)
        run = await _run(
            async_session,
            workflow,
            usage_info={"call_duration_seconds": 60},
            created_at=start + timedelta(hours=1),
        )
        await cost_workflow_run(async_session, run.id)

        await refresh_daily_rollup(async_session, day=day, organization_id=org.id)
        await refresh_daily_rollup(async_session, day=day, organization_id=org.id)

        count = await async_session.scalar(
            select(func.count())
            .select_from(DailyOrganizationRollupModel)
            .where(
                DailyOrganizationRollupModel.organization_id == org.id,
                DailyOrganizationRollupModel.day == day,
            )
        )
        assert count == 1

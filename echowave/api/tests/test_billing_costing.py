"""Persisting a call receipt, and rolling calls up into dashboard figures."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.constants import MANAGED_PROVIDER_MARKUP_BPS
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
from api.services.billing.money import round_half_up_div
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

    @pytest.mark.parametrize(
        "processor,expected",
        [
            ("OpenAILLMService#0", "openai"),
            ("DeepgramSTTService#1", "deepgram"),
            ("ElevenLabsTTSService#12", "elevenlabs"),
            ("DograhFluxSTTService#0", "dograh"),
            # A local subclass is still the vendor it wraps. Without its alias
            # this strips to "decibylsarvam", which has no rate on file -- and
            # an unpriced provider does not fail, it silently costs the call at
            # zero and reports margin at 100%.
            ("DecibylSarvamLLMService#0", "sarvam"),
        ],
    )
    def test_the_instance_suffix_pipecat_actually_emits_is_stripped(
        self, processor, expected
    ):
        """Regression: this is the form that reaches us in production.

        Pipecat names each processor "{ClassName}#{n}". Matching only the bare
        class name meant nothing stripped, every provider came out as
        "openaillmservice#0", no rate ever matched, and every receipt was
        uncosted with margin at 100%.
        """
        assert provider_from_processor(processor) == expected

    def test_a_realistic_usage_info_payload_costs(self):
        """End-to-end on the exact key shape the pipeline writes."""
        items = usage_items_from_usage_info(
            {
                "llm": {
                    "OpenAILLMService#0|||gpt-4o-mini": {
                        "prompt_tokens": 900,
                        "completion_tokens": 100,
                    }
                },
                "tts": {"ElevenLabsTTSService#0|||turbo-v2": 4000},
                "stt": {"DeepgramSTTService#0|||nova-2": 300},
                "telephony": {"twilio": 300},
                # Written by the status callback alongside the seconds, and
                # part of the realistic shape now: without it carriage reads as
                # the customer's own carrier and is deliberately not billed.
                "key_sources": {"telephony": "managed"},
                "call_duration_seconds": 300,
            }
        )
        by_component = {i.component.value: i for i in items}
        assert set(by_component) == {"llm", "tts", "stt", "telephony"}
        assert by_component["llm"].provider == "openai"
        assert by_component["llm"].model == "gpt-4o-mini"
        assert by_component["tts"].provider == "elevenlabs"
        assert by_component["stt"].provider == "deepgram"
        assert by_component["telephony"].provider == "twilio"

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

    def test_a_byok_component_produces_no_usage_item(self):
        """The core BYOK guarantee: measured usage exists, but the component
        it was measured on ran on the account's own key, so it must not
        become a billable line."""
        items = usage_items_from_usage_info(
            {
                "tts": {"SarvamTTSService|||bulbul:v3": 4000},
                "key_sources": {"tts": "byok"},
            }
        )
        assert items == ()

    def test_a_managed_component_is_unaffected_by_an_unrelated_byok_entry(self):
        items = usage_items_from_usage_info(
            {
                "llm": {
                    "OpenAILLMService|||gpt-4": {
                        "prompt_tokens": 100,
                        "completion_tokens": 100,
                    }
                },
                "key_sources": {"tts": "byok"},  # llm untouched
            }
        )
        assert len(items) == 1
        assert items[0].component is CostComponent.LLM

    def test_a_run_with_no_key_sources_at_all_is_charged_as_managed(self):
        """Calls costed before key_sources existed must keep being charged --
        an absent entry is not license to discount usage no one confirmed was
        BYOK."""
        items = usage_items_from_usage_info(
            {"tts": {"SarvamTTSService|||bulbul:v3": 4000}}
        )
        assert len(items) == 1

    def test_an_unrecognised_key_source_value_is_ignored(self):
        """Anything other than the two known values is treated as absent,
        not trusted -- future-proofing against a typo or a schema drift
        rather than silently granting a free ride."""
        from api.services.billing.usage import key_sources_from_usage_info

        assert key_sources_from_usage_info(
            {"key_sources": {"tts": "free", "llm": "managed"}}
        ) == {"llm": "managed"}


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
        # 90s is six whole 15s pulses, so we bill 1.5 minutes where a
        # whole-minute competitor bills two.
        assert cost.billed_seconds == 90
        assert cost.pulse_seconds == 15
        assert cost.platform_fee_paise == 288  # 1.5 min @ ₹1.92 ($0.02 at ₹96)
        # Model usage on our keys is sold at MANAGED_PROVIDER_MARKUP_BPS.
        # Derived from the setting rather than written out, so moving the
        # multiplier is a configuration change and not a test edit — the
        # property under test is that both figures survive on the receipt,
        # which is what keeps margin visible, not what the multiplier is today.
        assert cost.total_provider_cost_paise == 12  # 1k tokens @ 12_000 mpaise
        charged_llm = round_half_up_div(12 * MANAGED_PROVIDER_MARKUP_BPS, 10_000)
        assert cost.total_charged_paise == 288 + charged_llm

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
        assert run.billed_seconds == 90
        assert run.platform_rate_mpaise_applied == 192_000
        assert run.total_provider_cost_paise == 12
        assert run.costed_at is not None
        # The working behind the rupee figure, so the receipt can show a
        # customer quoted in dollars how it was arrived at.
        assert run.platform_rate_micros_usd_applied == 20_000
        assert run.usd_inr_paise_applied == 9_600
        assert run.pulse_seconds_applied == 15

    async def test_the_model_in_usage_info_selects_the_rate(self, async_session):
        """The same call costs less on a cheaper model.

        Before rates carried a model, both of these runs priced at the single
        openai rate, so a customer on gpt-4o-mini was billed as if they were on
        gpt-4o — roughly 17x too much on the inference line.
        """
        _org, workflow = await _org_with_workflow(async_session, "model-rate")
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="openai",
                    model="",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=12_000,
                    effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4o-mini",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=700,
                    effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                ),
            ]
        )

        def usage(model: str):
            return {
                "llm": {
                    f"OpenAILLMService|||{model}": {
                        "prompt_tokens": 5_000,
                        "completion_tokens": 5_000,
                    }
                },
                "call_duration_seconds": 60,
            }

        cheap_run = await _run(async_session, workflow, usage_info=usage("gpt-4o-mini"))
        dear_run = await _run(async_session, workflow, usage_info=usage("gpt-4o"))

        cheap = await cost_workflow_run(async_session, cheap_run.id)
        dear = await cost_workflow_run(async_session, dear_run.id)

        cheap_llm = next(l for l in cheap.line_items if l.component == "llm")
        dear_llm = next(l for l in dear.line_items if l.component == "llm")

        # 10k tokens: mini at 700 mpaise/1k = 7 paise; gpt-4o falls back to the
        # provider rate of 12000 mpaise/1k = 120 paise. Both are then sold at
        # the managed markup — the point of this test is which *rate* was
        # selected, which provider_cost_paise shows before any markup.
        assert cheap_llm.unit_rate_mpaise == 700
        assert cheap_llm.provider_cost_paise == 7
        assert cheap_llm.cost_paise == round_half_up_div(
            7 * MANAGED_PROVIDER_MARKUP_BPS, 10_000
        )
        assert cheap_llm.model == "gpt-4o-mini"

        assert dear_llm.unit_rate_mpaise == 12_000
        assert dear_llm.provider_cost_paise == 120
        assert dear_llm.cost_paise == round_half_up_div(
            120 * MANAGED_PROVIDER_MARKUP_BPS, 10_000
        )

        # And the receipt records which model it billed.
        rows = (
            (
                await async_session.execute(
                    select(CallCostItemModel.model).where(
                        CallCostItemModel.workflow_run_id == cheap_run.id,
                        CallCostItemModel.component == "llm",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == ["gpt-4o-mini"]

    async def test_telephony_seconds_are_costed(self, async_session):
        """Telephony was not measured at all before, so provider cost was
        understated and margin correspondingly overstated on every phone call."""
        _org, workflow = await _org_with_workflow(async_session, "telephony-cost")
        async_session.add(
            ProviderRateModel(
                provider="twilio",
                model="",
                component=CostComponent.TELEPHONY.value,
                unit=RateUnit.MINUTE.value,
                rate_mpaise=55_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "telephony": {"twilio": 120},
                "key_sources": {"telephony": "managed"},
                "call_duration_seconds": 120,
            },
        )

        cost = await cost_workflow_run(async_session, run.id)
        line = next(l for l in cost.line_items if l.component == "telephony")
        # 120s at 55000 mpaise/min = 2 min x 55 paise = 110 paise of carrier
        # cost, resold at the managed markup like every other provider line.
        assert line.provider_cost_paise == 110
        assert cost.total_provider_cost_paise == 110
        assert line.cost_paise > 110

    async def test_byok_run_has_only_a_platform_line(self, async_session):
        """No provider rates configured means no pass-through cost."""
        _org, workflow = await _org_with_workflow(async_session, "byok")
        run = await _run(
            async_session, workflow, usage_info={"call_duration_seconds": 60}
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert cost.total_provider_cost_paise == 0
        assert cost.total_charged_paise == 192  # 1 min @ ₹1.92
        items = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert [i.component for i in items] == ["platform"]

    async def test_a_component_run_on_the_accounts_own_key_is_not_charged(
        self, async_session
    ):
        """Real, measured usage on a BYOK component must produce no provider
        line -- the account already paid that vendor directly. Before
        key_sources existed, this case was priced exactly like a managed
        component and double-charged."""
        _org, workflow = await _org_with_workflow(async_session, "byok-measured")
        async_session.add(
            ProviderRateModel(
                provider="sarvam",
                component=CostComponent.TTS.value,
                unit=RateUnit.THOUSAND_CHARS.value,
                rate_mpaise=500_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "tts": {"SarvamTTSService|||bulbul:v3": 4000},
                "call_duration_seconds": 60,
                "key_sources": {"tts": "byok"},
            },
        )

        cost = await cost_workflow_run(async_session, run.id)

        assert cost.total_provider_cost_paise == 0
        assert cost.total_charged_paise == 192  # platform fee only, 1 min @ ₹1.92
        assert cost.uncosted == ()  # not "we don't know the price" -- not billed at all
        items = (
            await async_session.scalars(
                select(CallCostItemModel).where(
                    CallCostItemModel.workflow_run_id == run.id
                )
            )
        ).all()
        assert [i.component for i in items] == ["platform"]

    async def test_a_mixed_call_charges_only_the_managed_component(self, async_session):
        """One call, one BYOK component and one managed component -- only the
        managed one produces a pass-through line."""
        _org, workflow = await _org_with_workflow(async_session, "byok-mixed")
        async_session.add(
            ProviderRateModel(
                provider="openai",
                component=CostComponent.LLM.value,
                unit=RateUnit.THOUSAND_TOKENS.value,
                rate_mpaise=12_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        async_session.add(
            ProviderRateModel(
                provider="sarvam",
                component=CostComponent.STT.value,
                unit=RateUnit.MINUTE.value,
                rate_mpaise=100_000,
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
                "stt": {"SarvamSTTService|||saarika:v2.5": 60},
                "call_duration_seconds": 60,
                "key_sources": {"llm": "managed", "stt": "byok"},
            },
        )

        cost = await cost_workflow_run(async_session, run.id)

        components = {i.component for i in cost.line_items}
        assert components == {"llm", "platform"}
        assert cost.total_provider_cost_paise == 12  # only the LLM line

    async def test_carriage_on_our_own_carrier_is_charged(self, async_session):
        """Telephony *does* have a key-ownership concept, and this is the half
        we bill: a platform-managed configuration means Decibyl's carrier
        account served the call, so the minutes are ours to pass on.

        Model key sources do not enter into it — bringing your own language
        model says nothing about who owns the phone line."""
        _org, workflow = await _org_with_workflow(async_session, "byok-telephony")
        async_session.add(
            ProviderRateModel(
                provider="twilio",
                model="",
                component=CostComponent.TELEPHONY.value,
                unit=RateUnit.MINUTE.value,
                rate_mpaise=55_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "telephony": {"twilio": 60},
                "call_duration_seconds": 60,
                "key_sources": {
                    "llm": "byok",
                    "stt": "byok",
                    "tts": "byok",
                    "telephony": "managed",
                },
            },
        )

        cost = await cost_workflow_run(async_session, run.id)
        assert cost.total_provider_cost_paise == 55

    async def test_carriage_on_the_customers_own_carrier_is_not_charged(
        self, async_session
    ):
        """The double charge, in the form it reached production.

        ``is_platform_managed`` defaults to False, so the ordinary telephony
        configuration holds the customer's own Twilio credentials and Twilio
        has already billed them for these minutes. Adding a carriage line here
        charged for one phone call twice.
        """
        _org, workflow = await _org_with_workflow(async_session, "own-carrier")
        async_session.add(
            ProviderRateModel(
                provider="twilio",
                model="",
                component=CostComponent.TELEPHONY.value,
                unit=RateUnit.MINUTE.value,
                rate_mpaise=55_000,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        run = await _run(
            async_session,
            workflow,
            usage_info={
                "telephony": {"twilio": 60},
                "call_duration_seconds": 60,
                "key_sources": {"telephony": "byok"},
            },
        )

        cost = await cost_workflow_run(async_session, run.id)
        assert cost.total_provider_cost_paise == 0
        assert "telephony" not in {i.component for i in cost.line_items}

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
            await current_balance_paise(async_session, organization_id=org.id) == -192
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
        assert run.total_charged_paise == 576  # 3 min @ ₹1.92

        # The ledger reflects the corrected figure, not both attempts.
        assert (
            await current_balance_paise(async_session, organization_id=org.id) == -576
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
        assert entry.delta_paise == -384  # 2 min @ ₹1.92
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
        # Billed on pulses, not on those reported minutes: 3 * 90s * ₹1.92/min.
        assert row.charged_paise == 864
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

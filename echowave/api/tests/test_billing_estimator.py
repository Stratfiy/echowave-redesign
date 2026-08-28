"""The forward-looking per-minute estimate.

The estimate has to be reconcilable against the invoice it predicts, so it
prices from the same effective-dated rate rows the receipts use. What it
assumes — tokens and characters per minute — is measured from our own calls
where we have enough of them, and it says which basis it used.
"""

from datetime import UTC, datetime, timedelta

from api.db.models import (
    CallCostItemModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    ProviderRateModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import (
    RateSpec,
    UsageItem,
    compute_call_cost,
)
from api.services.billing.estimator import (
    DEFAULT_CHARACTERS_PER_MINUTE,
    DEFAULT_TOKENS_PER_MINUTE,
    MIN_CALLS_FOR_MEASURED_ASSUMPTION,
    SPOKEN_CHARACTERS_PER_SECOND,
    estimate_cost_per_minute,
)
from api.services.billing.markup import resolve_markup_bps
from api.services.billing.money import mpaise_to_micros_usd, round_half_up_div
from api.services.billing.realtime_pricing import CallShape

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=unit.value,
        rate_mpaise=mpaise,
        effective_from=LONG_AGO,
    )


class TestEstimate:
    async def test_itemises_the_stack_and_totals_its_own_lines(self, async_session):
        org = await _org(async_session, "itemised")
        async_session.add_all(
            [
                _rate("deepgram", CostComponent.STT, RateUnit.MINUTE, 25_000),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    12_000,
                    model="gpt-4o",
                ),
                _rate("sarvam", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 40_000),
                _rate("twilio", CostComponent.TELEPHONY, RateUnit.MINUTE, 55_000),
            ]
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider="deepgram",
            llm_provider="openai",
            llm_model="gpt-4o",
            tts_provider="sarvam",
            telephony_provider="twilio",
        )

        assert {line.component for line in est.lines} == {
            "stt",
            "llm",
            "tts",
            "telephony",
            "platform",
        }
        # Reconciles against its own breakdown, the same rule the receipt uses.
        assert est.total_paise_per_minute == sum(
            line.paise_per_minute for line in est.lines
        )
        assert (
            est.total_paise_per_minute
            == est.agent_paise_per_minute
            + est.telephony_paise_per_minute
            + est.platform_paise_per_minute
        )
        # A per-minute component is exact: 25000 mpaise/min = 25 paise of
        # vendor cost — and more than that to the customer, because STT is
        # bought on our key and carries the managed markup. Quoting the 25
        # would have promised a price the invoice does not honour.
        #
        # Derived from the multiple in force rather than written out: these
        # were hardcoded at the 1.4x figures and broke the moment the markup
        # moved. What is under test is that the markup is applied at all, not
        # what it happens to be today.
        markup_bps = await resolve_markup_bps(async_session)
        stt = next(l for l in est.lines if l.component == "stt")
        assert stt.paise_per_minute == round_half_up_div(25 * markup_bps, 10_000)
        assert stt.basis == "exact"

        # Telephony carries the markup too: 55000 mpaise/min is what the
        # carrier charges us, and carriage is bought and resold like any other
        # component.
        telephony = next(l for l in est.lines if l.component == "telephony")
        assert telephony.paise_per_minute == round_half_up_div(55 * markup_bps, 10_000)

    async def test_the_quote_is_what_the_receipt_will_charge(self, async_session):
        """The estimate and the invoice, computed the same way from the same rows.

        This is the property the module exists for, and it was false: the
        estimate applied no managed markup while every receipt applied 1.4x, so
        a customer choosing a stack was shown a number 40% under what their
        first bill would say for STT, LLM and TTS.
        """
        org = await _org(async_session, "reconciles")
        async_session.add(_rate("deepgram", CostComponent.STT, RateUnit.MINUTE, 25_000))
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session, organization_id=org.id, stt_provider="deepgram"
        )
        stt = next(l for l in est.lines if l.component == "stt")

        # One minute of the same usage, priced by the engine the receipt uses.
        charged = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=est.platform_paise_per_minute * 1_000,
            markup_bps=await resolve_markup_bps(async_session),
            pulse_seconds=60,
            usage=(
                UsageItem(
                    component=CostComponent.STT, provider="deepgram", quantity=60
                ),
            ),
            provider_rates={
                ("stt", "deepgram", ""): RateSpec(
                    rate_mpaise=25_000, unit=RateUnit.MINUTE
                )
            },
        )
        billed_stt = next(
            line for line in charged.line_items if line.component == "stt"
        )
        assert stt.paise_per_minute == billed_stt.cost_paise

    async def test_the_operator_view_can_ask_for_the_raw_vendor_cost(
        self, async_session
    ):
        """What the stack costs *us* is a different question, and still askable."""
        org = await _org(async_session, "unmarked")
        async_session.add(_rate("deepgram", CostComponent.STT, RateUnit.MINUTE, 25_000))
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider="deepgram",
            marked_up=False,
        )
        stt = next(l for l in est.lines if l.component == "stt")
        assert stt.paise_per_minute == 25

    async def test_a_cheaper_model_yields_a_cheaper_estimate(self, async_session):
        """The point of the whole feature: the price moves when you pick a
        different model."""
        org = await _org(async_session, "cheaper")
        async_session.add_all(
            [
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    12_000,
                    model="gpt-4o",
                ),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    700,
                    model="gpt-4o-mini",
                ),
            ]
        )
        await async_session.flush()

        dear = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="gpt-4o",
        )
        cheap = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        assert cheap.total_paise_per_minute < dear.total_paise_per_minute

    async def test_uses_the_accounts_own_platform_rate(self, async_session):
        """Two accounts on different negotiated rates must see their own."""
        default_org = await _org(async_session, "list-price")
        negotiated_org = await _org(async_session, "negotiated")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=negotiated_org.id,
                platform_rate_mpaise=50_000,  # Rs 0.50/min
                effective_from=LONG_AGO,
            )
        )
        await async_session.flush()

        listed = await estimate_cost_per_minute(
            async_session, organization_id=default_org.id
        )
        negotiated = await estimate_cost_per_minute(
            async_session, organization_id=negotiated_org.id
        )

        # The list price is $0.02/min, converted at the seeded ₹96.
        assert listed.platform_paise_per_minute == 192
        assert negotiated.platform_paise_per_minute == 50
        assert negotiated.total_paise_per_minute < listed.total_paise_per_minute

    async def test_the_estimate_is_reported_in_dollars_too(self, async_session):
        """The unit every competitor quotes, and the unit our own list price is
        fixed in — so the comparison can be made without a calculator."""
        org = await _org(async_session, "usd-estimate")

        estimate = await estimate_cost_per_minute(async_session, organization_id=org.id)

        assert estimate.usd_inr_paise == 9_600
        assert estimate.pulse_seconds == 15
        # Derived from the rupee total, so the two can never disagree.
        assert estimate.total_micros_usd_per_minute == mpaise_to_micros_usd(
            mpaise=estimate.total_paise_per_minute * 1000, usd_inr_paise=9_600
        )

    async def test_an_unpriced_component_is_reported_not_zeroed(self, async_session):
        """Same rule as a real call: silence about a missing rate would
        understate the estimate."""
        org = await _org(async_session, "unpriced")
        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            tts_provider="a-provider-we-never-priced",
        )
        assert est.unpriced == ("tts:a-provider-we-never-priced",)
        assert all(line.component != "tts" for line in est.lines)

    async def test_an_empty_stack_still_prices_the_platform_fee(self, async_session):
        org = await _org(async_session, "bare")
        est = await estimate_cost_per_minute(async_session, organization_id=org.id)
        assert [line.component for line in est.lines] == ["platform"]
        assert est.agent_paise_per_minute == 0
        assert est.telephony_paise_per_minute == 0


class TestTheCharacterAssumptionAgreesWithTheCallShape:
    """The synthesis assumption and ``CallShape`` describe the same minute.

    They did not. The constant read 2,300 characters a minute against a shape
    that gives the agent 27 seconds of it — 85 characters a second, six times
    human speech — because a per-minute figure had been multiplied by the turns
    in that minute. Synthesis is the largest component of an Indic call, so the
    Everyday card quoted about twice what the call then billed.
    """

    def test_it_is_the_agents_talk_time_at_a_human_speaking_rate(self):
        shape = CallShape()
        expected = round(60.0 * shape.agent_talk_share * SPOKEN_CHARACTERS_PER_SECOND)
        assert DEFAULT_CHARACTERS_PER_MINUTE == expected

    def test_the_agent_does_not_out_talk_the_call(self):
        """The failure the old number was: more speech than there is minute to
        speak it in. Anything at or above the wall clock is arithmetically
        impossible, whatever the shape says."""
        seconds_of_speech = DEFAULT_CHARACTERS_PER_MINUTE / SPOKEN_CHARACTERS_PER_SECOND
        assert seconds_of_speech < 60.0

    def test_a_talkier_shape_would_move_it(self):
        """Derived, not typed. If this ever stops holding, the constant has been
        pinned again and the drift it was extracted to prevent is back."""
        assert DEFAULT_CHARACTERS_PER_MINUTE == round(
            60.0 * CallShape().agent_talk_share * SPOKEN_CHARACTERS_PER_SECOND
        )


class TestConsumptionAssumption:
    async def test_falls_back_to_the_default_without_enough_history(
        self, async_session
    ):
        org = await _org(async_session, "no-history")
        async_session.add(
            _rate(
                "openai",
                CostComponent.LLM,
                RateUnit.THOUSAND_TOKENS,
                12_000,
                model="gpt-4o",
            )
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="gpt-4o",
        )
        llm = next(l for l in est.lines if l.component == "llm")
        assert llm.basis == "default"
        assert llm.units_per_minute == DEFAULT_TOKENS_PER_MINUTE

    async def test_measures_from_our_own_calls_once_there_are_enough(
        self, async_session
    ):
        """The differentiator: the assumption is our traffic, not a guess."""
        org = await _org(async_session, "measured")
        user = UserModel(provider_id="user-measured")
        async_session.add(user)
        await async_session.flush()
        workflow = WorkflowModel(
            name="wf",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        async_session.add(
            _rate(
                "openai",
                CostComponent.LLM,
                RateUnit.THOUSAND_TOKENS,
                12_000,
                model="gpt-4o",
            )
        )
        async_session.add(workflow)
        await async_session.flush()

        # Enough costed two-minute calls, each burning 6000 tokens — so 3000
        # tokens a minute, well clear of the 1400 default.
        for _ in range(MIN_CALLS_FOR_MEASURED_ASSUMPTION):
            run = WorkflowRunModel(
                name="run",
                workflow_id=workflow.id,
                mode="twilio",
                usage_info={},
                cost_info={},
                initial_context={},
                gathered_context={},
                is_completed=True,
                created_at=datetime.now(UTC) - timedelta(days=1),
                billable_seconds=120,
                costed_at=datetime.now(UTC),
            )
            async_session.add(run)
            await async_session.flush()
            async_session.add(
                CallCostItemModel(
                    workflow_run_id=run.id,
                    component=CostComponent.LLM.value,
                    provider="openai",
                    model="gpt-4o",
                    units=6_000,
                    unit_rate_mpaise=12_000,
                    cost_paise=72,
                )
            )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="gpt-4o",
        )
        llm = next(l for l in est.lines if l.component == "llm")
        assert llm.basis == "measured"
        assert llm.units_per_minute == 3_000

    async def test_a_model_priced_only_by_the_provider_fallback_says_so(
        self, async_session
    ):
        """Provenance matters: an estimate off a provider-wide rate is softer
        than one off a rate quoted for that exact model."""
        org = await _org(async_session, "fallback-flag")
        async_session.add(
            _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 12_000)
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="some-unpriced-model",
        )
        llm = next(l for l in est.lines if l.component == "llm")
        assert llm.rate_is_provider_fallback is True


class TestTheRouteReturnsWhatTheEstimateIsAbout:
    """The estimate exists to say two things a per-minute figure cannot: what
    it is in dollars, and that we do not bill by the minute at all.

    Both were computed and then dropped on the way out of the route, so the
    price badge rendered "billed in s pulses" — the differentiator printed as a
    typo — and the dollar line vanished. A test on the service alone could not
    see it: the estimate was right, the serialisation was lossy.
    """

    def test_every_field_the_price_badge_reads_is_serialised(self):
        import inspect

        from api.routes import cost_estimate

        source = inspect.getsource(cost_estimate.get_cost_per_minute)
        for field in (
            "total_paise_per_minute",
            "pulse_seconds",
            "total_micros_usd_per_minute",
            "unpriced",
        ):
            assert f'"{field}"' in source, f"route drops {field}"

    def test_the_estimate_carries_them_in_the_first_place(self):
        from api.services.billing.estimator import CostEstimate

        fields = CostEstimate.__dataclass_fields__
        assert "pulse_seconds" in fields
        assert "total_micros_usd_per_minute" in fields

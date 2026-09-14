"""Per-component multipliers, no platform fee, and the bundle rate by plan.

Arrival tests for KAN-54. Decided 14 Sept 2026 (KAN-47):

* carriage 1.15x, speech-to-text 1.3x, the model 2.0x, the voice 1.8x
  (premium voices 1.4x), embeddings at cost: one multiplier per component
  replaces the flat 1.7x on every managed line;
* there is no platform fee on a call: the default rate is zero, and the
  multipliers are the whole margin on an itemised call;
* a managed call is charged its bundle's flat rate, and that rate is by
  plan: 12 / 11 / 10 credits a minute on Business, Growth and Scale;
* the rate book shows each row's list cost, multiplier and sell price.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing import markup
from api.services.billing.cost_engine import (
    CREDIT_ROUNDING_PROVIDER,
    RateSpec,
    UsageItem,
    compute_call_cost,
)
from api.services.billing.credits import credits_for_charge
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE
from api.services.configuration import bundles


class TestTheMultipliers:
    def test_each_component_has_its_own(self):
        assert markup.default_markup_bps(CostComponent.TELEPHONY) == 11_500
        assert markup.default_markup_bps(CostComponent.STT) == 13_000
        assert markup.default_markup_bps(CostComponent.LLM) == 20_000
        assert markup.default_markup_bps(CostComponent.TTS, "sarvam") == 18_000
        assert markup.default_markup_bps(CostComponent.TTS, "smallest") == 18_000
        assert markup.default_markup_bps(CostComponent.EMBEDDING) == 10_000

    def test_premium_voices_carry_the_thinner_multiple(self):
        for provider in ("elevenlabs", "cartesia", "openai", "ElevenLabs"):
            assert markup.default_markup_bps(CostComponent.TTS, provider) == 14_000

    def test_an_unknown_component_is_at_cost_not_guessed(self):
        assert markup.default_markup_bps("translation") == 10_000

    def test_the_rate_book_lists_them(self):
        rows = markup.component_multipliers()
        by = {(r["component"], r["provider"]): r["markup_bps"] for r in rows}
        assert by[("llm", None)] == 20_000
        assert by[("tts", "elevenlabs")] == 14_000


class TestThereIsNoPlatformFee:
    def test_the_default_rate_is_zero(self):
        assert DEFAULT_PLATFORM_RATE_MPAISE == 0

    def test_an_itemised_call_is_the_marked_up_lines_and_nothing_else(self):
        """The study's Indic minute at list cost: carriage 38, STT 50, the
        model 26, the voice 122 paise. Sold at the multipliers that is 44 +
        65 + 52 + 220 = 381 paise, lifted to 400 (8 credits). No fee line."""
        usage = (
            UsageItem(CostComponent.TELEPHONY, "plivo", 60),
            UsageItem(CostComponent.STT, "sarvam", 60),
            UsageItem(CostComponent.LLM, "openai", 1_000, model="gpt-4.1-mini"),
            UsageItem(CostComponent.TTS, "sarvam", 850, model="bulbul:v3"),
        )
        rates = {
            ("telephony", "plivo", ""): RateSpec(
                rate_mpaise=38_000, unit=RateUnit.MINUTE
            ),
            ("stt", "sarvam", ""): RateSpec(rate_mpaise=50_000, unit=RateUnit.MINUTE),
            ("llm", "openai", "gpt-4.1-mini"): RateSpec(
                rate_mpaise=26_000, unit=RateUnit.THOUSAND_TOKENS
            ),
            ("tts", "sarvam", "bulbul:v3"): RateSpec(
                rate_mpaise=143_530, unit=RateUnit.THOUSAND_CHARS
            ),
        }
        overrides = {
            (
                item.component.value,
                item.provider,
                item.model,
            ): markup.default_markup_bps(item.component, item.provider)
            for item in usage
        }
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
            usage=usage,
            provider_rates=rates,
            markup_overrides=overrides,
        )
        assert cost.platform_fee_paise == 0
        charged = {
            line.component: line.cost_paise
            for line in cost.line_items
            if line.provider != CREDIT_ROUNDING_PROVIDER
        }
        assert charged["telephony"] == 44
        assert charged["stt"] == 65
        assert charged["llm"] == 52
        assert charged["tts"] == 220
        assert cost.total_provider_cost_paise == 38 + 50 + 26 + 122
        assert cost.total_charged_paise == 400
        assert cost.credits == 8


class TestTheBundleRateByPlan:
    def _row(self, **kw):
        base = dict(list_paise_per_minute=600, plan_rates={}, volume_tiers=[])
        base.update(kw)
        return SimpleNamespace(**base)

    def test_business_growth_and_scale_pay_twelve_eleven_ten_credits(self):
        row = self._row(plan_rates={"business": 600, "growth": 550, "scale": 500})
        assert (
            credits_for_charge(bundles.flat_rate_paise(row, plan_code="business")) == 12
        )
        assert (
            credits_for_charge(bundles.flat_rate_paise(row, plan_code="growth")) == 11
        )
        assert credits_for_charge(bundles.flat_rate_paise(row, plan_code="scale")) == 10

    def test_a_plan_with_no_figure_pays_the_list_price(self):
        row = self._row(plan_rates={"scale": 500})
        assert bundles.flat_rate_paise(row, plan_code="business") == 600
        assert bundles.flat_rate_paise(row) == 600

    def test_a_volume_tier_still_applies_beneath_the_plan_rate(self):
        row = self._row(
            plan_rates={"business": 600},
            volume_tiers=[{"min_minutes": 8_000, "paise_per_minute": 500}],
        )
        assert (
            bundles.flat_rate_paise(row, plan_code="business", period_minutes=0) == 600
        )
        assert (
            bundles.flat_rate_paise(row, plan_code="business", period_minutes=9_000)
            == 500
        )

    def test_an_itemised_bundle_has_no_rate(self):
        assert bundles.flat_rate_paise(self._row(list_paise_per_minute=None)) is None


@pytest.mark.asyncio
class TestTheLineResolver:
    async def test_the_default_applies_when_nothing_overrides(
        self, db_session, async_session
    ):
        from datetime import UTC, datetime

        bps = await markup.resolve_line_markup_bps(
            async_session,
            component=CostComponent.TTS,
            provider="elevenlabs",
            at=datetime.now(UTC),
            model="eleven_v3",
        )
        assert bps == 14_000

    async def test_a_per_model_override_still_wins(self, db_session, async_session):
        from datetime import UTC, datetime

        await markup.set_markup_override(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            model="gpt-4.1-mini",
            markup_bps=25_000,
            actor_user_id=None,
        )
        bps = await markup.resolve_line_markup_bps(
            async_session,
            component=CostComponent.LLM,
            provider="openai",
            at=datetime.now(UTC),
            model="gpt-4.1-mini",
        )
        assert bps == 25_000


@pytest.mark.asyncio
class TestAHoldStillHasAFloorWithoutAFee:
    async def test_a_new_account_is_measured_against_the_list_voice_minute(
        self, db_session, async_session
    ):
        """No history, no negotiated rate, no fee: the hold must not collapse
        to nothing. It stands on the list voice minute instead."""
        from api.db.models import OrganizationModel
        from api.services.billing import reservations

        org = OrganizationModel(provider_id="org-hold-floor", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        per_minute = await reservations.per_minute_paise(
            async_session, organization_id=org.id
        )
        assert (
            per_minute
            >= reservations.DEFAULT_LIST_VOICE_MINUTE_PAISE
            * reservations.NO_HISTORY_MULTIPLIER
        )

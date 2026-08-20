"""Why the price did not change, said out loud.

Two symptoms, one mechanism, and the mechanism is correct behaviour that
nothing surfaced:

* **An operator edits a provider-wide rate and nothing moves.** The resolver
  takes the most specific row, so a rate quoted for ``gpt-4.1-mini`` outranks
  the one quoted for ``openai``. Correct, and invisible — and the more named
  rows the price book grows, the more often the edit somebody makes is the one
  that does nothing.

* **A customer switches model and the price is identical.** A model we hold no
  rate for falls back to the provider-wide row, so two unpriced models on one
  provider quote the same number. Also correct, also invisible, and it reads
  as a broken calculator rather than a missing rate.

Both are now reported: ``shadowed_by`` from the operator's end,
``approximated`` from the customer's. Neither changes what is charged.

The third case here is the customer's own key. That already bills correctly —
a keyed component produces no provider line at all — but the estimate had no
way to say so, so a screen could not show the component at zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from api.db.models import OrganizationModel, ProviderRateModel
from api.enums import CostComponent, RateUnit
from api.services.billing import rate_card
from api.services.billing.estimator import estimate_cost_per_minute


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=unit.value,
        rate_mpaise=mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


class TestTheOperatorIsToldWhatOutranksTheirEdit:
    async def test_a_named_row_is_reported_as_shadowing_the_provider_row(
        self, db_session, async_session
    ):
        async_session.add_all(
            [
                _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    76,
                    model="gpt-4.1-mini",
                ),
            ]
        )
        await async_session.flush()
        rows = list(
            (
                await async_session.scalars(
                    select(ProviderRateModel).where(
                        ProviderRateModel.effective_to.is_(None)
                    )
                )
            ).all()
        )

        assert rate_card.shadowed_by(rows)[("llm", "openai")] == ["gpt-4.1-mini"]

    async def test_an_unshadowed_provider_row_reports_nothing(
        self, db_session, async_session
    ):
        """The case the operator assumes: the edit reaches every call."""
        async_session.add(_rate("deepgram", CostComponent.STT, RateUnit.MINUTE, 770))
        await async_session.flush()
        rows = list(
            (
                await async_session.scalars(
                    select(ProviderRateModel).where(
                        ProviderRateModel.effective_to.is_(None)
                    )
                )
            ).all()
        )

        assert rate_card.shadowed_by(rows)[("stt", "deepgram")] == []

    async def test_shadowing_does_not_leak_across_providers_or_components(
        self, db_session, async_session
    ):
        async_session.add_all(
            [
                _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285),
                _rate("openai", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 1500),
                _rate(
                    "openai",
                    CostComponent.TTS,
                    RateUnit.THOUSAND_CHARS,
                    3000,
                    model="tts-1-hd",
                ),
            ]
        )
        await async_session.flush()
        rows = list(
            (
                await async_session.scalars(
                    select(ProviderRateModel).where(
                        ProviderRateModel.effective_to.is_(None)
                    )
                )
            ).all()
        )
        shadow = rate_card.shadowed_by(rows)

        assert shadow[("tts", "openai")] == ["tts-1-hd"]
        assert shadow[("llm", "openai")] == []


class TestTheCustomerIsToldThePriceIsApproximate:
    async def test_an_unpriced_model_is_reported_as_approximated(
        self, db_session, async_session
    ):
        """The 'calculator is broken' report, in one field."""
        org = await _org(async_session, "approx")
        async_session.add(
            _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285)
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="some-model-with-no-row",
        )

        assert "llm" in est.approximated

    async def test_a_priced_model_is_not_reported_as_approximated(
        self, db_session, async_session
    ):
        org = await _org(async_session, "exact")
        async_session.add_all(
            [
                _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    76,
                    model="gpt-4.1-mini",
                ),
            ]
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="gpt-4.1-mini",
        )

        assert "llm" not in est.approximated

    async def test_two_unpriced_models_quote_the_same_number(
        self, db_session, async_session
    ):
        """The symptom itself, pinned. Both fall back to the provider row, so
        the price is identical — and both now say so rather than looking
        like a picker that ignored the change."""
        org = await _org(async_session, "same")
        async_session.add(
            _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285)
        )
        await async_session.flush()

        a = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="model-a",
        )
        b = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            llm_provider="openai",
            llm_model="model-b",
        )

        assert a.total_paise_per_minute == b.total_paise_per_minute
        assert a.approximated == b.approximated == ("llm",)


class TestACustomerKeyedSlotCostsThemNothing:
    async def test_a_keyed_component_prices_at_zero(self, db_session, async_session):
        org = await _org(async_session, "keyed")
        async_session.add(
            _rate("elevenlabs", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 5000)
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            tts_provider="elevenlabs",
            customer_keyed={"tts"},
        )

        tts = next(line for line in est.lines if line.component == "tts")
        assert tts.paise_per_minute == 0
        assert tts.customer_keyed is True
        assert est.customer_keyed == ("tts",)

    async def test_the_component_is_shown_rather_than_dropped(
        self, db_session, async_session
    ):
        """A row that vanishes when a key is added reads as a bug. A row that
        says nil reads as the answer."""
        org = await _org(async_session, "shown")
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            tts_provider="some-vendor-we-never-priced",
            customer_keyed={"tts"},
        )

        assert [line.component for line in est.lines if line.component == "tts"]
        assert not est.unpriced

    async def test_an_unkeyed_component_is_unaffected(self, db_session, async_session):
        org = await _org(async_session, "mixed")
        async_session.add_all(
            [
                _rate("elevenlabs", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 5000),
                _rate("deepgram", CostComponent.STT, RateUnit.MINUTE, 770),
            ]
        )
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            tts_provider="elevenlabs",
            stt_provider="deepgram",
            customer_keyed={"tts"},
        )

        stt = next(line for line in est.lines if line.component == "stt")
        tts = next(line for line in est.lines if line.component == "tts")
        assert tts.paise_per_minute == 0
        assert stt.paise_per_minute > 0

    async def test_the_platform_fee_is_still_charged(self, db_session, async_session):
        """Bringing every key does not make the call free. The platform fee is
        ours, and on a BYOK call it is the whole of our margin."""
        org = await _org(async_session, "still-billed")
        await async_session.flush()

        est = await estimate_cost_per_minute(
            async_session,
            organization_id=org.id,
            stt_provider="deepgram",
            llm_provider="openai",
            tts_provider="elevenlabs",
            customer_keyed={"stt", "llm", "tts"},
        )

        assert est.platform_paise_per_minute > 0
        assert est.total_paise_per_minute == est.platform_paise_per_minute


class TestSpeechToSpeechIsQuotedAtWhatItCosts:
    """Two independent pricing paths, made to agree.

    ``realtime_pricing`` models a speech-to-speech session properly — audio
    tokens, and the conversation re-sent on every turn as context grows. The
    estimator prices from the rate card at an assumed tokens-per-minute, and
    that assumption was the *text* one: 1,400 tokens a minute against a real
    4,815. It under-quoted an OpenAI realtime minute by 3.4x, on the exact
    bundle we are about to offer as a one-click choice.

    Billing was never wrong — a real call records real token counts — but a
    customer choosing speech-to-speech was shown a quarter of its price.
    """

    def test_the_two_paths_agree_per_model(self):
        from api.services.billing.default_rates import DEFAULT_RATES
        from api.services.billing.estimator import REALTIME_TOKENS_PER_MINUTE
        from api.services.billing.realtime_pricing import (
            CallShape,
            estimate_realtime_call,
        )
        from api.services.billing.realtime_rates import REALTIME_PRICES
        from api.services.billing.estimator import _REALTIME_RATE_CARD_NAMES

        rates = {r.provider: r.usd_per_unit for r in DEFAULT_RATES}
        for price in REALTIME_PRICES:
            name = _REALTIME_RATE_CARD_NAMES.get(price.provider)
            if not name or name not in rates:
                continue
            modelled = estimate_realtime_call(price, CallShape())
            from_module = modelled.micros_usd_per_minute / 1e6
            from_card = rates[name] * REALTIME_TOKENS_PER_MINUTE[name] / 1000

            # Within a tenth of a percent: the two round differently, they do
            # not disagree.
            assert abs(from_module - from_card) / from_module < 0.001, (
                f"{price.label}: rate card says ${from_card:.4f}/min, the "
                f"realtime model says ${from_module:.4f}/min"
            )

    def test_the_assumption_is_per_model_not_one_number(self):
        """Gemini tokenises audio about three times denser than OpenAI, so a
        single realtime constant would be wrong for one vendor whichever value
        it took."""
        from api.services.billing.estimator import REALTIME_TOKENS_PER_MINUTE as T

        assert T["decibylgeminilive"] != T["decibylopenairealtime"]
        assert min(T.values()) > 0

    def test_an_ordinary_language_model_keeps_the_text_assumption(self):
        from api.services.billing.estimator import (
            DEFAULT_TOKENS_PER_MINUTE,
            REALTIME_TOKENS_PER_MINUTE,
        )

        assert "openai" not in REALTIME_TOKENS_PER_MINUTE
        assert DEFAULT_TOKENS_PER_MINUTE == 1_400

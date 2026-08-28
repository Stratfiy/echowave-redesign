"""Speech-to-speech has two names, and everything has to agree which one prices.

A configuration names `openai_realtime`. The pipeline runs
`DecibylOpenAIRealtimeLLMService`, so what `provider_from_processor` derives —
and therefore what lands in `call_cost_items`, and what the rate card is keyed
by — is `decibylopenairealtime`.

Costing only ever sees the second name, so it was always right. Everything that
prices a stack *before* it runs starts from the first, and two lookups sitting
one line apart in the estimator disagreed about which to use: the rate was
resolved under the configuration name (missing every time, so realtime reported
as unpriced) while the consumption assumption was keyed under the rate-card name
(also missing, so it fell back to the *text* figure — 1,400 tokens a minute
against an audio reality of 4,815 for OpenAI and 14,355 for Gemini).

The two failures cancelled into something that looked merely incomplete. These
tests pin the translation itself, and pin it in both places at once.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.db.models import ProviderRateModel
from api.services.billing.estimator import (
    DEFAULT_TOKENS_PER_MINUTE,
    REALTIME_TOKENS_PER_MINUTE,
    estimate_cost_per_minute,
)
from api.services.billing.realtime_rates import REALTIME_PRICES, price_for
from api.services.billing.usage import provider_from_processor, rate_card_provider
from api.services.configuration import managed_tiers

#: Every realtime tier a customer can pick, as (config provider, rate-card name).
REALTIME_PAIRS = [
    ("openai_realtime", "decibylopenairealtime"),
    ("azure_realtime", "decibylazurerealtime"),
    ("google_realtime", "decibylgeminilive"),
    ("google_vertex_realtime", "decibylgeminilivevertex"),
]


class TestTheTwoNamesAreOneTranslation:
    @pytest.mark.parametrize(("config_name", "rate_card_name"), REALTIME_PAIRS)
    def test_a_realtime_config_name_maps_to_its_rate_card_name(
        self, config_name, rate_card_name
    ):
        assert rate_card_provider(config_name) == rate_card_name

    @pytest.mark.parametrize("ordinary", ["openai", "sarvam", "deepgram", "google"])
    def test_an_ordinary_vendor_is_left_alone(self, ordinary):
        """Only the realtime services carry the split. Translating anything
        else would break every non-realtime rate lookup at once."""
        assert rate_card_provider(ordinary) == ordinary

    def test_the_translation_agrees_with_what_costing_actually_records(self):
        """The load-bearing assertion.

        `rate_card_provider` claims to produce the name costing uses. Costing
        derives it from the pipecat service class instead. If these two ever
        disagree the quote prices one row and the invoice writes another, which
        is the whole failure — so derive it the way costing does and compare.
        """
        for config_name, rate_card_name in REALTIME_PAIRS:
            assert rate_card_provider(config_name) == rate_card_name
        assert provider_from_processor("DecibylOpenAIRealtimeLLMService#0") == (
            rate_card_provider("openai_realtime")
        )


class TestTheConsumptionAssumptionIsFound:
    @pytest.mark.parametrize(("config_name", "_rate_card_name"), REALTIME_PAIRS)
    def test_a_realtime_stack_never_falls_back_to_the_text_assumption(
        self, config_name, _rate_card_name
    ):
        """An audio minute is three to ten times a text minute. Falling back
        here is not a rounding error, it is a different product."""
        key = rate_card_provider(config_name)
        assert key in REALTIME_TOKENS_PER_MINUTE, (
            f"{config_name} has no audio token assumption under {key}"
        )
        assert REALTIME_TOKENS_PER_MINUTE[key] > DEFAULT_TOKENS_PER_MINUTE

    def test_gemini_tokenises_denser_than_openai(self):
        """Guards the table against collapsing to one number. Gemini bills
        about three times the tokens for the same second of audio, which is why
        a single realtime constant would be wrong for somebody whatever it was."""
        assert (
            REALTIME_TOKENS_PER_MINUTE[rate_card_provider("google_realtime")]
            > REALTIME_TOKENS_PER_MINUTE[rate_card_provider("openai_realtime")]
        )


class TestTheMiniIsNotPricedAsTheFlagship:
    def test_the_mini_has_a_price_of_its_own(self):
        flagship = price_for("openai_realtime", "gpt-realtime-2")
        mini = price_for("openai_realtime", "gpt-realtime-2.1-mini")

        assert mini is not None
        assert mini.model == "gpt-realtime-2.1-mini"
        assert mini.audio_input_usd_per_million < flagship.audio_input_usd_per_million
        assert mini.audio_output_usd_per_million < flagship.audio_output_usd_per_million

    def test_an_unknown_realtime_model_still_falls_back_to_its_provider(self):
        """The fallback is deliberate — a model we hold no specific price for is
        better estimated at its provider's rate than not estimated at all."""
        assert price_for("openai_realtime", "gpt-realtime-9-unreleased") is not None

    def test_every_price_book_entry_maps_to_a_rate_card_name(self):
        """A price nobody can look up is a price that does not exist. This is
        the check that would have caught the original defect."""
        for price in REALTIME_PRICES:
            assert rate_card_provider(price.provider) != price.provider, (
                f"{price.provider} is not being translated to a rate-card name"
            )


def _rate(provider, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component="llm",
        unit="1k_tokens",
        rate_mpaise=mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


class TestTheEstimatorFindsTheRealtimeRate:
    async def test_a_realtime_tier_is_priced_rather_than_reported_unpriced(
        self, db_session, async_session
    ):
        """The symptom the operator saw: "no rate on the rate card" for a model
        whose rate was on file the whole time, under the billing name."""
        async_session.add(_rate("decibylopenairealtime", 35_000))
        await async_session.flush()

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=None,
            llm_provider="openai_realtime",
            llm_model="gpt-realtime-2",
        )

        assert estimate.unpriced == ()
        llm = next(line for line in estimate.lines if line.component == "llm")
        assert llm.unit_rate_mpaise == 35_000
        # Priced on the audio assumption, not the text one.
        assert llm.units_per_minute > DEFAULT_TOKENS_PER_MINUTE

    async def test_the_quote_names_the_vendor_the_caller_asked_about(
        self, db_session, async_session
    ):
        """Priced under the billing name, reported under the configured one.
        A screen answering "openai_realtime" with "decibylopenairealtime" is
        answering a question nobody asked."""
        async_session.add(_rate("decibylopenairealtime", 35_000))
        await async_session.flush()

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=None,
            llm_provider="openai_realtime",
            llm_model="gpt-realtime-2",
        )

        llm = next(line for line in estimate.lines if line.component == "llm")
        assert llm.provider == "openai_realtime"

    async def test_an_ordinary_llm_is_unaffected(self, db_session, async_session):
        """The translation must not touch anything else — every non-realtime
        rate lookup in the product goes through this same line."""
        async_session.add(_rate("openai", 7_296, model="gpt-4.1-mini"))
        await async_session.flush()

        estimate = await estimate_cost_per_minute(
            async_session,
            organization_id=None,
            llm_provider="openai",
            llm_model="gpt-4.1-mini",
        )

        llm = next(line for line in estimate.lines if line.component == "llm")
        assert llm.provider == "openai"
        assert llm.unit_rate_mpaise == 7_296
        assert llm.units_per_minute == DEFAULT_TOKENS_PER_MINUTE


class TestEveryManagedRealtimeTierResolvesToSomethingPriceable:
    def test_no_tier_points_at_a_provider_with_no_price_book_entry(self):
        """The tier table and the price book are edited by different people at
        different times. This is the seam."""
        for tier in managed_tiers.REALTIME_TIERS:
            upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, tier)
            assert price_for(upstream.provider, upstream.model) is not None, (
                f"realtime tier {tier!r} resolves to {upstream.provider}/"
                f"{upstream.model}, which has no price book entry"
            )

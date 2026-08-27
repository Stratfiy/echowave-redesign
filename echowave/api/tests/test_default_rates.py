"""The starter price book.

An empty rate card does not look empty — it looks like a working product
quoting the platform fee and reporting 100% margin, because a call with no rate
on file is recorded as *uncosted* rather than free. That is the failure this
guards against, so the tests are about the price book being complete and
coherent rather than about any particular number being right. The numbers are
list prices and are expected to be wrong; the operator corrects them.
"""

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing.default_rates import (
    DEFAULT_RATES,
    LLM_INPUT_SHARE,
    REFERENCE_USD_INR,
    SEED_NOTE,
    _blend,
    _inr,
    usd_to_mpaise,
)
from api.services.billing.money import DEFAULT_USD_INR_PAISE


class TestTheUnitMatchesTheComponent:
    """A rate quoted in the wrong unit is not a wrong price, it is a wrong
    number by three orders of magnitude — priced per minute when the engine
    multiplies by characters."""

    EXPECTED = {
        CostComponent.LLM: RateUnit.THOUSAND_TOKENS,
        CostComponent.TTS: RateUnit.THOUSAND_CHARS,
        CostComponent.STT: RateUnit.MINUTE,
        CostComponent.TELEPHONY: RateUnit.MINUTE,
    }

    def test_every_rate_uses_its_components_unit(self):
        for rate in DEFAULT_RATES:
            assert rate.unit == self.EXPECTED[rate.component], (
                f"{rate.provider}/{rate.component.value} is quoted per "
                f"{rate.unit.value}"
            )


class TestTheBookIsCoherent:
    def test_no_duplicate_keys(self):
        """Two rows for one (provider, model, component) would make which one
        applies depend on insertion order."""
        keys = [(r.provider, r.model, r.component) for r in DEFAULT_RATES]
        assert len(keys) == len(set(keys))

    def test_every_provider_has_a_fallback(self):
        """A model-specific rate with no provider-wide row means an unlisted
        model from that vendor prices at nothing at all."""
        by_component: dict[tuple[str, CostComponent], set[str]] = {}
        for rate in DEFAULT_RATES:
            by_component.setdefault((rate.provider, rate.component), set()).add(
                rate.model
            )
        for (provider, component), models in by_component.items():
            assert "" in models, (
                f"{provider}/{component.value} has model-specific rates but no "
                "provider-wide fallback"
            )

    def test_no_rate_is_zero(self):
        """Zero is indistinguishable from unpriced downstream, and reads as a
        free provider."""
        for rate in DEFAULT_RATES:
            assert rate.usd_per_unit > 0, f"{rate.provider} is priced at zero"

    def test_the_fallback_is_not_more_expensive_than_a_named_model(self):
        """An unpriced model should under-report rather than over-report — a
        surprise on the invoice ought to be a pleasant one."""
        for provider, component in {(r.provider, r.component) for r in DEFAULT_RATES}:
            rows = [
                r
                for r in DEFAULT_RATES
                if r.provider == provider and r.component == component
            ]
            fallback = next((r for r in rows if r.model == ""), None)
            named = [r for r in rows if r.model]
            if fallback and named:
                assert fallback.usd_per_unit <= max(r.usd_per_unit for r in named)


class TestBlending:
    """The schema carries one rate per model; vendors quote two. The blend is
    an assumption and has to be a stated one."""

    def test_it_lands_between_the_two_prices(self):
        blended = _blend(1.0, 10.0) * 1000  # back to per-million
        assert 1.0 < blended < 10.0

    def test_it_weights_input_as_documented(self):
        blended = _blend(0.0, 10.0) * 1000
        assert blended == pytest.approx(10.0 * (1 - LLM_INPUT_SHARE))

    def test_an_input_heavy_assumption_is_cheaper_than_an_even_split(self):
        """Voice agents resend the transcript each turn. If that assumption is
        ever flipped, the price book gets more expensive across the board and
        somebody should notice."""
        assert LLM_INPUT_SHARE > 0.5


class TestConversion:
    def test_dollars_become_millipaise(self):
        # $1 at ₹80 is ₹80 = 8,000 paise = 8,000,000 millipaise.
        assert usd_to_mpaise(1.0, usd_inr=80.0) == 8_000_000

    def test_a_fraction_of_a_cent_survives(self):
        """Deepgram-scale prices are $0.0043/min. Truncating instead of
        rounding here would quietly price transcription at zero."""
        assert usd_to_mpaise(0.0043, usd_inr=96.0) == 41_280

    def test_rounding_is_half_up(self):
        assert usd_to_mpaise(0.000005, usd_inr=1.0) == 1

    def test_it_never_returns_zero_for_a_real_price(self):
        for rate in DEFAULT_RATES:
            assert usd_to_mpaise(rate.usd_per_unit, usd_inr=80.0) > 0


class TestRupeeQuotedVendors:
    """Sarvam publishes in ₹ and this file is in USD, so those rows are a round
    trip. It is only lossless while the two reference rates agree."""

    def test_the_reference_rate_matches_the_platform_default(self):
        """If these drift, a rupee price seeds as a different rupee price and
        nothing anywhere says so — the row still looks like a published figure."""
        assert REFERENCE_USD_INR == DEFAULT_USD_INR_PAISE / 100

    def test_a_rupee_price_round_trips_to_itself(self):
        # ₹3.00 per 1k characters is 300 paise is 300,000 millipaise.
        assert usd_to_mpaise(_inr(3.00), usd_inr=REFERENCE_USD_INR) == 300_000

    def test_sarvam_tts_fallback_is_the_generation_the_managed_tier_runs(self):
        """Bulbul v2 and v3 are published at ₹15 and ₹30 per 10k characters, so
        which one the provider-wide row quotes changes synthesis cost by 2×.

        It is v2, because ``managed_tiers`` resolves the default TTS tier to
        ``bulbul:v2``. Quoting v3 — as this row previously did — priced every
        managed call at twice the synthesis it actually bought, in the component
        large enough to decide whether an Indic deal is profitable.

        Whenever the managed tier moves to v3, this row moves with it.
        """
        row = next(
            r
            for r in DEFAULT_RATES
            if r.provider == "sarvam"
            and r.component == CostComponent.TTS
            and not r.model
        )
        mpaise = usd_to_mpaise(row.usd_per_unit, usd_inr=REFERENCE_USD_INR)
        assert mpaise == 150_000  # ₹1.50 per 1k chars = ₹15 per 10k

    def test_both_bulbul_generations_are_priced(self):
        """The 2× gap between them is why an explicit row exists for each: a
        customer who switches generation should see the cost move."""
        by_model = {
            r.model: usd_to_mpaise(r.usd_per_unit, usd_inr=REFERENCE_USD_INR)
            for r in DEFAULT_RATES
            if r.provider == "sarvam" and r.component == CostComponent.TTS
        }
        assert by_model["bulbul:v2"] == 150_000
        assert by_model["bulbul:v3"] == 300_000

    def test_sarvam_stt_is_the_published_thirty_rupees_an_hour(self):
        row = next(
            r
            for r in DEFAULT_RATES
            if r.provider == "sarvam" and r.component == CostComponent.STT
        )
        mpaise = usd_to_mpaise(row.usd_per_unit, usd_inr=REFERENCE_USD_INR)
        assert mpaise == 50_000  # ₹0.50 per minute

    def test_saaras_v3_is_priced_the_same_as_saarika(self):
        """Found missing on the first real call to use it, three weeks after
        it shipped: Sarvam's own pricing page has no separate figure for
        saaras:v3 -- it is the same real-time-streaming STT tier as
        saarika:v2.5, priced identically, so this is not a second number to
        keep in sync -- it is one fact Sarvam publishes once.
        """
        row = next(
            r
            for r in DEFAULT_RATES
            if r.provider == "sarvam"
            and r.component == CostComponent.STT
            and r.model == "saaras:v3"
        )
        mpaise = usd_to_mpaise(row.usd_per_unit, usd_inr=REFERENCE_USD_INR)
        assert mpaise == 50_000  # ₹0.50 per minute, same as saarika:v2.5
        assert not row.provisional


class TestProvenance:
    def test_the_note_says_it_was_defaulted(self):
        """Every seeded row carries this. Without it there is no way to tell a
        rate somebody chose from one this file guessed."""
        assert "Seeded default" in SEED_NOTE
        assert "Verify" in SEED_NOTE

    def test_each_rate_records_how_it_was_derived(self):
        for rate in DEFAULT_RATES:
            assert rate.basis, f"{rate.provider}/{rate.component.value} has no basis"


class TestPostCallAnalysisIsBilledLikeAnyOtherInference:
    """QA tokens reach the cost engine as an ordinary LLM line.

    Post-call analysis runs real inference on our own key for a managed
    account. It used to be recorded under the string "QAAnalysis", which is not
    a vendor and has no rate, so every one of those tokens was reported uncosted
    -- not billed to the customer, and not counted as our cost either, which
    overstates margin on exactly the calls doing the most work.

    Competitors fold the same cost into their model-cost line rather than
    charging a separate analysis fee, which is what this arrangement reproduces:
    same provider, same model, same rate, same markup as the call's own tokens.
    """

    def test_a_qa_keyed_usage_row_resolves_to_a_real_provider(self):
        from api.services.billing.usage import usage_items_from_usage_info

        items = usage_items_from_usage_info(
            {
                "llm": {
                    "openai|||gpt-4.1-mini": {
                        "prompt_tokens": 3_000,
                        "completion_tokens": 400,
                    }
                }
            }
        )
        assert len(items) == 1
        assert items[0].provider == "openai"
        assert items[0].model == "gpt-4.1-mini"
        assert items[0].quantity == 3_400

    def test_the_old_label_had_no_rate_on_file(self):
        """The regression this guards. If a rate for "qaanalysis" is ever added,
        somebody has papered over the bug rather than fixing the key."""
        assert not any(r.provider == "qaanalysis" for r in DEFAULT_RATES)

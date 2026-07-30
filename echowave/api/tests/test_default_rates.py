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
    SEED_NOTE,
    _blend,
    usd_to_mpaise,
)


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


class TestProvenance:
    def test_the_note_says_it_was_defaulted(self):
        """Every seeded row carries this. Without it there is no way to tell a
        rate somebody chose from one this file guessed."""
        assert "Seeded default" in SEED_NOTE
        assert "Verify" in SEED_NOTE

    def test_each_rate_records_how_it_was_derived(self):
        for rate in DEFAULT_RATES:
            assert rate.basis, f"{rate.provider}/{rate.component.value} has no basis"

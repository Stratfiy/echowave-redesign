"""Measured rates, and the exchange rate that is actually live.

The point of both is to stop the rate card being a number somebody typed once.
These tests are about the guards rather than the arithmetic: a blend computed
from three characters of audio, or an exchange rate invented because a feed
returned nonsense, are worse than having no figure at all — they look
authoritative and nothing downstream can tell.
"""

import pytest

from api.services.billing import fx_source
from api.services.billing.realized_rates import (
    DIVERGENCE_THRESHOLD,
    MIN_UNITS_FOR_SIGNIFICANCE,
    RealizedRate,
    divergence,
)


def _rate(units: int, cost_paise: int, provider="sarvam", component="tts"):
    return RealizedRate(
        provider=provider,
        component=component,
        units=units,
        cost_paise=cost_paise,
        calls=10,
    )


class TestTheBlend:
    def test_it_is_millipaise_per_unit(self):
        # 5,000 units costing ₹15.00 is 0.3 paise = 300 millipaise per unit.
        assert _rate(5_000, 1_500).mpaise_per_unit == 300.0

    def test_no_units_is_zero_not_a_division_error(self):
        """A provider configured but never used must not crash the rate card."""
        assert _rate(0, 0).mpaise_per_unit == 0.0

    def test_a_thin_sample_is_not_significant(self):
        """One atypical call moves a small blend by an order of magnitude, and
        a number that wrong is worse than an empty cell."""
        assert not _rate(MIN_UNITS_FOR_SIGNIFICANCE - 1, 100).is_significant
        assert _rate(MIN_UNITS_FOR_SIGNIFICANCE, 100).is_significant


class TestDivergence:
    def test_agreement_is_not_reported(self):
        """Where one rate has been in force all window, measured and configured
        agree by construction — cost was computed from the rate. Reporting that
        as a finding would bury the real ones."""
        rates = [_rate(100_000, 30_000)]  # 300 mpaise/unit
        assert divergence(rates, {("sarvam", "tts"): 300.0}) == []

    def test_it_catches_the_seeded_sarvam_error(self):
        """The book shipped Sarvam TTS at ₹1.92/1k chars against a published
        ₹3.00 — a 1.56x understatement in the largest line of an Indic call.
        This is the check that would have caught it."""
        realized = [_rate(1_000_000, 300_000)]  # 300 mpaise = ₹3.00/1k chars
        found = divergence(realized, {("sarvam", "tts"): 192.0})
        assert len(found) == 1
        assert found[0].ratio == pytest.approx(300 / 192, rel=1e-3)
        assert "understates cost" in found[0].note

    def test_paying_less_than_configured_reads_as_a_discount(self):
        realized = [_rate(1_000_000, 200_000)]  # 200 mpaise
        found = divergence(realized, {("sarvam", "tts"): 300.0})
        assert found[0].ratio < 1
        assert "negotiated" in found[0].note

    def test_a_gap_inside_the_threshold_is_left_alone(self):
        """Rounding and pulse effects move the blend a little; that is not a
        finding."""
        configured = 300.0
        units = 1_000_000
        # cost_paise that lands the blend half a threshold above configured.
        inside = int(configured * (1 + DIVERGENCE_THRESHOLD / 2) * units / 1000)
        assert divergence([_rate(units, inside)], {("sarvam", "tts"): configured}) == []

    def test_an_unpriced_provider_is_skipped_not_infinitely_divergent(self):
        """No configured rate is a different problem — the card reports unpriced
        providers separately, and dividing by zero here would drown that."""
        assert divergence([_rate(1_000_000, 300_000)], {}) == []

    def test_thin_samples_never_raise_a_finding(self):
        realized = [_rate(10, 10_000)]  # wildly divergent, but 10 units
        assert divergence(realized, {("sarvam", "tts"): 300.0}) == []

    def test_the_worst_offender_sorts_first(self):
        realized = [
            _rate(1_000_000, 330_000, provider="a"),  # 1.1x
            _rate(1_000_000, 600_000, provider="b"),  # 2.0x
        ]
        found = divergence(realized, {("a", "tts"): 300.0, ("b", "tts"): 300.0})
        assert [d.provider for d in found] == ["b", "a"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, _url):
        return _FakeResponse(self._payload)


def _with_feed(monkeypatch, payload):
    monkeypatch.setattr(
        fx_source.httpx, "AsyncClient", lambda **_: _FakeClient(payload)
    )


class TestFetchingTheRate:
    """The live feed cannot be reached from the test sandbox, so the parsing,
    rounding and bounds are exercised against a stubbed response. Getting any of
    the three wrong misprices every call in both currencies."""

    async def test_it_parses_rupees_into_paise(self, monkeypatch):
        _with_feed(monkeypatch, {"rates": {"INR": 87.42}})
        assert (await fx_source.fetch()).paise_per_usd == 8742

    async def test_rounding_is_half_up_like_every_other_conversion(self, monkeypatch):
        _with_feed(monkeypatch, {"rates": {"INR": 87.425}})
        assert (await fx_source.fetch()).paise_per_usd == 8743

    async def test_a_feed_quoting_paise_is_refused(self, monkeypatch):
        """8742 "rupees" per dollar is the tell that a feed changed units. It
        would otherwise become ₹8,742/USD and inflate every provider cost 100x."""
        _with_feed(monkeypatch, {"rates": {"INR": 8742}})
        with pytest.raises(fx_source.FxFetchError, match="plausible band"):
            await fx_source.fetch()

    async def test_a_missing_rate_is_refused(self, monkeypatch):
        _with_feed(monkeypatch, {"rates": {}})
        with pytest.raises(fx_source.FxFetchError, match="no usable INR rate"):
            await fx_source.fetch()

    async def test_zero_is_refused_rather_than_dividing_by_it_later(self, monkeypatch):
        _with_feed(monkeypatch, {"rates": {"INR": 0}})
        with pytest.raises(fx_source.FxFetchError):
            await fx_source.fetch()

    async def test_an_unreachable_feed_raises_and_writes_nothing(self, monkeypatch):
        def _boom(**_):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(fx_source.httpx, "AsyncClient", _boom)
        with pytest.raises(fx_source.FxFetchError, match="Could not reach"):
            await fx_source.fetch()


class TestTheExchangeRateGuards:
    """A wrong exchange rate misprices every call in both currencies, so the
    fetcher is written to refuse rather than guess."""

    def test_a_rate_below_the_band_is_refused(self):
        assert fx_source.MIN_PLAUSIBLE_PAISE_PER_USD > 0
        assert not (
            fx_source.MIN_PLAUSIBLE_PAISE_PER_USD
            <= 0
            <= fx_source.MAX_PLAUSIBLE_PAISE_PER_USD
        )

    def test_a_feed_that_switched_to_paise_is_outside_the_band(self):
        """₹96/USD reported as 9600 would sail through a naive check and make
        every dollar cost ₹9,600."""
        assert 9600 * 100 > fx_source.MAX_PLAUSIBLE_PAISE_PER_USD

    def test_the_band_still_admits_a_realistic_rate(self):
        assert (
            fx_source.MIN_PLAUSIBLE_PAISE_PER_USD
            <= 9_600
            <= fx_source.MAX_PLAUSIBLE_PAISE_PER_USD
        )

    def test_small_moves_do_not_earn_a_history_row(self):
        """Daily noise of a few paise changes no invoice and would fill the
        table with rows nobody can act on."""
        assert fx_source.MIN_MOVE_PAISE > 0

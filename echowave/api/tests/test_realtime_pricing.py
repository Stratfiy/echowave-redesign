"""Pricing a speech-to-speech call.

The thing under test is not multiplication. It is the claim that a realtime
call costs meaningfully more than the vendor's per-minute figure implies,
because every turn re-sends the conversation's audio and pays input price on it
again. If that model is wrong the calculator is worse than useless — it is a
confident wrong number in front of a pricing decision.

So the tests that matter are the ones that pin the *shape* of the answer: that
cost per minute rises with call length, that the agent's own speech is billed
on both sides, that caching bends the curve, and that comparing two vendors on
headline price alone gets the ranking wrong.
"""

import pytest

from api.services.billing.realtime_pricing import (
    CallShape,
    RealtimeModelPrice,
    compare_realtime_models,
    estimate_realtime_call,
)
from api.services.billing.realtime_rates import (
    REALTIME_PRICES,
    price_for,
)

# Round numbers so expected values can be worked out by hand: one token per
# second of audio each way, $1 per million in, $2 per million out.
PLAIN = RealtimeModelPrice(
    provider="test",
    model="plain",
    label="Plain",
    audio_input_usd_per_million=1.0,
    audio_output_usd_per_million=2.0,
    cached_audio_input_usd_per_million=None,
    audio_input_tokens_per_second=1.0,
    audio_output_tokens_per_second=1.0,
    basis="fixture",
)

CACHED = RealtimeModelPrice(
    provider="test",
    model="cached",
    label="Cached",
    audio_input_usd_per_million=1.0,
    audio_output_usd_per_million=2.0,
    cached_audio_input_usd_per_million=0.0,  # free, to isolate the effect
    audio_input_tokens_per_second=1.0,
    audio_output_tokens_per_second=1.0,
    basis="fixture",
)


class TestTheCallShape:
    def test_a_single_turn_repeats_nothing(self):
        """The floor. One turn has no history behind it, so the multiple is 1
        and the quoted per-minute price is the real one."""
        shape = CallShape(duration_seconds=10, seconds_per_turn=10)

        assert shape.turns == 1
        assert shape.context_multiple == 1.0

    def test_the_multiple_grows_with_the_number_of_turns(self):
        short = CallShape(duration_seconds=60, seconds_per_turn=10)
        long = CallShape(duration_seconds=600, seconds_per_turn=10)

        assert short.turns == 6
        assert short.context_multiple == 3.5
        assert long.context_multiple == 30.5

    def test_a_connected_call_always_has_at_least_one_turn(self):
        """A five-second call still got a greeting. Zero turns would price it
        at nothing and quietly divide by zero downstream."""
        assert CallShape(duration_seconds=5, seconds_per_turn=30).turns == 1

    def test_silence_is_not_audio(self):
        shape = CallShape(
            duration_seconds=100, agent_talk_share=0.4, caller_talk_share=0.3
        )

        assert shape.agent_audio_seconds == 40
        assert shape.caller_audio_seconds == 30

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"duration_seconds": 0},
            {"seconds_per_turn": 0},
            {"agent_talk_share": 1.5},
            {"caller_talk_share": -0.1},
            {"agent_talk_share": 0.7, "caller_talk_share": 0.7},
        ],
    )
    def test_an_impossible_call_is_refused(self, kwargs):
        """Two people talking for 140% of the call is not a conservative
        estimate, it is a bug upstream. Better to raise than to price it."""
        with pytest.raises(ValueError):
            CallShape(**kwargs)


class TestReSentContextIsMostOfTheBill:
    def test_a_one_turn_call_bills_the_audio_once(self):
        shape = CallShape(
            duration_seconds=10,
            seconds_per_turn=10,
            agent_talk_share=0.5,
            caller_talk_share=0.5,
        )

        estimate = estimate_realtime_call(PLAIN, shape)

        # 10s of audio at 1 token/s, none of it repeated.
        assert estimate.fresh_input_tokens == 10
        assert estimate.repeated_input_tokens == 0
        assert estimate.repeated_input_micros_usd == 0

    def test_a_longer_call_bills_the_same_audio_many_times(self):
        """The whole point. Six turns means the input side is billed 3.5x, so
        two and a half times the audio is pure repetition."""
        shape = CallShape(
            duration_seconds=60,
            seconds_per_turn=10,
            agent_talk_share=0.5,
            caller_talk_share=0.5,
        )

        estimate = estimate_realtime_call(PLAIN, shape)

        assert estimate.fresh_input_tokens == 60
        assert estimate.repeated_input_tokens == 150  # 60 * (3.5 - 1)

    def test_cost_per_minute_rises_with_call_length(self):
        """The claim that makes this module necessary. If a per-minute figure
        were constant, a rate row would have done."""
        per_minute = [
            estimate_realtime_call(
                PLAIN, CallShape(duration_seconds=seconds, seconds_per_turn=10)
            ).micros_usd_per_minute
            for seconds in (60, 180, 600)
        ]

        assert per_minute == sorted(per_minute)
        assert per_minute[0] < per_minute[-1]

    def test_most_of_a_long_call_is_repetition(self):
        estimate = estimate_realtime_call(
            PLAIN, CallShape(duration_seconds=600, seconds_per_turn=10)
        )

        assert estimate.repeated_share > 0.8

    def test_the_agent_pays_for_its_own_voice_twice(self):
        """Generated audio is charged as output, then re-enters the context and
        is charged as input on every later turn. An agent that talks more is
        more expensive than the output price alone suggests."""
        talkative = CallShape(
            duration_seconds=120,
            seconds_per_turn=10,
            agent_talk_share=0.6,
            caller_talk_share=0.2,
        )
        quiet = CallShape(
            duration_seconds=120,
            seconds_per_turn=10,
            agent_talk_share=0.2,
            caller_talk_share=0.6,
        )

        loud_estimate = estimate_realtime_call(PLAIN, talkative)
        quiet_estimate = estimate_realtime_call(PLAIN, quiet)

        # Same total audio, so identical input cost — the difference is output.
        assert loud_estimate.fresh_input_tokens == quiet_estimate.fresh_input_tokens
        assert loud_estimate.total_micros_usd > quiet_estimate.total_micros_usd


class TestCaching:
    def test_caching_only_discounts_the_repeated_part(self):
        """Fresh audio is fresh whatever the cache does. A calculator that
        applied the cached rate to everything would understate by the whole
        first pass."""
        shape = CallShape(
            duration_seconds=600, seconds_per_turn=10, caching_enabled=True
        )

        cached = estimate_realtime_call(CACHED, shape)
        uncached = estimate_realtime_call(PLAIN, shape)

        assert cached.caching_applied
        assert cached.repeated_input_micros_usd == 0
        assert cached.fresh_input_micros_usd == uncached.fresh_input_micros_usd
        assert cached.total_micros_usd < uncached.total_micros_usd

    def test_asking_for_caching_a_model_lacks_changes_nothing(self):
        """Silently pricing as though a provider cached when it does not is the
        exact failure this module exists to prevent."""
        shape = CallShape(duration_seconds=600, caching_enabled=True)

        estimate = estimate_realtime_call(PLAIN, shape)

        assert not estimate.caching_applied
        assert estimate.total_micros_usd == (
            estimate_realtime_call(
                PLAIN, CallShape(duration_seconds=600, caching_enabled=False)
            ).total_micros_usd
        )

    def test_caching_flattens_the_curve(self):
        """Without it, a ten-minute call costs far more per minute than a one
        minute call. With it, much less so — which is why it is the first thing
        to reach for on long calls."""

        def per_minute(price, seconds):
            return estimate_realtime_call(
                price,
                CallShape(
                    duration_seconds=seconds,
                    seconds_per_turn=10,
                    caching_enabled=True,
                ),
            ).micros_usd_per_minute

        uncached_growth = per_minute(PLAIN, 600) / per_minute(PLAIN, 60)
        cached_growth = per_minute(CACHED, 600) / per_minute(CACHED, 60)

        assert cached_growth < uncached_growth


class TestComparingModels:
    def test_headline_price_alone_gets_the_ranking_wrong(self):
        """Gemini Live's audio input is ~10x cheaper per million than OpenAI's,
        but it tokenises audio ~3x more densely. The per-million comparison and
        the per-minute one are different questions, and only one of them is the
        bill."""
        gemini = price_for("google_realtime")
        openai = price_for("openai_realtime")
        assert gemini is not None and openai is not None

        headline_ratio = (
            openai.audio_input_usd_per_million / gemini.audio_input_usd_per_million
        )
        shape = CallShape(duration_seconds=180)
        actual_ratio = (
            estimate_realtime_call(openai, shape).micros_usd_per_minute
            / estimate_realtime_call(gemini, shape).micros_usd_per_minute
        )

        # ~10.7x on headline, but far less once tokenisation is accounted for.
        assert headline_ratio > 10
        assert actual_ratio < headline_ratio / 2

    def test_comparison_is_cheapest_first(self):
        results = compare_realtime_models(REALTIME_PRICES)

        costs = [estimate.micros_usd_per_minute for estimate in results]
        assert costs == sorted(costs)
        assert len(results) == len(REALTIME_PRICES)

    def test_every_seeded_model_prices_without_error(self):
        """A price book entry that cannot be priced is worse than a missing
        one — it reaches the UI before it reaches anyone's attention."""
        for estimate in compare_realtime_models(REALTIME_PRICES):
            assert estimate.micros_usd_per_minute > 0
            assert estimate.basis


class TestTheBlendedRateForTheRateCard:
    """The rate card holds one rate per model; realtime billing has four
    prices. Something has to collapse that, and these pin how."""

    def test_the_blend_reproduces_the_modelled_cost(self):
        """The blend is only defensible if applying it to the tokens gives back
        the cost it was derived from. If those drift, receipts stop matching
        the calculator that justified the rate."""
        from api.services.billing.realtime_pricing import blended_usd_per_1k_tokens

        shape = CallShape(duration_seconds=180)
        estimate = estimate_realtime_call(PLAIN, shape)
        tokens = (
            estimate.fresh_input_tokens
            + estimate.repeated_input_tokens
            + estimate.output_tokens
        )

        rebuilt_usd = blended_usd_per_1k_tokens(PLAIN, shape) * tokens / 1000

        assert rebuilt_usd == pytest.approx(estimate.total_micros_usd / 1_000_000)

    def test_a_pricier_model_blends_to_a_pricier_rate(self):
        from api.services.billing.realtime_pricing import blended_usd_per_1k_tokens

        gemini = price_for("google_realtime")
        openai = price_for("openai_realtime")

        assert blended_usd_per_1k_tokens(openai) > blended_usd_per_1k_tokens(gemini)

    def test_every_realtime_provider_the_pipeline_records_has_a_seeded_rate(self):
        """The silent failure this guards. ``provider_from_processor`` derives
        the name from the service class, so a class rename leaves the seeded
        rate pointing at a provider that no longer exists — every realtime call
        goes uncosted and margin reports 100% with no error anywhere."""
        from api.services.billing.default_rates import REALTIME_RATES
        from api.services.billing.usage import provider_from_processor

        seeded = {rate.provider for rate in REALTIME_RATES}
        recorded = {
            provider_from_processor(f"{cls}#0")
            for cls in (
                "DecibylGeminiLiveLLMService",
                "DecibylGeminiLiveVertexLLMService",
                "DecibylOpenAIRealtimeLLMService",
            )
        }

        assert recorded <= seeded

    def test_the_seeded_realtime_rates_are_not_zero(self):
        """A zero rate is indistinguishable from no rate on the margin report,
        and is how "free" gets into the numbers."""
        from api.services.billing.default_rates import REALTIME_RATES

        for rate in REALTIME_RATES:
            assert rate.usd_per_unit > 0


class TestTheEndpoint:
    """The calculator is only useful through the route, and the route is where
    the shape validation has to turn into a 422 rather than a 500."""

    @staticmethod
    def _client():
        from unittest.mock import MagicMock

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.routes.cost_estimate import router
        from api.services.auth.depends import get_user

        app = FastAPI()
        app.include_router(router)
        user = MagicMock()
        user.id = 1
        user.selected_organization_id = 1
        app.dependency_overrides[get_user] = lambda: user
        return TestClient(app)

    def test_it_returns_models_cheapest_first_with_provenance(self):
        response = self._client().post(
            "/cost-estimate/realtime", json={"duration_seconds": 180}
        )

        assert response.status_code == 200
        body = response.json()
        costs = [model["micros_usd_per_minute"] for model in body["models"]]
        assert costs == sorted(costs)
        # A list price on screen without its date is indistinguishable from a
        # measured one, which is how a stale number gets quoted to a customer.
        assert body["as_of"]
        assert body["note"]

    def test_an_impossible_call_is_a_422_not_a_500(self):
        response = self._client().post(
            "/cost-estimate/realtime",
            json={"agent_talk_share": 0.8, "caller_talk_share": 0.8},
        )

        assert response.status_code == 422

    def test_a_longer_call_costs_more_per_minute_through_the_route(self):
        """End-to-end proof of the claim the screen is built on."""
        client = self._client()

        def cheapest(seconds):
            body = client.post(
                "/cost-estimate/realtime", json={"duration_seconds": seconds}
            ).json()
            return body["models"][0]["micros_usd_per_minute"]

        assert cheapest(600) > cheapest(60)


class TestLookup:
    def test_an_exact_model_is_matched(self):
        price = price_for("google_realtime", "gemini-3.1-flash-live-preview")

        assert price is not None
        assert price.model == "gemini-3.1-flash-live-preview"

    def test_an_unknown_model_falls_back_to_its_provider(self):
        """A newly released model should be estimated at its provider's rate
        rather than reported as unpriceable."""
        price = price_for("openai_realtime", "gpt-realtime-9-does-not-exist")

        assert price is not None
        assert price.provider == "openai_realtime"

    def test_an_unknown_provider_is_not_guessed_at(self):
        assert price_for("some-new-vendor") is None

    def test_the_registry_provider_names_are_the_lookup_keys(self):
        """These strings are what the configuration registry stores, so a
        rename there must break this test rather than silently stop pricing
        every realtime call."""
        from api.services.configuration.registry import ServiceProviders

        for name in (
            ServiceProviders.GOOGLE_REALTIME,
            ServiceProviders.GOOGLE_VERTEX_REALTIME,
            ServiceProviders.OPENAI_REALTIME,
        ):
            assert price_for(name.value) is not None

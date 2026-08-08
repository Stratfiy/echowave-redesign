"""Rumik Silk as a text-to-speech provider.

Synthesis is the largest provider line on a call — bigger than transcription,
far bigger than the language model — so a cheaper voice vendor moves margin more
than anything else on the rate card. These tests hold the pieces that make that
saving real rather than theoretical: the provider is offered, the key is stored,
the rate exists, and the service is constructed the way Rumik expects.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.options.rumik import (
    RUMIK_DEFAULT_DESCRIPTION,
    RUMIK_GATEWAY_URL,
    RUMIK_TTS_MODELS,
    RUMIK_VOICES,
)
from api.services.configuration.registry import (
    RumikTTSConfiguration,
    ServiceProviders,
)


class TestConfiguration:
    def test_it_defaults_to_the_cheap_fast_model(self):
        # mulberry is half muga's price and the faster of the two. A voice
        # agent that defaulted to the expressive model would cost double for a
        # quality nobody asked for.
        config = RumikTTSConfiguration(api_key="k")
        assert config.model == "mulberry"

    def test_mulberry_is_offered_before_muga(self):
        assert RUMIK_TTS_MODELS[0] == "mulberry"

    def test_it_carries_a_description_by_default(self):
        # Rumik shapes even a preset voice with the description. Blank means
        # every agent on the platform sounds identical.
        config = RumikTTSConfiguration(api_key="k")
        assert config.description.strip()

    def test_the_default_voice_is_a_real_preset(self):
        assert RumikTTSConfiguration(api_key="k").voice in RUMIK_VOICES

    def test_it_registers_as_a_tts_provider(self):
        # The decorator is what puts it in the picker, the form and the BYOK
        # key slot. If this fails, nobody can select Rumik anywhere.
        from api.services.configuration.registry import REGISTRY, ServiceType

        assert ServiceProviders.RUMIK in REGISTRY[ServiceType.TTS]


class TestServiceConstruction:
    def _config(self, **tts):
        return SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.RUMIK.value,
                api_key="test-key",
                model=tts.pop("model", "mulberry"),
                voice=tts.pop("voice", "ira"),
                description=tts.pop("description", RUMIK_DEFAULT_DESCRIPTION),
                language=tts.pop("language", "hi-IN"),
                **tts,
            )
        )

    def _build(self, config):
        from api.services.pipecat.audio_config import AudioConfig
        from api.services.pipecat.service_factory import create_tts_service

        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )
        # Patched where it is imported — the factory imports it inside the
        # branch, so the name to patch lives in the vendor package.
        with patch("pipecat_rumik.RumikTTSService") as mock_service:
            create_tts_service(config, audio_config)
        assert mock_service.call_count == 1
        # Settings is an attribute of the patched class, so the constructed
        # object is a mock. What we can assert on — and what actually matters —
        # is the arguments we hand it.
        return mock_service.call_args.kwargs, mock_service.Settings.call_args.kwargs

    def test_it_passes_the_key_and_the_gateway(self):
        kwargs, _ = self._build(self._config())
        assert kwargs["api_key"] == "test-key"
        assert kwargs["gateway_url"] == RUMIK_GATEWAY_URL

    def test_it_sends_the_chosen_voice(self):
        _, settings = self._build(self._config(voice="zoya"))
        assert settings["voice"] == "zoya"

    def test_muga_is_sent_no_voice(self):
        # Muga is directed by tone tags, not a speaker. Rumik treats an
        # unrecognised speaker as a description hint rather than erroring, so
        # sending one produces a stranger's voice instead of a failure.
        _, settings = self._build(self._config(model="muga", voice="ira"))
        assert "voice" not in settings

    def test_a_blank_description_falls_back_rather_than_being_sent_empty(self):
        _, settings = self._build(self._config(description="   "))
        assert settings["description"] == RUMIK_DEFAULT_DESCRIPTION

    def test_the_language_is_passed_through(self):
        _, settings = self._build(self._config(language="en-IN"))
        assert settings["language"] == "en-IN"


class TestPricing:
    def _rate(self, model):
        from api.enums import CostComponent
        from api.services.billing.default_rates import DEFAULT_RATES

        return next(
            r.usd_per_unit
            for r in DEFAULT_RATES
            if r.provider == "rumik"
            and r.model == model
            and r.component == CostComponent.TTS
        )

    def test_every_model_is_priced(self):
        for model in RUMIK_TTS_MODELS:
            assert self._rate(model) > 0

    def test_there_is_a_provider_wide_fallback(self):
        # A model Rumik ships next must not land as uncosted.
        assert self._rate("") > 0

    def test_muga_costs_about_twice_mulberry(self):
        assert self._rate("muga") == pytest.approx(2 * self._rate("mulberry"), rel=0.05)

    def test_mulberry_undercuts_sarvam_bulbul_v2(self):
        # The reason this integration exists. If this ever stops being true,
        # the cheaper-voice argument has gone with it.
        from api.enums import CostComponent
        from api.services.billing.default_rates import DEFAULT_RATES

        sarvam = next(
            r.usd_per_unit
            for r in DEFAULT_RATES
            if r.provider == "sarvam"
            and r.model == "bulbul:v2"
            and r.component == CostComponent.TTS
        )
        assert self._rate("mulberry") < sarvam

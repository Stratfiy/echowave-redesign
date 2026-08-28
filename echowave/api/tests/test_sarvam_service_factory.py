import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pipecat.services.sarvam.llm import SarvamLLMService as RealSarvamLLMService
from pipecat.services.settings import is_given
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transcriptions.language import Language

from api.services.configuration.registry import (
    SarvamLLMConfiguration,
    SarvamTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat import service_factory
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    _LOW_LATENCY_STREAMING_TTS_PROVIDERS,
    _REQUEST_BASED_TTS_PROVIDERS,
    _create_tts_service_instance,
    create_llm_service,
    create_llm_service_from_provider,
    create_stt_service,
    create_tts_service,
)


class TestSarvamLLMConfiguration:
    def test_default_values(self):
        config = SarvamLLMConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.SARVAM
        assert config.model == "sarvam-105b"
        assert config.temperature == 0.5

    def test_custom_model(self):
        # allow_custom_input is on, so a model Sarvam ships after this release
        # can be typed in without waiting for a registry change.
        config = SarvamLLMConfiguration(api_key="test-key", model="sarvam-next")
        assert config.model == "sarvam-next"

    def test_the_retired_model_is_no_longer_offered(self):
        # sarvam-30b returns a 400 from Sarvam. It was the default, so every
        # agent built on it failed after the caller had already spoken.
        from api.services.configuration.options.sarvam import SARVAM_LLM_MODELS

        assert "sarvam-30b" not in SARVAM_LLM_MODELS
        assert SarvamLLMConfiguration(api_key="k").model != "sarvam-30b"


class TestSarvamLLMServiceFactory:
    def test_create_sarvam_llm_service(self):
        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-105b",
                api_key="test-key",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["settings"].model == "sarvam-105b"
        assert kwargs["settings"].temperature == 0.5

    def test_create_sarvam_llm_service_passes_user_temperature(self):
        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-105b",
                api_key="test-key",
                temperature=0.8,
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].temperature == 0.8

    def test_create_llm_service_extracts_sarvam_temperature(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-105b",
                api_key="test-key",
                temperature=0.7,
            )
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].temperature == 0.7


class TestSarvamSTTServiceFactory:
    @pytest.mark.parametrize(
        "input_language,expected_language",
        [
            ("unknown", None),
            (None, None),
            ("hi-IN", Language.HI_IN),
            ("ne-IN", "ne-IN"),
        ],
    )
    def test_stt_language_mapping(self, input_language, expected_language):
        user_config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                model="saaras:v3",
                api_key="test-key",
                language=input_language,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamSTTService"
        ) as mock_service:
            create_stt_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].language == expected_language


class TestTTSLatencyPolicy:
    @pytest.mark.parametrize("provider", sorted(_LOW_LATENCY_STREAMING_TTS_PROVIDERS))
    def test_streaming_providers_receive_tokens_immediately(self, provider):
        service = Mock()

        result = _create_tts_service_instance(provider, service, api_key="test-key")

        assert result is service.return_value
        service.assert_called_once_with(
            api_key="test-key",
            text_aggregation_mode=TextAggregationMode.TOKEN,
        )

    @pytest.mark.parametrize("provider", sorted(_REQUEST_BASED_TTS_PROVIDERS))
    def test_request_providers_keep_sentence_aggregation(self, provider):
        service = Mock()

        _create_tts_service_instance(provider, service, api_key="test-key")

        service.assert_called_once_with(api_key="test-key")


class TestSarvamTTSServiceFactory:
    def test_sarvam_tts_configuration_defaults(self):
        config = SarvamTTSConfiguration(api_key="test-key")

        assert config.provider == ServiceProviders.SARVAM
        assert config.model == "bulbul:v2"
        assert config.voice == "anushka"
        assert config.language == "hi-IN"
        assert config.speed == 1.0

    def test_sarvam_tts_voice_schema_allows_custom_model_specific_options(self):
        voice_schema = SarvamTTSConfiguration.model_json_schema()["properties"]["voice"]

        assert voice_schema["allow_custom_input"] is True
        assert "bulbul:v2" in voice_schema["model_options"]
        assert "bulbul:v3" in voice_schema["model_options"]

    def test_create_sarvam_tts_service_maps_speed_to_pace(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice="anushka",
                language="hi-IN",
                speed=1.25,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["settings"].model == "bulbul:v2"
        assert kwargs["settings"].voice == "anushka"
        assert kwargs["settings"].language == Language.HI
        assert kwargs["settings"].pace == 1.25
        assert kwargs["settings"].min_buffer_size == 20
        assert kwargs["settings"].max_chunk_length == 80
        assert kwargs["text_aggregation_mode"] == TextAggregationMode.TOKEN

    def test_create_sarvam_tts_service_normalizes_custom_voice_id(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice=" Rehan ",
                language="hi-IN",
                speed=1.0,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].voice == "rehan"

    def test_create_sarvam_tts_service_omits_a_blank_voice_id(self):
        """A blank voice must not be sent, and must not be filled in here.

        Pipecat resolves an unset voice against the *model*: anushka on
        bulbul:v2, shubh on v3. Naming a default here would hardcode the v2
        answer and silently send a v2 speaker to a v3 model — so the setting is
        left out entirely and the vendor library picks.
        """
        for model in ("bulbul:v2", "bulbul:v3"):
            user_config = SimpleNamespace(
                tts=SimpleNamespace(
                    provider=ServiceProviders.SARVAM.value,
                    api_key="test-key",
                    model=model,
                    voice="   ",
                    language="hi-IN",
                    speed=1.0,
                )
            )
            audio_config = AudioConfig(
                transport_in_sample_rate=16000, transport_out_sample_rate=16000
            )

            with patch(
                "api.services.pipecat.service_factory.SarvamTTSService"
            ) as mock_service:
                create_tts_service(user_config, audio_config)

            settings = mock_service.call_args.kwargs["settings"]
            assert not is_given(settings.voice), (
                f"a blank voice leaked a value on {model}: {settings.voice!r}"
            )

    def test_create_sarvam_tts_service_omits_the_default_sentinel(self):
        """``"default"`` is our word for "you choose", not a Sarvam speaker.

        It is truthy, so it survived every ``or``-style fallback and went to
        the vendor as ``speaker="default"`` — a name bulbul:v2 does not have.
        Every managed call with no explicit voice took that path.
        """
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice="default",
                language="hi-IN",
                speed=1.0,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        settings = mock_service.call_args.kwargs["settings"]
        assert not is_given(settings.voice)


class TestEveryTTSProviderHasALatencyDecision:
    """No TTS provider may sit outside both sets.

    The two frozensets are the audit. A provider absent from both has never
    been looked at, and it silently gets Pipecat's sentence default — which is
    the safe answer but an unrecorded one, indistinguishable from a considered
    decision. Reading it back out of the factory is what makes a new provider's
    omission visible instead of invisible.
    """

    @staticmethod
    def _providers_the_factory_handles() -> set[str]:
        """Every provider `create_tts_service` branches on."""
        source = Path(service_factory.__file__).read_text()
        return set(
            re.findall(
                r"user_config\.tts\.provider == ServiceProviders\.(\w+)\.value", source
            )
        )

    def test_the_two_sets_do_not_overlap(self):
        assert not (_LOW_LATENCY_STREAMING_TTS_PROVIDERS & _REQUEST_BASED_TTS_PROVIDERS)

    def test_every_provider_is_classified(self):
        classified = {
            ServiceProviders(value).name
            for value in _LOW_LATENCY_STREAMING_TTS_PROVIDERS
            | _REQUEST_BASED_TTS_PROVIDERS
        }
        missing = self._providers_the_factory_handles() - classified

        assert not missing, (
            f"TTS providers with no recorded latency decision: {sorted(missing)}. "
            "Add each to _LOW_LATENCY_STREAMING_TTS_PROVIDERS or to "
            "_REQUEST_BASED_TTS_PROVIDERS with the reason, having checked "
            "whether the class the factory builds holds its connection open "
            "across synthesis calls."
        )

    def test_no_set_names_a_provider_the_factory_cannot_build(self):
        """A stale entry is a decision about something that no longer exists."""
        handled = self._providers_the_factory_handles()
        classified = {
            ServiceProviders(value).name
            for value in _LOW_LATENCY_STREAMING_TTS_PROVIDERS
            | _REQUEST_BASED_TTS_PROVIDERS
        }

        assert not (classified - handled)

    def test_azure_is_not_treated_as_streaming(self):
        """Its connection is persistent; its synthesis is not.

        AzureTTSService drains its audio queue on entry to run_tts, so a second
        call arriving while the first is still playing discards the rest of
        that audio. Token mode would chop speech rather than speed it up, which
        is exactly the kind of mistake the websocket-shaped class invites.
        """
        assert (
            ServiceProviders.AZURE_SPEECH.value
            not in _LOW_LATENCY_STREAMING_TTS_PROVIDERS
        )

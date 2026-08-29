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
        """The conversational model, because these agents answer phones.

        sarvam-105b reasons in chain-of-thought before every reply and cannot
        be told not to, which measured 6,045ms of a 7,554ms turn.
        """
        config = SarvamLLMConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.SARVAM
        assert config.model == "sarvam-105b-conversations"
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
            "api.services.pipecat.service_factory.DecibylSarvamLLMService"
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
            "api.services.pipecat.service_factory.DecibylSarvamLLMService"
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
            "api.services.pipecat.service_factory.DecibylSarvamLLMService"
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
        assert kwargs["settings"].min_buffer_size == 30
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


class TestTheBufferSettingsStayInsideSarvamsRange:
    """These two numbers are validated by Sarvam, not by us.

    They are sent in the config message at websocket connect. A value outside
    the accepted range is refused there, and pipecat turns that into a fatal
    ErrorFrame — so the call is answered and then dies at 0s with
    ``pipeline_error`` rather than merely sounding wrong. There is no gentle
    version of getting these wrong, which is why the bound is asserted rather
    than left to a comment.

    Ranges from Sarvam's API reference: ``min_buffer_size`` 30-200 (default
    50), ``max_chunk_length`` 50-500 (default 150).
    """

    MIN_BUFFER_SIZE_RANGE = (30, 200)
    MAX_CHUNK_LENGTH_RANGE = (50, 500)

    @staticmethod
    def _shipped_settings():
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice="anushka",
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
        return mock_service.call_args.kwargs["settings"]

    def test_min_buffer_size_is_not_below_the_floor(self):
        """It shipped at 20, under Sarvam's documented minimum of 30.

        Their Pipecat integration guide suggests 15-25, which contradicts their
        own API reference. Until that is settled with them the reference is the
        number to hold: too high costs a little first-byte latency, too low
        costs the entire call.
        """
        low, high = self.MIN_BUFFER_SIZE_RANGE
        assert low <= self._shipped_settings().min_buffer_size <= high

    def test_max_chunk_length_is_inside_the_range(self):
        low, high = self.MAX_CHUNK_LENGTH_RANGE
        assert low <= self._shipped_settings().max_chunk_length <= high


class TestTheConversationalModelIsWhatVoiceGets:
    """sarvam-105b is a reasoning model, and that is the whole latency story.

    It emits reasoning_content -- chain-of-thought -- before a single word of
    the answer, on every request. reasoning_effort cannot switch it off: Sarvam
    accepts only low/medium/high, and against a 600-token cap the default and
    "low" both spent the entire budget thinking and returned no content at all.
    Measured on run 77 it was 6,045ms of a 7,554ms turn, with the caller waiting
    in silence for all of it.
    """

    def test_the_wrapper_allows_the_conversational_model(self):
        """Unknown names raise in _validate_model at construction, so widening
        the allow-list is what makes the model selectable at all."""
        from api.services.pipecat.sarvam_llm import (
            SARVAM_CONVERSATIONS_MODEL,
            DecibylSarvamLLMService,
        )

        assert SARVAM_CONVERSATIONS_MODEL in DecibylSarvamLLMService._SUPPORTED_MODELS

    def test_the_wrapper_keeps_every_model_upstream_allowed(self):
        from api.services.pipecat.sarvam_llm import DecibylSarvamLLMService

        assert RealSarvamLLMService._SUPPORTED_MODELS.issubset(
            DecibylSarvamLLMService._SUPPORTED_MODELS
        )

    def test_constructing_the_conversational_model_does_not_raise(self):
        from api.services.pipecat.sarvam_llm import DecibylSarvamLLMService

        service = DecibylSarvamLLMService(
            api_key="test-key",
            settings=DecibylSarvamLLMService.Settings(
                model="sarvam-105b-conversations"
            ),
        )

        assert service is not None

    def test_the_upstream_class_still_refuses_it(self):
        """Guards the reason this wrapper exists. If upstream ever ships the
        model in its own allow-list, this fails and the wrapper can shrink."""
        with pytest.raises(ValueError):
            RealSarvamLLMService(
                api_key="test-key",
                settings=RealSarvamLLMService.Settings(
                    model="sarvam-105b-conversations"
                ),
            )

    def test_the_voice_tiers_resolve_to_the_conversational_model(self):
        """The tier is what a managed account actually gets -- a default on the
        configuration schema never reaches it."""
        from api.services.configuration import managed_tiers

        for tier in ("lite", "fast", "zen"):
            upstream = managed_tiers.resolve("llm", tier)
            assert upstream.provider == "sarvam"
            assert upstream.model == "sarvam-105b-conversations", tier

    def test_the_conversational_model_is_offered_first(self):
        from api.services.configuration.options.sarvam import SARVAM_LLM_MODELS

        assert SARVAM_LLM_MODELS[0] == "sarvam-105b-conversations"


class TestToolCallsSurviveTheReply:
    """The parent's one-shot completion is load-bearing, and its docstring
    undersells why.

    It reads as a guard against Sarvam's streaming deltas losing leading
    whitespace -- cosmetic, and worth trading for the seconds a one-shot
    completion costs. It is not: the same method stamps an `index` onto every
    tool call, which OpenAI-style streaming aggregation needs to assemble a
    call from its deltas and which Sarvam does not supply. The commit title
    says both halves: "Preserve spaces *and tool calls*".

    Every node transition is a tool call, so overriding it does not risk joined
    words -- it stops transitions resolving and the agent falls silent on the
    first turn that should move nodes. Runs 78-80 each recorded exactly one
    turn where runs 75-76 recorded five.
    """

    def test_the_wrapper_does_not_override_the_completion_path(self):
        from api.services.pipecat.sarvam_llm import DecibylSarvamLLMService

        assert (
            DecibylSarvamLLMService.get_chat_completions
            is RealSarvamLLMService.get_chat_completions
        )

    def test_the_wrapper_changes_nothing_but_the_allowed_models(self):
        """Narrow on purpose. The model swap is the latency win and it is
        independent of the completion path; anything else this class touched
        would be reaching past what was measured."""
        from api.services.pipecat.sarvam_llm import DecibylSarvamLLMService

        overridden = {
            name
            for name, attr in vars(DecibylSarvamLLMService).items()
            if not name.startswith("__") and callable(attr)
        }

        assert overridden == set()

    def test_sarvams_parameter_cleanup_is_still_applied(self):
        from api.services.pipecat.sarvam_llm import DecibylSarvamLLMService

        assert (
            DecibylSarvamLLMService.build_chat_completion_params
            is RealSarvamLLMService.build_chat_completion_params
        )


class TestTheInstantTranscriberIsOptInOnly:
    """Deepgram Flux emits its own turn boundaries, so the endpointing wait
    disappears rather than shrinking -- worth ~1,172ms a turn, the largest
    single stage once the LLM is not misconfigured.

    It is not the default and must not become one. Flux multilingual covers
    de/en/es/fr/hi/it/ja/nl/pt/ru: Hindi yes, Telugu and Tamil no. A caller
    answering in Telugu is transcribed as nothing, which is a worse call than a
    slow one -- and Telugu is what the QA transcript that started this actually
    ended in.
    """

    def test_the_default_transcriber_still_understands_indian_languages(self):
        from api.services.configuration import managed_tiers

        upstream = managed_tiers.resolve("stt", "default")

        assert upstream.provider == "sarvam"
        assert upstream.model == "saaras:v3"

    def test_the_instant_tier_resolves_to_a_turn_owning_model(self):
        from api.services.configuration import managed_tiers
        from api.services.configuration.options.deepgram import DEEPGRAM_FLUX_MODELS

        upstream = managed_tiers.resolve("stt", "instant")

        assert upstream.provider == "deepgram"
        assert upstream.model in DEEPGRAM_FLUX_MODELS

    def test_the_instant_tier_actually_skips_the_endpointing_budget(self):
        """The point of the tier. If this model did not report external turns,
        it would cost more and save nothing."""
        from types import SimpleNamespace

        from api.services.configuration import managed_tiers

        upstream = managed_tiers.resolve("stt", "instant")
        config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=upstream.provider, model=upstream.model, language="multi"
            )
        )

        assert service_factory.stt_uses_external_turns(config) is True

    def test_the_default_tier_does_not_claim_external_turns(self):
        """Guards the inverse: saaras holds the turn until its final transcript
        lands, which is where the ~1,172ms goes."""
        from types import SimpleNamespace

        from api.services.configuration import managed_tiers

        upstream = managed_tiers.resolve("stt", "default")
        config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=upstream.provider, model=upstream.model, language="multi"
            )
        )

        assert service_factory.stt_uses_external_turns(config) is False

    def test_the_instant_tier_is_priced(self):
        """An unpriced tier does not fail -- it bills the platform fee alone and
        reports margin nobody earned."""
        from api.enums import CostComponent
        from api.services.billing.default_rates import DEFAULT_RATES
        from api.services.configuration import managed_tiers

        upstream = managed_tiers.resolve("stt", "instant")
        priced = {
            (r.provider, r.model)
            for r in DEFAULT_RATES
            if r.component == CostComponent.STT
        }

        assert (upstream.provider, upstream.model) in priced

    def test_both_tiers_carry_a_label_that_names_the_trade(self):
        """The language limit is the whole reason this is a choice rather than
        an upgrade, so it has to reach the screen."""
        from api.services.configuration import managed_tiers

        for tier in managed_tiers.STT_TIERS:
            label, blurb = managed_tiers.STT_TIER_LABELS[tier]
            assert label and blurb

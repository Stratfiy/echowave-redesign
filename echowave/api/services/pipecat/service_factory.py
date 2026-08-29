from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse, urlunparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.constants import MPS_API_URL
from api.schemas.ai_model_configuration import DECIBYL_DEFAULT_VOICE
from api.services.configuration.options import (
    DEEPGRAM_FLUX_MODELS,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
    RUMIK_DEFAULT_DESCRIPTION,
    RUMIK_GATEWAY_URL,
)
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DecibylGeminiJSONSchemaAdapter,
)
from api.services.pipecat.minimax_tts import MiniMaxOwnedSessionTTSService
from api.utils.url_security import validate_user_configured_service_url
from pipecat.services.assemblyai.stt import AssemblyAISTTService, AssemblyAISTTSettings
from pipecat.services.aws.llm import AWSBedrockLLMService, AWSBedrockLLMSettings
from pipecat.services.azure.llm import AzureLLMService, AzureLLMSettings
from pipecat.services.azure.stt import AzureSTTService, AzureSTTSettings
from pipecat.services.azure.tts import AzureTTSService, AzureTTSSettings
from pipecat.services.cartesia.stt import CartesiaSTTService, CartesiaSTTSettings
from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.cartesia.turns.stt import CartesiaTurnsSTTService
from pipecat.services.deepgram.flux.stt import (
    DeepgramFluxSTTService,
    DeepgramFluxSTTSettings,
)
from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
from pipecat.services.deepgram.tts import DeepgramTTSService, DeepgramTTSSettings
from pipecat.services.dograh.flux.stt import DograhFluxSTTService
from pipecat.services.dograh.llm import DograhLLMService
from pipecat.services.dograh.stt import DograhSTTService, DograhSTTSettings
from pipecat.services.dograh.tts import DograhTTSService, DograhTTSSettings
from pipecat.services.elevenlabs.stt import (
    CommitStrategy,
    ElevenLabsRealtimeSTTService,
    ElevenLabsRealtimeSTTSettings,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings
from pipecat.services.gladia.stt import GladiaSTTService, GladiaSTTSettings
from pipecat.services.google.llm import GoogleLLMService, GoogleLLMSettings
from pipecat.services.google.stt import GoogleSTTService, GoogleSTTSettings
from pipecat.services.google.tts import GoogleTTSService, GoogleTTSSettings
from pipecat.services.google.vertex.llm import (
    GoogleVertexLLMService,
    GoogleVertexLLMSettings,
)
from pipecat.services.groq.llm import GroqLLMService, GroqLLMSettings
from pipecat.services.huggingface.llm import (
    HuggingFaceLLMService,
    HuggingFaceLLMSettings,
)
from pipecat.services.huggingface.stt import (
    HuggingFaceSTTService,
    HuggingFaceSTTSettings,
)
from pipecat.services.inworld.tts import InworldTTSService, InworldTTSSettings
from pipecat.services.minimax.llm import MiniMaxLLMService
from pipecat.services.minimax.tts import MiniMaxTTSSettings
from pipecat.services.openai._constants import OPENAI_SAMPLE_RATE
from pipecat.services.openai.base_llm import OpenAILLMSettings
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import (
    OpenAISTTService,
    OpenAISTTSettings,
)
from pipecat.services.openai.tts import OpenAITTSService, OpenAITTSSettings
from pipecat.services.openrouter.llm import OpenRouterLLMService, OpenRouterLLMSettings
from pipecat.services.rime.tts import RimeTTSService, RimeTTSSettings
from pipecat.services.sarvam.llm import SarvamLLMService, SarvamLLMSettings
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.services.smallest.stt import SmallestSTTService, SmallestSTTSettings
from pipecat.services.smallest.tts import SmallestTTSService, SmallestTTSSettings
from pipecat.services.speaches.llm import SpeachesLLMService, SpeachesLLMSettings
from pipecat.services.speaches.stt import SpeachesSTTService, SpeachesSTTSettings
from pipecat.services.speaches.tts import SpeachesTTSService, SpeachesTTSSettings
from pipecat.services.speechmatics.stt import (
    SpeechmaticsSTTService,
    SpeechmaticsSTTSettings,
)
from pipecat.services.tts_service import TextAggregationMode
from pipecat.services.xai.tts import XAITTSService, XAIWebsocketTTSSettings
from pipecat.transcriptions.language import Language
from pipecat.utils.text.xml_function_tag_filter import XMLFunctionTagFilter

if TYPE_CHECKING:
    from api.services.pipecat.audio_config import AudioConfig


DEEPGRAM_FLUX_LANGUAGE_HINTS = {
    "de": Language.DE,
    "en": Language.EN,
    "es": Language.ES,
    "fr": Language.FR,
    "hi": Language.HI,
    "it": Language.IT,
    "ja": Language.JA,
    "nl": Language.NL,
    "pt": Language.PT,
    "ru": Language.RU,
}


def decibyl_stt_uses_flux_language(language: str | None) -> bool:
    language = language or "multi"
    return language in DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS


def _resolve_elevenlabs_stt_language(
    language_code: str | None,
) -> Language | str | None:
    if not language_code or language_code == "auto":
        return None
    try:
        return Language(language_code)
    except ValueError:
        return language_code


def _elevenlabs_websocket_url(base_url: str) -> str:
    """Normalize an ElevenLabs API base URL for WebSocket clients."""
    base_url = base_url.strip()
    parsed = urlparse(base_url)
    if not parsed.netloc:
        return base_url.rstrip("/")

    websocket_scheme = {
        "http": "ws",
        "https": "wss",
    }.get(parsed.scheme, parsed.scheme)
    return urlunparse(
        parsed._replace(
            scheme=websocket_scheme,
            path=parsed.path.rstrip("/"),
        )
    )


def _elevenlabs_realtime_stt_host(base_url: str) -> str:
    """Return the host/path prefix Pipecat's ElevenLabs realtime STT expects.

    Pipecat's realtime STT service builds
    ``wss://{host}/v1/speech-to-text/realtime`` internally, so remove the scheme
    from the same normalized WebSocket URL used by ElevenLabs TTS. Preserve
    netloc (including optional ports) and any path prefix used by BYOK proxies.
    """
    websocket_url = _elevenlabs_websocket_url(base_url)
    parsed = urlparse(websocket_url)
    if parsed.netloc:
        path = parsed.path
        return f"{parsed.netloc}{path}" if path else parsed.netloc
    return websocket_url


def stt_uses_external_turns(user_config) -> bool:
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        return user_config.stt.model in DEEPGRAM_FLUX_MODELS
    if user_config.stt.provider == ServiceProviders.DECIBYL.value:
        return decibyl_stt_uses_flux_language(
            getattr(user_config.stt, "language", None)
        )
    if user_config.stt.provider == ServiceProviders.CARTESIA.value:
        return user_config.stt.model == "ink-2"
    return False


class DecibylGoogleLLMService(GoogleLLMService):
    adapter_class = DecibylGeminiJSONSchemaAdapter


class DecibylGoogleVertexLLMService(GoogleVertexLLMService):
    adapter_class = DecibylGeminiJSONSchemaAdapter


def _validate_runtime_service_url(url: str, field_name: str) -> None:
    try:
        validate_user_configured_service_url(
            url,
            field_name=field_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def create_stt_service(
    user_config,
    audio_config: "AudioConfig",
    keyterms: list[str] | None = None,
    correlation_id: str | None = None,
):
    """Create and return appropriate STT service based on user configuration

    Args:
        user_config: User configuration containing STT settings
        keyterms: Optional list of keyterms for speech recognition boosting (Deepgram only)
    """
    logger.info(
        f"Creating STT service: provider={user_config.stt.provider}, model={user_config.stt.model}"
    )
    if user_config.stt.provider == ServiceProviders.DEEPGRAM.value:
        if user_config.stt.model in DEEPGRAM_FLUX_MODELS:
            settings_kwargs = {
                "model": user_config.stt.model,
                "eot_timeout_ms": 3000,
                "eot_threshold": 0.7,
                "eager_eot_threshold": 0.5,
                "keyterm": keyterms or [],
            }
            if user_config.stt.model == "flux-general-multi":
                language = getattr(user_config.stt, "language", None)
                language_hint = DEEPGRAM_FLUX_LANGUAGE_HINTS.get(language)
                if language_hint:
                    settings_kwargs["language_hints"] = [language_hint]

            return DeepgramFluxSTTService(
                api_key=user_config.stt.api_key,
                settings=DeepgramFluxSTTSettings(**settings_kwargs),
                should_interrupt=False,  # Let UserAggregator take care of sending InterruptionFrame
                sample_rate=audio_config.transport_in_sample_rate,
            )

        # Other models than flux
        # Use language from user config, defaulting to "multi" for multilingual support
        language = getattr(user_config.stt, "language", None) or "multi"
        logger.debug(f"Using DeepGram Model - {user_config.stt.model}")
        return DeepgramSTTService(
            api_key=user_config.stt.api_key,
            settings=DeepgramSTTSettings(
                language=language,
                profanity_filter=False,
                endpointing=100,
                model=user_config.stt.model,
                keyterm=keyterms or [],
            ),
            should_interrupt=False,  # Let UserAggregator take care of sending InterruptionFrame
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.OPENAI.value:
        kwargs = {}
        base_url = getattr(user_config.stt, "base_url", None)
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return OpenAISTTService(
            api_key=user_config.stt.api_key,
            settings=OpenAISTTSettings(model=user_config.stt.model),
            should_interrupt=False,  # Let UserAggregator own interruption confirmation.
            **kwargs,
        )
    elif user_config.stt.provider == ServiceProviders.GOOGLE.value:
        language = getattr(user_config.stt, "language", None) or "en-US"
        location = getattr(user_config.stt, "location", None) or "global"
        credentials = getattr(user_config.stt, "credentials", None)

        settings_kwargs = {"model": user_config.stt.model}
        try:
            settings_kwargs["languages"] = [Language(language)]
        except ValueError:
            settings_kwargs["language_codes"] = [language]

        return GoogleSTTService(
            credentials=credentials,
            location=location,
            settings=GoogleSTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.CARTESIA.value:
        if user_config.stt.model == "ink-2":
            return CartesiaTurnsSTTService(
                api_key=user_config.stt.api_key,
                should_interrupt=False,  # Let UserAggregator emit interruption frames.
                sample_rate=audio_config.transport_in_sample_rate,
            )

        language = getattr(user_config.stt, "language", None) or "en"
        return CartesiaSTTService(
            api_key=user_config.stt.api_key,
            settings=CartesiaSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.DECIBYL.value:
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        language = getattr(user_config.stt, "language", None) or "multi"

        if decibyl_stt_uses_flux_language(language):
            # Decibyl's Flux proxy only supports multilingual auto-detect and the
            # same language hint subset as Deepgram Flux multilingual.
            settings_kwargs = {
                "model": "flux-general-multi",
                "eot_timeout_ms": 3000,
                "eot_threshold": 0.7,
                "eager_eot_threshold": 0.5,
                "keyterm": keyterms or [],
            }
            language_hint = DEEPGRAM_FLUX_LANGUAGE_HINTS.get(language)
            if language_hint:
                settings_kwargs["language_hints"] = [language_hint]
            return DograhFluxSTTService(
                base_url=base_url,
                api_key=user_config.stt.api_key,
                correlation_id=correlation_id,
                settings=DeepgramFluxSTTSettings(**settings_kwargs),
                should_interrupt=False,  # external turn strategies own interruption
                sample_rate=audio_config.transport_in_sample_rate,
            )

        return DograhSTTService(
            base_url=base_url,
            api_key=user_config.stt.api_key,
            correlation_id=correlation_id,
            settings=DograhSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            keyterms=keyterms,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SARVAM.value:
        language = getattr(user_config.stt, "language", None)
        language_mapping = {
            "bn-IN": Language.BN_IN,
            "gu-IN": Language.GU_IN,
            "hi-IN": Language.HI_IN,
            "kn-IN": Language.KN_IN,
            "ml-IN": Language.ML_IN,
            "mr-IN": Language.MR_IN,
            "ta-IN": Language.TA_IN,
            "te-IN": Language.TE_IN,
            "pa-IN": Language.PA_IN,
            "od-IN": Language.OR_IN,
            "en-IN": Language.EN_IN,
            "as-IN": Language.AS_IN,
            "ur-IN": Language.UR_IN,
            "kok-IN": Language.KOK_IN,
            "mai-IN": Language.MAI_IN,
            "sd-IN": Language.SD_IN,
        }
        if not language or language == "unknown":
            pipecat_language = None
        elif language in language_mapping:
            pipecat_language = language_mapping[language]
        else:
            # Unmapped BCP-47 codes pass through; Sarvam accepts them per https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe
            pipecat_language = language
        return SarvamSTTService(
            api_key=user_config.stt.api_key,
            settings=SarvamSTTSettings(
                model=user_config.stt.model,
                language=pipecat_language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEACHES.value:
        language = getattr(user_config.stt, "language", None)
        _validate_runtime_service_url(user_config.stt.base_url, "base_url")
        return SpeachesSTTService(
            base_url=user_config.stt.base_url,
            api_key=user_config.stt.api_key or "none",
            settings=SpeachesSTTSettings(
                model=user_config.stt.model,
                language=language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.HUGGINGFACE.value:
        base_url = (
            getattr(user_config.stt, "base_url", None)
            or "https://router.huggingface.co/hf-inference"
        )
        _validate_runtime_service_url(base_url, "base_url")
        return HuggingFaceSTTService(
            api_key=user_config.stt.api_key,
            base_url=base_url,
            bill_to=getattr(user_config.stt, "bill_to", None),
            settings=HuggingFaceSTTSettings(
                model=user_config.stt.model,
                return_timestamps=getattr(user_config.stt, "return_timestamps", False),
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.ASSEMBLYAI.value:
        language = getattr(user_config.stt, "language", None)
        settings_kwargs = {"model": user_config.stt.model, "language": language}
        if keyterms:
            settings_kwargs["keyterms_prompt"] = keyterms
        return AssemblyAISTTService(
            api_key=user_config.stt.api_key,
            settings=AssemblyAISTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.GLADIA.value:
        from pipecat.services.gladia.config import LanguageConfig

        language = getattr(user_config.stt, "language", None) or "en"
        settings_kwargs = {
            "model": user_config.stt.model,
            "language_config": LanguageConfig(
                languages=[language], code_switching=False
            ),
        }
        return GladiaSTTService(
            api_key=user_config.stt.api_key,
            settings=GladiaSTTSettings(**settings_kwargs),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SPEECHMATICS.value:
        from pipecat.services.speechmatics.stt import (
            AdditionalVocabEntry,
            OperatingPoint,
        )

        language = getattr(user_config.stt, "language", None) or "en"
        # Map model field to operating point (standard or enhanced)
        operating_point = (
            OperatingPoint.ENHANCED
            if user_config.stt.model == "enhanced"
            else OperatingPoint.STANDARD
        )
        # Convert keyterms to AdditionalVocabEntry objects for Speechmatics
        additional_vocab = []
        if keyterms:
            additional_vocab = [AdditionalVocabEntry(content=term) for term in keyterms]
        return SpeechmaticsSTTService(
            api_key=user_config.stt.api_key,
            settings=SpeechmaticsSTTSettings(
                language=language,
                operating_point=operating_point,
                additional_vocab=additional_vocab,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.AZURE_SPEECH.value:
        from pipecat.transcriptions.language import Language as PipecatLanguage

        language_code = getattr(user_config.stt, "language", None) or "en-US"
        region = getattr(user_config.stt, "region", None) or "eastus"
        try:
            pipecat_language = PipecatLanguage(language_code)
        except ValueError:
            pipecat_language = language_code
        return AzureSTTService(
            api_key=user_config.stt.api_key,
            region=region,
            settings=AzureSTTSettings(language=pipecat_language),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.SMALLEST.value:
        language_code = getattr(user_config.stt, "language", None) or "en"
        try:
            pipecat_language = Language(language_code)
        except ValueError:
            pipecat_language = Language.EN
        return SmallestSTTService(
            api_key=user_config.stt.api_key,
            settings=SmallestSTTSettings(
                model=user_config.stt.model,
                language=pipecat_language,
            ),
            sample_rate=audio_config.transport_in_sample_rate,
        )
    elif user_config.stt.provider == ServiceProviders.ELEVENLABS.value:
        language_code = getattr(user_config.stt, "language", None)
        pipecat_language = _resolve_elevenlabs_stt_language(language_code)

        _validate_runtime_service_url(user_config.stt.base_url, "base_url")
        elevenlabs_host = _elevenlabs_realtime_stt_host(user_config.stt.base_url)

        return ElevenLabsRealtimeSTTService(
            api_key=user_config.stt.api_key,
            base_url=elevenlabs_host,
            commit_strategy=CommitStrategy.VAD,
            settings=ElevenLabsRealtimeSTTSettings(
                model=user_config.stt.model,
                language=pipecat_language,
            ),
            should_interrupt=False,
            sample_rate=audio_config.transport_in_sample_rate,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid STT provider {user_config.stt.provider}"
        )


# Providers whose TTS class holds one connection open across synthesis calls,
# so feeding it LLM tokens as they arrive starts audio sooner instead of
# starting a new request per token.
#
# Membership is per *class the factory actually constructs*, not per vendor.
# Several vendors ship both a websocket and an HTTP service and we pick one;
# the vendor supporting streaming somewhere is not the question.
#
#   deepgram    DeepgramTTSService      websocket base class
#   elevenlabs  ElevenLabsTTSService    websocket base class — also derives its
#                                       own auto_mode from this setting, so it
#                                       needs no separate tuning here
#   cartesia    CartesiaTTSService      websocket base class
#   inworld     InworldTTSService       websocket base class
#   decibyl     DograhTTSService        websocket base class
#   rime        RimeTTSService          websocket base class
#   sarvam      SarvamTTSService        persistent websocket, sends text on it
#   smallest    SmallestTTSService      persistent websocket, sends text on it
#   xai         XAITTSService           websocket base class. The factory built
#                                       xAI's HTTP class until this audit found
#                                       the websocket one sitting unused beside
#                                       it; the settings are a superset, so the
#                                       swap cost one renamed argument.
_LOW_LATENCY_STREAMING_TTS_PROVIDERS = frozenset(
    {
        ServiceProviders.DEEPGRAM.value,
        ServiceProviders.ELEVENLABS.value,
        ServiceProviders.CARTESIA.value,
        ServiceProviders.INWORLD.value,
        ServiceProviders.DECIBYL.value,
        ServiceProviders.RIME.value,
        ServiceProviders.SARVAM.value,
        ServiceProviders.SMALLEST.value,
        ServiceProviders.XAI.value,
    }
)

# The rest, and why each one is not an oversight. Written down because "some
# providers got the low-latency setting and some didn't" reads like unfinished
# work, and re-deriving the answer means reading a TTS class per provider.
#
#   openai      OpenAITTSService        one HTTP request per synthesis
#   speaches    SpeachesTTSService      subclasses OpenAITTSService
#   camb        CambTTSService          one HTTP request per synthesis
#   minimax     MiniMaxHttpTTS (ours)   one HTTP request per synthesis. The
#                                       "OwnedSession" in our subclass is an
#                                       aiohttp session's lifecycle, not a
#                                       streaming session.
#   google      GoogleTTSService        websocket-free; builds a fresh
#                                       StreamingSynthesizeConfig and opens a
#                                       new gRPC streaming call per run_tts,
#                                       so token mode means one call per token
#   azure_speech AzureTTSService        the interesting one. Its connection
#                                       *is* persistent, but each run_tts wraps
#                                       the text in SSML for a discrete
#                                       speak_ssml_async, and it drains the
#                                       audio queue on entry — so a second call
#                                       arriving mid-playback discards the
#                                       first one's remaining audio. Token mode
#                                       here would chop speech, not speed it up.
#
#   rumik       RumikTTSService does hold a persistent websocket, like Sarvam
#               and Smallest — but it defaults to full_response_aggregation and
#               replaces self._text_aggregator with its own
#               _FullResponseTextAggregator *after* calling super().__init__().
#               A text_aggregation_mode passed here is therefore overwritten
#               and has no effect: adding rumik to the streaming set would look
#               like a latency fix and change nothing at all.
_REQUEST_BASED_TTS_PROVIDERS = frozenset(
    {
        ServiceProviders.OPENAI.value,
        ServiceProviders.SPEACHES.value,
        ServiceProviders.CAMB.value,
        ServiceProviders.MINIMAX.value,
        ServiceProviders.GOOGLE.value,
        ServiceProviders.AZURE_SPEECH.value,
        ServiceProviders.RUMIK.value,
    }
)


def _create_tts_service_instance(provider, service, /, **kwargs):
    """Apply the transport-aware TTS latency policy to every provider.

    Persistent streaming transports receive LLM tokens immediately and buffer
    provider-side while generation continues. Request-based transports retain
    Pipecat's sentence default: token mode would create one synthesis request
    per token, increasing latency, cost, and audible seams.
    """
    if provider in _LOW_LATENCY_STREAMING_TTS_PROVIDERS:
        kwargs["text_aggregation_mode"] = TextAggregationMode.TOKEN
    return service(**kwargs)


def create_tts_service(
    user_config, audio_config: "AudioConfig", correlation_id: str | None = None
):
    """Create and return appropriate TTS service based on user configuration

    Args:
        user_config: User configuration containing TTS settings
        transport_type: Type of transport (e.g., 'twilio', 'webrtc')
    """
    logger.info(
        f"Creating TTS service: provider={user_config.tts.provider}, model={user_config.tts.model}"
    )
    # Create function call filter to prevent TTS from speaking function call tags
    xml_function_tag_filter = XMLFunctionTagFilter()
    if user_config.tts.provider == ServiceProviders.DEEPGRAM.value:
        return _create_tts_service_instance(
            user_config.tts.provider,
            DeepgramTTSService,
            api_key=user_config.tts.api_key,
            settings=DeepgramTTSSettings(voice=user_config.tts.voice),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.OPENAI.value:
        kwargs = {}
        base_url = getattr(user_config.tts, "base_url", None)
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return _create_tts_service_instance(
            user_config.tts.provider,
            OpenAITTSService,
            api_key=user_config.tts.api_key,
            sample_rate=OPENAI_SAMPLE_RATE,
            settings=OpenAITTSSettings(model=user_config.tts.model),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
            **kwargs,
        )
    elif user_config.tts.provider == ServiceProviders.GOOGLE.value:
        model = getattr(user_config.tts, "model", None) or "chirp_3_hd"
        language = getattr(user_config.tts, "language", None) or "en-US"
        voice = getattr(user_config.tts, "voice", None) or "en-US-Chirp3-HD-Charon"
        speed = getattr(user_config.tts, "speed", None)
        location = getattr(user_config.tts, "location", None) or None
        credentials = getattr(user_config.tts, "credentials", None)

        settings_kwargs = {
            "model": model,
            "voice": voice,
            "language": language,
        }
        if speed is not None and speed != 1.0:
            settings_kwargs["speaking_rate"] = speed

        return _create_tts_service_instance(
            user_config.tts.provider,
            GoogleTTSService,
            credentials=credentials,
            location=location,
            settings=GoogleTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.ELEVENLABS.value:
        # Backward compatible with older configuration "Name - voice_id"
        try:
            voice_id = user_config.tts.voice.split(" - ")[1]
        except IndexError:
            voice_id = user_config.tts.voice
        # ElevenLabs TTS consumes the full normalized WebSocket URL. Realtime
        # STT uses the same normalization before adapting it to Pipecat's
        # scheme-less base_url contract.
        # getattr with the class default, not a direct read. After
        # managed_resolution a section can answer provider == "elevenlabs"
        # while being a Decibyl tier class, which carries voice and speed but
        # no endpoint -- reading it directly is an AttributeError at pipeline
        # build, i.e. a call that connects and dies before its first frame.
        # Latent while the managed TTS tier points at Sarvam, but MANAGED_TTS_*
        # repoints tiers from the environment, so it is one ops change away.
        elevenlabs_base_url = (
            getattr(user_config.tts, "base_url", None) or "https://api.elevenlabs.io"
        )
        _validate_runtime_service_url(elevenlabs_base_url, "base_url")
        elevenlabs_url = _elevenlabs_websocket_url(elevenlabs_base_url)
        return _create_tts_service_instance(
            user_config.tts.provider,
            ElevenLabsTTSService,
            reconnect_on_error=False,
            api_key=user_config.tts.api_key,
            url=elevenlabs_url,
            settings=ElevenLabsTTSSettings(
                voice=voice_id,
                model=user_config.tts.model,
                # getattr, not attribute access: after managed_resolution a
                # section can be a Decibyl tier class answering
                # provider == "elevenlabs" while carrying no tuning fields at
                # all -- the same reason _carry() guards every field it copies.
                # The fallbacks are the literals this branch used to pass, so a
                # tier slot still sounds exactly as it did.
                stability=getattr(user_config.tts, "stability", 0.8),
                speed=user_config.tts.speed,
                similarity_boost=getattr(
                    user_config.tts, "similarity_boost", 0.75
                ),
                style=getattr(user_config.tts, "style", 0.0),
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.CARTESIA.value:
        speed = getattr(user_config.tts, "speed", None)
        volume = getattr(user_config.tts, "volume", None)
        gen_config_kwargs = {}
        if speed and speed != 1.0:
            gen_config_kwargs["speed"] = speed
        if volume and volume != 1.0:
            gen_config_kwargs["volume"] = volume
        generation_config = (
            GenerationConfig(**gen_config_kwargs) if gen_config_kwargs else None
        )
        language = getattr(user_config.tts, "language", None) or "en"
        return _create_tts_service_instance(
            user_config.tts.provider,
            CartesiaTTSService,
            api_key=user_config.tts.api_key,
            settings=CartesiaTTSSettings(
                voice=user_config.tts.voice,
                model=user_config.tts.model,
                language=language,
                **(
                    {"generation_config": generation_config}
                    if generation_config
                    else {}
                ),
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.INWORLD.value:
        voice = getattr(user_config.tts, "voice", None) or "Ashley"
        model = getattr(user_config.tts, "model", None) or "inworld-tts-2"
        speed = getattr(user_config.tts, "speed", None)
        language = getattr(user_config.tts, "language", None) or "en-US"
        delivery_mode = getattr(user_config.tts, "delivery_mode", None) or "BALANCED"
        return _create_tts_service_instance(
            user_config.tts.provider,
            InworldTTSService,
            api_key=user_config.tts.api_key,
            settings=InworldTTSSettings(
                voice=voice,
                model=model,
                language=language,
                speaking_rate=speed,
                delivery_mode=delivery_mode,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.DECIBYL.value:
        # Convert HTTP URL to WebSocket URL for TTS
        base_url = MPS_API_URL.replace("http://", "ws://").replace("https://", "wss://")
        return _create_tts_service_instance(
            user_config.tts.provider,
            DograhTTSService,
            base_url=base_url,
            api_key=user_config.tts.api_key,
            correlation_id=correlation_id,
            settings=DograhTTSSettings(
                model=user_config.tts.model,
                voice=user_config.tts.voice,
                speed=user_config.tts.speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.CAMB.value:
        from pipecat.services.camb.tts import CambTTSService

        voice_id = int(getattr(user_config.tts, "voice", None) or "147320")
        language = getattr(user_config.tts, "language", None) or "en-us"
        tts = _create_tts_service_instance(
            user_config.tts.provider,
            CambTTSService,
            api_key=user_config.tts.api_key,
            voice_id=voice_id,
            model=user_config.tts.model,
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
        )
        # Set language directly as BCP-47 code (bypasses Language enum conversion)
        tts._settings.language = language
        return tts
    elif user_config.tts.provider == ServiceProviders.SPEACHES.value:
        speaches_base_url = (
            getattr(user_config.tts, "base_url", None) or "http://localhost:8000/v1"
        )
        _validate_runtime_service_url(speaches_base_url, "base_url")
        return _create_tts_service_instance(
            user_config.tts.provider,
            SpeachesTTSService,
            base_url=speaches_base_url,
            api_key=user_config.tts.api_key or "none",
            settings=SpeachesTTSSettings(
                model=user_config.tts.model,
                voice=user_config.tts.voice,
                speed=user_config.tts.speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.RIME.value:
        speed = getattr(user_config.tts, "speed", None)
        language_code = getattr(user_config.tts, "language", None) or "en"
        rime_language_mapping = {
            "en": Language.EN,
            "de": Language.DE,
            "fr": Language.FR,
            "es": Language.ES,
            "hi": Language.HI,
        }
        pipecat_language = rime_language_mapping.get(language_code, Language.EN)
        settings_kwargs = {
            "voice": user_config.tts.voice,
            "model": user_config.tts.model,
            "language": pipecat_language,
        }
        if speed and speed != 1.0:
            settings_kwargs["speedAlpha"] = speed
        return _create_tts_service_instance(
            user_config.tts.provider,
            RimeTTSService,
            api_key=user_config.tts.api_key,
            settings=RimeTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.SARVAM.value:
        # Map Sarvam language code to pipecat Language enum for TTS
        language_mapping = {
            "bn-IN": Language.BN,
            "en-IN": Language.EN,
            "gu-IN": Language.GU,
            "hi-IN": Language.HI,
            "kn-IN": Language.KN,
            "ml-IN": Language.ML,
            "mr-IN": Language.MR,
            "od-IN": Language.OR,
            "pa-IN": Language.PA,
            "ta-IN": Language.TA,
            "te-IN": Language.TE,
        }
        language = getattr(user_config.tts, "language", None)
        pipecat_language = language_mapping.get(language, Language.HI)

        # "default" is *our* sentinel for "the customer never picked one", and
        # it is the value a managed configuration carries until they do. It is
        # not a Sarvam speaker: their v2 model takes one of seven names, and
        # pipecat only substitutes its own default when the voice is *unset* —
        # a voice of "default" is set, so it went to the vendor verbatim.
        #
        # Omitting the key instead lets pipecat apply the right default for
        # whichever model the tier resolves to, which is "anushka" on v2 and
        # "shubh" on v3. Hardcoding either here would be wrong the day the
        # tier moves.
        voice = (getattr(user_config.tts, "voice", None) or "").strip().lower()
        speed = getattr(user_config.tts, "speed", None)
        # Sarvam's WebSocket can synthesize while the LLM is still streaming.
        # Feed tokens immediately and let Sarvam aggregate a short provider-side
        # buffer. The previous sentence-first + 50-character buffering made TTS
        # first-byte latency include almost the entire first sentence (6.39s in
        # production run #67).
        #
        # **These two are Sarvam's numbers, not ours, and it validates them.**
        # The config is sent over the websocket at connect; a value outside the
        # accepted range is refused there, which surfaces as a fatal ErrorFrame
        # before the first word — a call that is answered and then dies at 0s,
        # not a call that sounds slightly wrong. Sarvam's API reference gives
        # min_buffer_size as 30-200 (default 50) and max_chunk_length as 50-500
        # (default 150), so 30 is the floor and the whole of the available win;
        # this shipped at 20, under it. Their Pipecat integration guide does
        # suggest 15-25, which contradicts their own reference — until that is
        # resolved with them, the documented floor is the number to hold, since
        # being wrong in this direction costs a little latency and being wrong
        # in the other costs every call.
        settings_kwargs = {
            "model": user_config.tts.model,
            "language": pipecat_language,
            "min_buffer_size": 30,
            "max_chunk_length": 80,
        }
        if voice and voice != DECIBYL_DEFAULT_VOICE:
            settings_kwargs["voice"] = voice
        if speed and speed != 1.0:
            settings_kwargs["pace"] = speed
        return _create_tts_service_instance(
            user_config.tts.provider,
            SarvamTTSService,
            api_key=user_config.tts.api_key,
            settings=SarvamTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.RUMIK.value:
        # Rumik ships its own pipecat service, so there is no client to write
        # here — only the mapping from our configuration to theirs.
        from pipecat_rumik import RumikTTSService

        model = getattr(user_config.tts, "model", None) or "mulberry"
        voice = (getattr(user_config.tts, "voice", None) or "").strip().lower()
        description = (
            getattr(user_config.tts, "description", None) or ""
        ).strip() or RUMIK_DEFAULT_DESCRIPTION

        rumik_settings: dict[str, Any] = {
            "model": model,
            "description": description,
            "language": getattr(user_config.tts, "language", None) or "hi-IN",
        }
        # Muga takes no preset voice — it is directed by tone tags in the text.
        # Sending one would be ignored at best and generate an unrelated voice
        # at worst, since Rumik treats an unknown speaker as a description hint.
        if voice and model != "muga":
            rumik_settings["voice"] = voice

        return _create_tts_service_instance(
            user_config.tts.provider,
            RumikTTSService,
            api_key=user_config.tts.api_key,
            gateway_url=RUMIK_GATEWAY_URL,
            settings=RumikTTSService.Settings(**rumik_settings),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
        )
    elif user_config.tts.provider == ServiceProviders.MINIMAX.value:
        group_id = getattr(user_config.tts, "group_id", None)
        if not group_id:
            raise HTTPException(
                status_code=400,
                detail="MiniMax TTS requires a group_id. Configure it in your TTS settings.",
            )
        voice = getattr(user_config.tts, "voice", None) or "English_Graceful_Lady"
        speed = getattr(user_config.tts, "speed", None) or 1.0

        # Pipecat appends "?GroupId=..." to base_url as-is, so /t2a_v2 must
        # already be in the path.
        base_url = (
            getattr(user_config.tts, "base_url", None)
            or "https://api.minimax.io/v1/t2a_v2"
        ).rstrip("/")
        if not base_url.endswith("/t2a_v2"):
            base_url = f"{base_url}/t2a_v2"
        _validate_runtime_service_url(base_url, "base_url")

        session = aiohttp.ClientSession()
        return _create_tts_service_instance(
            user_config.tts.provider,
            MiniMaxOwnedSessionTTSService,
            api_key=user_config.tts.api_key,
            group_id=group_id,
            base_url=base_url,
            aiohttp_session=session,
            settings=MiniMaxTTSSettings(
                model=user_config.tts.model,
                voice=voice,
                speed=speed,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.AZURE_SPEECH.value:
        region = getattr(user_config.tts, "region", None) or "eastus"
        voice = getattr(user_config.tts, "voice", None) or "en-US-AriaNeural"
        language = getattr(user_config.tts, "language", None) or "en-US"
        speed = getattr(user_config.tts, "speed", None) or 1.0
        # Map speed multiplier (0.5–2.0) to Azure SSML rate string (e.g. "1.25")
        rate = str(speed) if speed != 1.0 else None
        settings_kwargs: dict = {
            "voice": voice,
            "language": language,
        }
        if rate:
            settings_kwargs["rate"] = rate
        return _create_tts_service_instance(
            user_config.tts.provider,
            AzureTTSService,
            api_key=user_config.tts.api_key,
            region=region,
            settings=AzureTTSSettings(**settings_kwargs),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.SMALLEST.value:
        language_code = getattr(user_config.tts, "language", None) or "en"
        try:
            pipecat_language = Language(language_code)
        except ValueError:
            pipecat_language = Language.EN
        speed = getattr(user_config.tts, "speed", None)
        model = user_config.tts.model.replace("lightning-v", "lightning_v")
        settings_kwargs = SmallestTTSSettings(
            model=model,
            voice=user_config.tts.voice,
            language=pipecat_language,
        )
        if speed and speed != 1.0:
            settings_kwargs.speed = speed
        return _create_tts_service_instance(
            user_config.tts.provider,
            SmallestTTSService,
            api_key=user_config.tts.api_key,
            settings=settings_kwargs,
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    elif user_config.tts.provider == ServiceProviders.XAI.value:
        voice = getattr(user_config.tts, "voice", None) or "eve"
        language_code = getattr(user_config.tts, "language", None) or "en"
        if language_code.lower() == "auto":
            pipecat_language = "auto"
        else:
            try:
                pipecat_language = Language(language_code)
            except ValueError:
                pipecat_language = Language.EN
        return _create_tts_service_instance(
            user_config.tts.provider,
            XAITTSService,
            api_key=user_config.tts.api_key,
            sample_rate=audio_config.transport_out_sample_rate,
            # The websocket class calls this "codec" where the HTTP one called
            # it "encoding". Same request: raw PCM, so nothing downstream has
            # to decode.
            codec="pcm",
            settings=XAIWebsocketTTSSettings(
                voice=voice,
                language=pipecat_language,
                # The websocket service defaults this on, which flips it to
                # emitting a TTSTextFrame per word instead of one aggregated
                # frame -- and xAI delivers those timings in bursts decoupled
                # from the audio. That is a change to what the transcript and
                # recording router see, which is not what this migration is
                # for. Off keeps the aggregated-text behaviour the HTTP class
                # had, so the only thing that changes here is the transport.
                with_timestamps=False,
            ),
            text_filters=[xml_function_tag_filter],
            skip_aggregator_types=["recording_router", "recording"],
            silence_time_s=1.0,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid TTS provider {user_config.tts.provider}"
        )


def _migrate_deprecated_google_model(model: str) -> str:
    """Google removed the ``gemini-2.0-flash*`` models. Transparently upgrade
    any stored config that still references them to the 2.5 equivalent so old
    user configurations keep working instead of failing at runtime."""
    if model and model.startswith("gemini-2.0-flash"):
        migrated = model.replace("gemini-2.0-", "gemini-2.5-", 1)
        logger.warning(
            f"Google model '{model}' is no longer supported; using '{migrated}' instead"
        )
        return migrated
    return model


def _llm_tuning(
    temperature: float | None,
    max_tokens: int | None,
    *,
    default_temperature: float | None = None,
) -> dict:
    """Settings kwargs for the generation controls, or nothing.

    ``default_temperature`` is whatever literal that provider's branch passed
    before these became configurable, so an unset config produces byte-identical
    settings and no existing call changes. A provider that passed no temperature
    at all keeps passing none.

    ``max_tokens`` is omitted unless set: every settings class here inherits
    the OpenAI-shaped settings base (or declares its own) and treats an absent
    value as the provider's default, which is not the same as a number we
    invented.

    (The base class is described rather than named. ``test_every_service_is_priced``
    derives "every service the factory can build" by regex over this file's
    source, so spelling a ``*Service`` class name in a comment invents a service
    that needs a rate row -- the same trap ``_carry`` records below.)
    """
    tuning: dict = {}
    resolved = temperature if temperature is not None else default_temperature
    if resolved is not None:
        tuning["temperature"] = resolved
    if max_tokens is not None:
        tuning["max_tokens"] = max_tokens
    return tuning


def create_llm_service_from_provider(
    provider: str,
    model: str,
    api_key: str | None,
    *,
    correlation_id: str | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    credentials: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    bill_to: str | None = None,
):
    """Create an LLM service from explicit provider/model/api_key.

    Also used by create_llm_service which extracts these from user_config.
    """
    logger.info(f"Creating LLM service: provider={provider}, model={model}")
    if provider == ServiceProviders.OPENAI.value:
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        if "gpt-5" in model:
            return OpenAILLMService(
                api_key=api_key,
                settings=OpenAILLMSettings(
                    model=model,
                    extra={"reasoning_effort": "minimal", "verbosity": "low"},
                    # No temperature, configured or otherwise: the reasoning
                    # models this branch exists for reject the parameter.
                    **_llm_tuning(None, max_tokens),
                ),
                **kwargs,
            )
        return OpenAILLMService(
            api_key=api_key,
            settings=OpenAILLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
            **kwargs,
        )
    elif provider == ServiceProviders.GROQ.value:
        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
        )
    elif provider == ServiceProviders.OPENROUTER.value:
        kwargs = {}
        if base_url:
            _validate_runtime_service_url(base_url, "base_url")
            kwargs["base_url"] = base_url
        return OpenRouterLLMService(
            api_key=api_key,
            settings=OpenRouterLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
            **kwargs,
        )
    elif provider == ServiceProviders.GOOGLE.value:
        model = _migrate_deprecated_google_model(model)
        return DecibylGoogleLLMService(
            api_key=api_key,
            settings=GoogleLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
        )
    elif provider == ServiceProviders.GOOGLE_VERTEX.value:
        return DecibylGoogleVertexLLMService(
            credentials=credentials,
            project_id=project_id,
            location=location or "us-east4",
            settings=GoogleVertexLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
        )
    elif provider == ServiceProviders.AZURE.value:
        if endpoint:
            _validate_runtime_service_url(endpoint, "endpoint")
        return AzureLLMService(
            api_key=api_key,
            endpoint=endpoint,
            settings=AzureLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
        )
    elif provider == ServiceProviders.DECIBYL.value:
        return DograhLLMService(
            base_url=f"{MPS_API_URL}/api/v1/llm",
            api_key=api_key,
            correlation_id=correlation_id,
            settings=OpenAILLMSettings(
                model=model, **_llm_tuning(temperature, max_tokens)
            ),
        )
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        return AWSBedrockLLMService(
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
            aws_region=aws_region,
            settings=AWSBedrockLLMSettings(
                model=model, **_llm_tuning(temperature, max_tokens)
            ),
        )
    elif provider == ServiceProviders.SPEACHES.value:
        base_url = base_url or "http://localhost:11434/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return SpeachesLLMService(
            base_url=base_url,
            api_key=api_key or "none",
            settings=SpeachesLLMSettings(
                model=model, **_llm_tuning(temperature, max_tokens)
            ),
        )
    elif provider == ServiceProviders.CUSTOM_LLM.value:
        # Built by OpenAILLMService because that is what "OpenAI-compatible"
        # means; the base_url is the whole configuration. Validated like every
        # other customer-supplied endpoint -- this one is by definition a URL we
        # have never seen, so the SSRF guard matters more here than anywhere.
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="base_url is required for a custom LLM endpoint",
            )
        _validate_runtime_service_url(base_url, "base_url")
        return OpenAILLMService(
            api_key=api_key or "none",
            base_url=base_url,
            settings=OpenAILLMSettings(
                model=model, **_llm_tuning(temperature, max_tokens)
            ),
        )
    elif provider == ServiceProviders.HUGGINGFACE.value:
        base_url = base_url or "https://router.huggingface.co/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return HuggingFaceLLMService(
            api_key=api_key,
            base_url=base_url,
            bill_to=bill_to,
            settings=HuggingFaceLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.1),
            ),
        )
    elif provider == ServiceProviders.MINIMAX.value:
        base_url = base_url or "https://api.minimax.io/v1"
        _validate_runtime_service_url(base_url, "base_url")
        return MiniMaxLLMService(
            api_key=api_key,
            base_url=base_url,
            settings=MiniMaxLLMService.Settings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=1.0),
            ),
        )
    elif provider == ServiceProviders.SARVAM.value:
        return SarvamLLMService(
            api_key=api_key,
            settings=SarvamLLMSettings(
                model=model,
                **_llm_tuning(temperature, max_tokens, default_temperature=0.5),
            ),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Invalid LLM provider {provider}")


#: Language values that mean "let the model work it out" rather than naming a
#: language. Kept alongside the pipeline follower's own set deliberately: both
#: halves of the product have to agree on what "not pinned" looks like.
_UNPINNED_LANGUAGES = frozenset({"auto", "multi", ""})


def _realtime_language_setting(language: str | None) -> str | None:
    """Translate a configured language into what Gemini Live's settings want.

    ``None`` is the load-bearing value and it is not the same as leaving the
    field out. Gemini Live's own defaults fill an omitted language with
    ``en-US``, so *not passing it* silently pins English — the exact opposite of
    what an operator choosing "auto" asked for. Passing ``None`` explicitly
    makes it through to ``SpeechConfig(language_code=None)``, which the Google
    SDK drops from the wire payload.

    That matters because the models shipped here are native-audio ones. They
    detect the caller's language and switch between languages mid-conversation
    by themselves, and Google documents ``language_code`` as unsupported for
    them. Pinning a code on one is at best ignored and at worst fights the
    behaviour we want.
    """
    if language is None or language.strip().lower() in _UNPINNED_LANGUAGES:
        return None
    return language


def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
    """Create a realtime (speech-to-speech) LLM service that handles STT+LLM+TTS.

    These services bypass separate STT/TTS and handle audio directly via
    a bidirectional WebSocket connection. Reads from user_config.realtime.
    """
    realtime_config = user_config.realtime
    provider = realtime_config.provider
    model = realtime_config.model
    api_key = realtime_config.api_key
    voice = getattr(realtime_config, "voice", None)
    language = getattr(realtime_config, "language", None)

    logger.info(
        f"Creating realtime LLM service: provider={provider}, model={model}, voice={voice}, language={language}"
    )

    if provider == ServiceProviders.OPENAI_REALTIME.value:
        from api.services.pipecat.realtime.openai_realtime import (
            DecibylOpenAIRealtimeLLMService,
        )
        from pipecat.services.openai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            AudioOutput,
            InputAudioTranscription,
            SessionProperties,
        )

        return DecibylOpenAIRealtimeLLMService(
            api_key=api_key,
            settings=DecibylOpenAIRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(),
                        ),
                        output=AudioOutput(
                            voice=voice or "alloy",
                        ),
                    ),
                ),
            ),
        )
    elif provider == ServiceProviders.GROK_REALTIME.value:
        from api.services.pipecat.realtime.grok_realtime import (
            DecibylGrokRealtimeLLMService,
        )
        from pipecat.services.xai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            InputAudioTranscription,
            SessionProperties,
        )

        grok_voice = voice or "ara"
        if grok_voice.lower() in {"ara", "rex", "sal", "eve", "leo"}:
            grok_voice = grok_voice.lower()

        return DecibylGrokRealtimeLLMService(
            api_key=api_key,
            settings=DecibylGrokRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    voice=grok_voice,
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(),
                        ),
                    ),
                ),
            ),
        )
    elif provider == ServiceProviders.ULTRAVOX_REALTIME.value:
        from api.services.pipecat.realtime.ultravox_realtime import (
            DecibylUltravoxOneShotInputParams,
            DecibylUltravoxRealtimeLLMService,
        )

        return DecibylUltravoxRealtimeLLMService(
            params=DecibylUltravoxOneShotInputParams(
                api_key=api_key,
                model=model,
                voice=voice,
                output_medium="voice",
            ),
            settings=DecibylUltravoxRealtimeLLMService.Settings(
                model=model,
                output_medium="voice",
            ),
        )
    elif provider == ServiceProviders.GOOGLE_REALTIME.value:
        from api.services.pipecat.realtime.gemini_live import (
            DecibylGeminiLiveLLMService,
        )

        # Gemini Live enables input/output audio transcription by default
        # in its _connect() method — no need to configure it explicitly.
        return DecibylGeminiLiveLLMService(
            api_key=api_key,
            settings=DecibylGeminiLiveLLMService.Settings(
                model=model,
                voice=voice or "Puck",
                language=_realtime_language_setting(language),
            ),
        )
    elif provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
        from api.services.pipecat.realtime.gemini_live_vertex import (
            DecibylGeminiLiveVertexLLMService,
        )

        project_id = getattr(realtime_config, "project_id", None)
        location = getattr(realtime_config, "location", None) or "us-east4"
        credentials = getattr(realtime_config, "credentials", None)

        return DecibylGeminiLiveVertexLLMService(
            credentials=credentials,
            project_id=project_id,
            location=location,
            settings=DecibylGeminiLiveVertexLLMService.Settings(
                model=model,
                voice=voice or "Charon",
                language=_realtime_language_setting(language),
            ),
        )
    elif provider == ServiceProviders.AZURE_REALTIME.value:
        from api.services.pipecat.realtime.azure_realtime import (
            DecibylAzureRealtimeLLMService,
        )
        from pipecat.services.openai.realtime.events import (
            AudioConfiguration,
            AudioInput,
            AudioOutput,
            InputAudioTranscription,
            SessionProperties,
        )

        endpoint = getattr(realtime_config, "endpoint", None) or ""
        if not endpoint:
            raise HTTPException(
                status_code=400,
                detail="Azure Realtime requires an endpoint.",
            )
        _validate_runtime_service_url(endpoint, "endpoint")
        api_version = getattr(realtime_config, "api_version", None) or "v1"
        parsed_endpoint = urlparse(endpoint)
        if api_version == "v1":
            # Azure's GA Realtime API uses the deployment name as `model` and
            # deliberately has no date-based api-version query parameter.
            path = "/openai/v1/realtime"
            query = urlencode({"model": model})
        else:
            # Preserve explicitly configured preview deployments while users
            # migrate. Microsoft deprecated this protocol on April 30, 2026.
            path = "/openai/realtime"
            query = urlencode({"api-version": api_version, "deployment": model})
        wss_url = urlunparse(
            (
                "wss",
                parsed_endpoint.netloc,
                path,
                "",
                query,
                "",
            )
        )
        return DecibylAzureRealtimeLLMService(
            api_key=api_key,
            base_url=wss_url,
            settings=DecibylAzureRealtimeLLMService.Settings(
                model=model,
                session_properties=SessionProperties(
                    audio=AudioConfiguration(
                        input=AudioInput(
                            transcription=InputAudioTranscription(),
                        ),
                        output=AudioOutput(
                            voice=voice or "alloy",
                        ),
                    ),
                ),
            ),
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid realtime LLM provider {provider}"
        )


def _carry(kwargs: dict, section, *fields: str) -> None:
    """Copy the vendor options a section actually carries into ``kwargs``.

    Guarded with ``hasattr`` rather than read directly, because after
    ``managed_resolution`` a section's ``provider`` no longer tells you what
    class it is. That module rewrites ``provider``, ``model`` and ``api_key``
    in place and leaves the object as whatever it was — deliberately, so the
    pipeline has one configuration object rather than two representations of
    the same thing (see its module docstring). A managed slot is therefore a
    Decibyl tier class answering ``provider == "openai"`` while carrying no
    ``base_url``: the tier classes in ``configuration/registry`` have no
    endpoint or tuning fields at all, by design, because the customer picked a
    tier rather than a vendor.

    (Those class names are spelled out in this module's tests rather than here.
    ``tests/test_every_service_is_priced.py`` derives "every service the factory
    can build" by regex over this file's source text, so naming a configuration
    class in a comment here makes it look like a service that needs a rate row.)

    Reading those fields unconditionally raised ``AttributeError`` inside the
    pipeline for *every* managed LLM tier — ``default``/``accurate`` resolve to
    openai and died on ``base_url``, ``lite``/``fast``/``zen`` resolve to sarvam
    and died on ``temperature``. The call connected, the pipeline crashed
    before its first frame, and the caller heard the line go dead with Plivo
    reporting "End Of XML Instructions".

    Omitting an absent field is also the correct behaviour, not just a way to
    avoid the crash: a managed section has no customer-chosen endpoint or
    tuning to honour, so the vendor's own default is exactly what it should
    run on — the same conclusion ``managed_resolution`` reaches when it resets
    a customer-set endpoint back to the default before handing over our key.
    """
    for field in fields:
        if hasattr(section, field):
            kwargs[field] = getattr(section, field)


def create_llm_service(user_config, correlation_id: str | None = None):
    """Create and return appropriate LLM service based on user configuration."""
    provider = user_config.llm.provider
    model = user_config.llm.model
    api_key = user_config.llm.api_key

    kwargs: dict = {}
    if provider == ServiceProviders.OPENAI.value:
        _carry(kwargs, user_config.llm, "base_url")
    elif provider == ServiceProviders.OPENROUTER.value:
        _carry(kwargs, user_config.llm, "base_url")
    elif provider == ServiceProviders.AZURE.value:
        _carry(kwargs, user_config.llm, "endpoint")
    elif provider == ServiceProviders.SPEACHES.value:
        _carry(kwargs, user_config.llm, "base_url")
    elif provider == ServiceProviders.CUSTOM_LLM.value:
        _carry(kwargs, user_config.llm, "base_url")
    elif provider == ServiceProviders.HUGGINGFACE.value:
        _carry(kwargs, user_config.llm, "base_url", "bill_to")
    elif provider == ServiceProviders.AWS_BEDROCK.value:
        _carry(
            kwargs,
            user_config.llm,
            "aws_access_key",
            "aws_secret_key",
            "aws_region",
        )
    elif provider == ServiceProviders.GOOGLE_VERTEX.value:
        _carry(kwargs, user_config.llm, "project_id", "location", "credentials")
    elif provider == ServiceProviders.MINIMAX.value:
        _carry(kwargs, user_config.llm, "base_url")

    # Every pipeline LLM class carries these (see PipelineLLMTuning); a managed
    # tier class carries neither, and _carry omits what is absent. Done once
    # after the branches rather than per provider, so a new provider gets the
    # generation controls by declaring the fields and nothing else.
    _carry(kwargs, user_config.llm, "temperature", "max_tokens")

    return create_llm_service_from_provider(
        provider,
        model,
        api_key,
        correlation_id=correlation_id,
        **kwargs,
    )

import random
from enum import Enum, auto
from typing import Annotated, Dict, Literal, Type, TypeVar, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)

from api.services.configuration.options import (
    AZURE_EMBEDDING_MODELS,
    AZURE_MODELS,
    AZURE_REALTIME_API_VERSIONS,
    AZURE_REALTIME_MODELS,
    AZURE_REALTIME_VOICES,
    AZURE_SPEECH_REGIONS,
    AZURE_SPEECH_STT_LANGUAGES,
    AZURE_SPEECH_TTS_LANGUAGES,
    AZURE_SPEECH_TTS_VOICES,
    CARTESIA_INK_2_STT_LANGUAGES,
    CARTESIA_INK_WHISPER_STT_LANGUAGES,
    CARTESIA_STT_LANGUAGES,
    CARTESIA_STT_MODELS,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
    DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES,
    DEEPGRAM_LANGUAGES,
    DEEPGRAM_STT_MODELS,
    ELEVENLABS_STT_LANGUAGES,
    ELEVENLABS_STT_MODELS,
    GLADIA_STT_LANGUAGES,
    GLADIA_STT_MODELS,
    GOOGLE_MODELS,
    GOOGLE_REALTIME_LANGUAGES,
    GOOGLE_REALTIME_MODELS,
    GOOGLE_REALTIME_VOICES,
    GOOGLE_STT_LANGUAGES,
    GOOGLE_STT_MODELS,
    GOOGLE_TTS_LANGUAGES,
    GOOGLE_TTS_MODELS,
    GOOGLE_TTS_VOICES,
    GOOGLE_VERTEX_REALTIME_LANGUAGES,
    GOOGLE_VERTEX_REALTIME_MODELS,
    GOOGLE_VERTEX_REALTIME_VOICES,
    RUMIK_DEFAULT_DESCRIPTION,
    RUMIK_LANGUAGES,
    RUMIK_TTS_MODELS,
    RUMIK_VOICES,
    SARVAM_LANGUAGES,
    SARVAM_LLM_MODELS,
    SARVAM_STT_LANGUAGES_V3,
    SARVAM_STT_LANGUAGES_V25,
    SARVAM_STT_MODELS,
    SARVAM_TTS_MODELS,
    SARVAM_V2_VOICES,
    SARVAM_V3_VOICES,
    SMALLEST_TTS_LANGUAGES,
    SMALLEST_TTS_MODELS,
    SMALLEST_TTS_PRO_VOICES,
    SMALLEST_TTS_VOICES,
    SPEECHMATICS_STT_LANGUAGES,
)
from api.services.configuration.options.google import GOOGLE_VERTEX_MODELS


class ServiceType(Enum):
    LLM = auto()
    TTS = auto()
    STT = auto()
    EMBEDDINGS = auto()
    REALTIME = auto()


class ServiceProviders(str, Enum):
    OPENAI = "openai"
    DEEPGRAM = "deepgram"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    INWORLD = "inworld"
    CARTESIA = "cartesia"
    # NEUPHONIC = "neuphonic"
    ELEVENLABS = "elevenlabs"
    GOOGLE = "google"
    AZURE = "azure"
    AZURE_SPEECH = "azure_speech"
    DECIBYL = "decibyl"
    SARVAM = "sarvam"
    RUMIK = "rumik"
    SPEECHMATICS = "speechmatics"
    CAMB = "camb"
    AWS_BEDROCK = "aws_bedrock"
    SPEACHES = "speaches"
    HUGGINGFACE = "huggingface"
    ASSEMBLYAI = "assemblyai"
    GLADIA = "gladia"
    RIME = "rime"
    MINIMAX = "minimax"
    GOOGLE_VERTEX = "google_vertex"
    OPENAI_REALTIME = "openai_realtime"
    GROK_REALTIME = "grok_realtime"
    ULTRAVOX_REALTIME = "ultravox_realtime"
    GOOGLE_REALTIME = "google_realtime"
    GOOGLE_VERTEX_REALTIME = "google_vertex_realtime"
    AZURE_REALTIME = "azure_realtime"
    SMALLEST = "smallest"
    XAI = "xai"
    CUSTOM_LLM = "custom_llm"


class BaseServiceConfiguration(BaseModel):
    provider: Literal[
        ServiceProviders.OPENAI,
        ServiceProviders.DEEPGRAM,
        ServiceProviders.GROQ,
        ServiceProviders.OPENROUTER,
        ServiceProviders.INWORLD,
        ServiceProviders.ELEVENLABS,
        ServiceProviders.GOOGLE,
        ServiceProviders.AZURE,
        ServiceProviders.AZURE_SPEECH,
        ServiceProviders.DECIBYL,
        ServiceProviders.AWS_BEDROCK,
        ServiceProviders.SPEACHES,
        ServiceProviders.HUGGINGFACE,
        ServiceProviders.ASSEMBLYAI,
        ServiceProviders.GLADIA,
        ServiceProviders.RIME,
        ServiceProviders.MINIMAX,
        ServiceProviders.GOOGLE_VERTEX,
        ServiceProviders.OPENAI_REALTIME,
        ServiceProviders.GROK_REALTIME,
        ServiceProviders.ULTRAVOX_REALTIME,
        ServiceProviders.GOOGLE_REALTIME,
        ServiceProviders.GOOGLE_VERTEX_REALTIME,
        ServiceProviders.AZURE_REALTIME,
        ServiceProviders.SARVAM,
        ServiceProviders.SMALLEST,
        ServiceProviders.XAI,
        ServiceProviders.CUSTOM_LLM,
    ]
    # Stays required. A vault-backed slot sends an explicit empty string, which
    # satisfies this and is a different statement from omitting the field: it
    # says "the key comes from somewhere else", and byok_resolution fills it
    # from the account's vault at dial time. Defaulting it would have let a
    # configuration that genuinely forgot a key validate silently, which is the
    # guard several provider tests rely on.
    api_key: str | list[str]

    # A real, customer-chosen provider and model (e.g. provider=openai,
    # model=gpt-4o), authenticated with *our* platform key instead of the
    # account's own. The older way to get a Decibyl-billed section is
    # provider=decibyl with model as a tier name ("fast"/"accurate"/...),
    # resolved to a real vendor by managed_tiers -- that path is untouched
    # and still works for existing saved configurations. This is the newer,
    # additive path: a genuine vendor and model chosen directly, with no tier
    # translation, for accounts that want the same model catalog BYOK offers
    # without holding their own key. See BaseServiceConfiguration.is_managed
    # and managed_resolution.apply.
    use_platform_key: bool = Field(
        default=False,
        description=(
            "Run this section on Decibyl's own key for the chosen provider, "
            "rather than the account's."
        ),
    )

    # Stamped by byok_resolution.apply() before either resolver mutates
    # ``provider``/``api_key`` -- the only place left that can still tell a
    # BYOK section from a managed one, since both resolvers converge on the
    # same shape (real vendor name, real key) afterward. A PrivateAttr rather
    # than a Field: a Field's ``exclude=True`` keeps it out of
    # ``.model_dump()`` but still puts it in the generated JSON Schema, which
    # is what FastAPI serves as this provider's form definition -- it would
    # have rendered as a mystery input on every provider's config screen.
    # PrivateAttr is invisible to both.
    _key_source: str | None = PrivateAttr(default=None)

    @property
    def key_source(self) -> str | None:
        return self._key_source

    @key_source.setter
    def key_source(self, value: str | None) -> None:
        self._key_source = value

    @property
    def is_managed(self) -> bool:
        """Whether this section runs on a Decibyl-held key rather than the
        account's own -- true for both the tier path (``provider="decibyl"``)
        and the direct path (a real provider with ``use_platform_key=True``).

        The one place this question gets asked, shared by byok_resolution and
        managed_resolution so the two definitions cannot drift apart -- see
        the ``key_source`` docstring above for why that matters.
        """
        provider = getattr(self, "provider", None)
        provider_value = provider.value if hasattr(provider, "value") else provider
        return provider_value == ServiceProviders.DECIBYL.value or bool(
            getattr(self, "use_platform_key", False)
        )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v):
        if v is None:
            return v
        if isinstance(v, list) and len(v) == 0:
            raise ValueError("api_key list must not be empty")
        return v

    def __getattribute__(self, name: str):
        if name == "api_key":
            value = super().__getattribute__(name)
            if value is None:
                return value
            if isinstance(value, list):
                return random.choice(value)
            return value
        return super().__getattribute__(name)

    def get_all_api_keys(self) -> list[str]:
        """Get all API keys as a list (bypasses random selection)."""
        value = super().__getattribute__("api_key")
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value]


class BaseLLMConfiguration(BaseServiceConfiguration):
    model: str


class BaseTTSConfiguration(BaseServiceConfiguration):
    model: str


class BaseSTTConfiguration(BaseServiceConfiguration):
    model: str


class BaseEmbeddingsConfiguration(BaseServiceConfiguration):
    model: str


# Unified registry for all service types
REGISTRY: Dict[ServiceType, Dict[str, Type[BaseServiceConfiguration]]] = {
    ServiceType.LLM: {},
    ServiceType.TTS: {},
    ServiceType.STT: {},
    ServiceType.EMBEDDINGS: {},
    ServiceType.REALTIME: {},
}

#: The components a key can be stored against, and the registry each is
#: declared in. Embeddings and realtime are deliberately absent: neither is a
#: billable cost component, and a key stored against them has nowhere to go.
_KEYED_COMPONENTS: tuple[tuple[str, ServiceType], ...] = (
    ("stt", ServiceType.STT),
    ("llm", ServiceType.LLM),
    ("tts", ServiceType.TTS),
)


#: A realtime (speech-to-speech) provider authenticates with the same vendor
#: account as its ordinary sibling, not a key of its own — OpenAI Realtime
#: bills to your OpenAI key, Gemini Live to your Google key. Kept as an
#: explicit table rather than derived by stripping "_realtime" off the name:
#: Grok Realtime bills through the xAI account (there is no "grok" LLM
#: provider to strip down to), and Ultravox has no non-realtime integration
#: in this codebase at all, so it maps to itself — its key is stored under
#: its own name, exactly like any other vendor.
REALTIME_KEY_PROVIDER: dict[str, str] = {
    ServiceProviders.OPENAI_REALTIME.value: ServiceProviders.OPENAI.value,
    ServiceProviders.GOOGLE_REALTIME.value: ServiceProviders.GOOGLE.value,
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value: ServiceProviders.GOOGLE_VERTEX.value,
    ServiceProviders.AZURE_REALTIME.value: ServiceProviders.AZURE.value,
    ServiceProviders.GROK_REALTIME.value: ServiceProviders.XAI.value,
    ServiceProviders.ULTRAVOX_REALTIME.value: ServiceProviders.ULTRAVOX_REALTIME.value,
}


def realtime_key_provider(provider: str) -> str | None:
    """Which vault provider a realtime vendor's key is authenticated under.

    ``None`` if ``provider`` is not a realtime provider at all — the caller's
    cue that no such fallback applies.
    """
    return REALTIME_KEY_PROVIDER.get((provider or "").strip().lower())


#: The inverse of ``REALTIME_KEY_PROVIDER`` — a bijection, since no two
#: realtime providers share a vault provider. Used to tell an operator who has
#: just connected a vendor's ordinary key which realtime provider name to ask
#: model discovery about.
_BASE_PROVIDER_REALTIME: dict[str, str] = {
    base: realtime for realtime, base in REALTIME_KEY_PROVIDER.items()
}


def realtime_provider_for(base_provider: str) -> str | None:
    """The realtime vendor unlocked by already holding ``base_provider``'s key.

    ``None`` if that vendor has no speech-to-speech sibling in this codebase.
    """
    return _BASE_PROVIDER_REALTIME.get((base_provider or "").strip().lower())


def components_for_provider(provider: str) -> tuple[str, ...]:
    """Which components this vendor can serve, in receipt order.

    Several vendors do more than one — Sarvam and ElevenLabs each do all three
    — and a customer holding one account with them holds one key. Asking them
    to paste it once per component is asking them to do the registry's lookup
    by hand.

    Empty for an unknown provider rather than raising: the caller is usually
    validating user input, and "this vendor serves nothing" is the answer.
    """
    provider = (provider or "").strip().lower()
    return tuple(
        component
        for component, service_type in _KEYED_COMPONENTS
        if provider in REGISTRY[service_type]
    )


def known_providers() -> dict[str, tuple[str, ...]]:
    """Every provider this codebase has integrated, and what it serves.

    Built by walking the same registries ``components_for_provider`` reads,
    rather than a list maintained by hand somewhere else — a vendor added to
    one component's registry appears here on the next request, with no second
    place to remember to update.

    Drives the provider-keys screen: a key-entry form that led with "which
    component" made adding one Sarvam key feel like adding three, because
    nothing on the form said the vendor already covers all of them. Leading
    with the vendor and reading its components off this answers "what will
    this key cover" before a key is ever pasted in.
    """
    providers: dict[str, tuple[str, ...]] = {}
    for component, service_type in _KEYED_COMPONENTS:
        for provider in REGISTRY[service_type]:
            # ``str(provider)`` rather than ``.value``: most entries are
            # ``ServiceProviders`` members, but a handful of providers register
            # under a plain string, and only ``str()`` is safe against both.
            name = provider.value if isinstance(provider, Enum) else str(provider)
            if name == ServiceProviders.DECIBYL.value:
                # The managed-tier sentinel, not a vendor. Nobody pastes a key
                # in under this name — ``organization_credentials._normalise``
                # refuses it for the same reason on the customer side.
                continue
            providers[name] = providers.get(name, ()) + (component,)

    # A realtime vendor is visible too, on whichever entry its key actually
    # authenticates against — a key that already unlocks speech-to-speech
    # should say so, rather than requiring the realtime provider's exact name
    # to be typed in by hand before that becomes visible.
    for key_provider in REALTIME_KEY_PROVIDER.values():
        providers[key_provider] = providers.get(key_provider, ()) + ("realtime",)
    return providers


T = TypeVar("T", bound=BaseServiceConfiguration)


def register_service(service_type: ServiceType):
    """Generic decorator for registering service configurations"""

    def decorator(cls: Type[T]) -> Type[T]:
        # Get provider from class attributes or field defaults
        provider = getattr(cls, "provider", None)
        if provider is None:
            # Try to get from model fields
            provider = cls.model_fields.get("provider", None)
            if provider is not None:
                provider = provider.default
        if provider is None:
            raise ValueError(f"Provider not specified for {cls.__name__}")

        REGISTRY[service_type][provider] = cls
        return cls

    return decorator


# Convenience decorators
def register_llm(cls: Type[BaseLLMConfiguration]):
    return register_service(ServiceType.LLM)(cls)


def register_tts(cls: Type[BaseTTSConfiguration]):
    return register_service(ServiceType.TTS)(cls)


def register_stt(cls: Type[BaseSTTConfiguration]):
    return register_service(ServiceType.STT)(cls)


def register_embeddings(cls: Type[BaseEmbeddingsConfiguration]):
    return register_service(ServiceType.EMBEDDINGS)(cls)


def provider_model_config(
    title: str,
    *,
    description: str | None = None,
    provider_docs_url: str | None = None,
) -> ConfigDict:
    json_schema_extra: dict[str, str] = {}
    if description is not None:
        json_schema_extra["description"] = description
    if provider_docs_url is not None:
        json_schema_extra["provider_docs_url"] = provider_docs_url
    if json_schema_extra:
        return ConfigDict(title=title, json_schema_extra=json_schema_extra)
    return ConfigDict(title=title)


###################################################### LLM ########################################################################

# Suggested models for each provider (used for UI dropdown)
OPENAI_PROVIDER_MODEL_CONFIG = provider_model_config("OpenAI")
CUSTOM_LLM_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Custom LLM",
    description=(
        "Any OpenAI-compatible chat-completions endpoint: a self-hosted "
        "model, a gateway, or a provider we do not list yet."
    ),
)
GOOGLE_PROVIDER_MODEL_CONFIG = provider_model_config("Google")
GROQ_PROVIDER_MODEL_CONFIG = provider_model_config("Groq")
OPENROUTER_PROVIDER_MODEL_CONFIG = provider_model_config("Open Router")
AZURE_OPENAI_PROVIDER_MODEL_CONFIG = provider_model_config("Azure OpenAI")
DECIBYL_PROVIDER_MODEL_CONFIG = provider_model_config("Decibyl")
AWS_BEDROCK_PROVIDER_MODEL_CONFIG = provider_model_config("AWS Bedrock")
GOOGLE_VERTEX_PROVIDER_MODEL_CONFIG = provider_model_config("Google Vertex")
OPENAI_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("OpenAI Realtime")
GROK_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Grok Realtime")
ULTRAVOX_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Ultravox Realtime")
GOOGLE_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Google Realtime")
GOOGLE_VERTEX_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Google Vertex Realtime"
)
DEEPGRAM_PROVIDER_MODEL_CONFIG = provider_model_config("Deepgram")
ELEVENLABS_PROVIDER_MODEL_CONFIG = provider_model_config("ElevenLabs")
CARTESIA_PROVIDER_MODEL_CONFIG = provider_model_config("Cartesia")
XAI_PROVIDER_MODEL_CONFIG = provider_model_config("xAI")
INWORLD_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Inworld",
    description=(
        "Inworld AI streaming text-to-speech with built-in and cloned voices. "
        "Defaults to the Ashley system voice on inworld-tts-2."
    ),
    provider_docs_url="https://docs.inworld.ai/tts/tts",
)
SARVAM_PROVIDER_MODEL_CONFIG = provider_model_config("Sarvam")
RUMIK_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Rumik",
    description=(
        "Silk voice models. Hindi and English only, including code-mixed, at "
        "roughly a third of the price per character of the alternatives."
    ),
    provider_docs_url="https://docs.rumik.ai/",
)
CAMB_PROVIDER_MODEL_CONFIG = provider_model_config("Camb.ai")
RIME_PROVIDER_MODEL_CONFIG = provider_model_config("Rime")
GOOGLE_CLOUD_PROVIDER_MODEL_CONFIG = provider_model_config("Google Cloud")
SPEECHMATICS_PROVIDER_MODEL_CONFIG = provider_model_config("Speechmatics")
ASSEMBLYAI_PROVIDER_MODEL_CONFIG = provider_model_config("AssemblyAI")
GLADIA_PROVIDER_MODEL_CONFIG = provider_model_config("Gladia")
SPEACHES_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Local Models (Speaches)",
    description=(
        "Self-hosted OpenAI-compatible local models. See the Speaches project "
        "for setup and supported backends."
    ),
    provider_docs_url="https://github.com/speaches-ai/speaches",
)
HUGGINGFACE_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Hugging Face",
    description="Hosted Hugging Face Inference Providers API for usage-based inference.",
    provider_docs_url="https://huggingface.co/docs/inference-providers/en/index",
)
AZURE_SPEECH_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Azure Speech Services",
    description="Azure Cognitive Services Speech — TTS and STT via the Azure Speech SDK.",
    provider_docs_url="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/",
)
AZURE_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Azure OpenAI Realtime",
    description="Azure OpenAI Realtime API — low-latency speech-to-speech conversations.",
    provider_docs_url="https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/realtime-audio-quickstart",
)

OPENAI_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-3.5-turbo",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "deepseek-r1-distill-llama-70b",
    "qwen-qwq-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "gemma2-9b-it",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
]
OPENROUTER_MODELS = [
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "anthropic/claude-sonnet-4",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat-v3-0324",
]
DECIBYL_LLM_MODELS = ["default", "accurate", "fast", "lite", "zen"]
AWS_BEDROCK_MODELS = [
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
]


class PipelineLLMTuning(BaseModel):
    """Generation controls every pipeline LLM accepts, declared in one place.

    Both are ``None`` by default and mean "say nothing to the provider", which
    is what makes adding them safe: the factory keeps whatever literal that
    branch already passed as its fallback, so no existing call changes until
    somebody moves a slider.

    A provider that wants a different default just redeclares the field --
    MiniMax and Sarvam already do, and their own declaration wins over this
    one.

    Not on ``BaseLLMConfiguration``: the realtime (speech-to-speech) classes
    inherit that too, and neither control reaches them through this path.
    """

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description=(
            "How much the wording varies between runs. Low is repeatable and "
            "sticks to the prompt; high is more creative and drifts from it "
            "more often."
        ),
    )
    max_tokens: int | None = Field(
        default=None,
        ge=16,
        le=4096,
        description=(
            "Ceiling on the reply length for one turn. On a phone call this is "
            "a latency control as much as a cost one -- without streaming the "
            "caller waits for the whole reply to generate. Too low truncates "
            "mid-sentence, which sounds like the agent hung up."
        ),
    )


@register_llm
class OpenAILLMService(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4.1",
        description="OpenAI chat model to use.",
        json_schema_extra={"examples": OPENAI_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local LLM, proxy).",
    )


@register_llm
class GoogleLLMService(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = GOOGLE_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model on Google AI Studio (not Vertex).",
        json_schema_extra={"examples": GOOGLE_MODELS, "allow_custom_input": True},
    )


@register_llm
class GoogleVertexLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = GOOGLE_VERTEX_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE_VERTEX] = ServiceProviders.GOOGLE_VERTEX
    model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model on Vertex AI.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_MODELS,
            "allow_custom_input": True,
        },
    )
    project_id: str = Field(description="Google Cloud project ID for Vertex AI.")
    location: str = Field(
        default="global",
        description="GCP region for the Vertex AI endpoint (e.g. 'global').",
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire service-account JSON file contents. If omitted, "
            "falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description=(
            "Not used for Vertex AI — authentication is via the service account "
            "in `credentials` (or ADC). Leave blank."
        ),
    )


@register_llm
class GroqLLMService(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = GROQ_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GROQ] = ServiceProviders.GROQ
    model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq-hosted model identifier.",
        json_schema_extra={"examples": GROQ_MODELS, "allow_custom_input": True},
    )


@register_llm
class OpenRouterLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = OPENROUTER_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENROUTER] = ServiceProviders.OPENROUTER
    model: str = Field(
        default="openai/gpt-4.1",
        description="OpenRouter model slug in 'vendor/model' form.",
        json_schema_extra={"examples": OPENROUTER_MODELS, "allow_custom_input": True},
    )

    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Override only if proxying OpenRouter through your own gateway.",
    )


@register_llm
class AzureLLMService(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = AZURE_OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="gpt-4.1-mini",
        description="Azure deployment name (not the upstream OpenAI model id).",
        json_schema_extra={"examples": AZURE_MODELS, "allow_custom_input": True},
    )

    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )


@register_llm
class DecibylLLMService(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = DECIBYL_PROVIDER_MODEL_CONFIG
    # Managed slots carry no key from the customer — that is the entire point
    # of choosing one. ``managed_resolution`` substitutes our platform key at
    # resolution time. Inherited as required from the base, which made a
    # managed slot unsaveable and is the same holdover already documented on
    # DecibylManagedAIModelConfiguration.
    api_key: str | list[str] = ""
    provider: Literal[ServiceProviders.DECIBYL] = ServiceProviders.DECIBYL
    model: str = Field(
        default="default",
        description="Decibyl-hosted model tier.",
        # No custom input: the dropdown already lists every tier
        # managed_tiers.resolve() understands. Anything else typed here
        # silently falls back to "default" with no error shown -- offering
        # the text box only invites that footgun.
        json_schema_extra={"examples": DECIBYL_LLM_MODELS},
    )


@register_llm
class AWSBedrockLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = AWS_BEDROCK_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AWS_BEDROCK] = ServiceProviders.AWS_BEDROCK
    model: str = Field(
        default="us.amazon.nova-pro-v1:0",
        description="Bedrock model ID — include the region inference-profile prefix (e.g. 'us.').",
        json_schema_extra={"examples": AWS_BEDROCK_MODELS, "allow_custom_input": True},
    )
    aws_access_key: str = Field(
        default="",
        description="AWS access key ID with bedrock:InvokeModel permission.",
    )
    aws_secret_key: str = Field(
        default="",
        description="AWS secret access key paired with the access key ID.",
    )
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region where the Bedrock model is available.",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Bedrock — authentication is via the AWS credentials above. Leave blank.",
    )


SPEACHES_LLM_MODELS = ["llama3", "mistral", "phi3", "qwen2", "gemma2", "deepseek-r1"]


@register_llm
class SpeachesLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = SPEACHES_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="llama3",
        description="Model name as exposed by your OpenAI-compatible server.",
        json_schema_extra={
            "examples": SPEACHES_LLM_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible endpoint (Ollama, vLLM, etc.).",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted endpoints. Leave blank unless your server enforces one.",
    )


@register_llm
class CustomLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    """Any OpenAI-compatible chat-completions endpoint.

    Distinct from Speaches, which is a specific self-hosted product with its
    own default endpoint and model list. This is the escape hatch: a gateway, a
    fine-tune, or a vendor we have not added yet. Both are built by the same
    OpenAI-shaped service class, so the only thing this really adds is a name
    that tells a customer the option exists -- which was the whole gap, since
    pointing `openai` at your own base_url already worked and nobody could tell.
    """

    model_config = CUSTOM_LLM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.CUSTOM_LLM] = ServiceProviders.CUSTOM_LLM
    model: str = Field(
        description=(
            "Model name exactly as your endpoint expects it in the request body."
        ),
        json_schema_extra={"allow_custom_input": True},
    )
    base_url: str = Field(
        description=(
            "Base URL of the OpenAI-compatible endpoint, including the version "
            "segment (e.g. https://your-gateway.example.com/v1)."
        ),
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description=(
            "Bearer token your endpoint expects. Leave blank if it needs none."
        ),
    )

    @model_validator(mode="after")
    def reject_platform_key(self):
        """There is no platform key for somebody else's endpoint.

        Left allowed this is a silent billing error rather than a failed call:
        the factory builds this with the OpenAI service class, so usage would
        be attributed to OpenAI and metered at OpenAI's rate for an endpoint we
        neither run nor pay for. Refusing it at save time is the difference
        between an error someone reads and a margin figure nobody checks.
        """
        if self.use_platform_key:
            raise ValueError(
                "A custom LLM endpoint runs on your own key. Leave the "
                "platform-key option off and supply the endpoint's own token."
            )
        return self


HUGGINGFACE_LLM_MODELS = [
    "openai/gpt-oss-120b:cerebras",
    "deepseek-ai/DeepSeek-R1:fastest",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct:fastest",
]


@register_llm
class HuggingFaceLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = HUGGINGFACE_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.HUGGINGFACE] = ServiceProviders.HUGGINGFACE
    model: str = Field(
        default="openai/gpt-oss-120b:cerebras",
        description="Hugging Face chat-completion model identifier, optionally with provider suffix.",
        json_schema_extra={
            "examples": HUGGINGFACE_LLM_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://router.huggingface.co/v1",
        description="Hugging Face OpenAI-compatible chat-completions router base URL.",
    )
    bill_to: str | None = Field(
        default=None,
        description="Optional Hugging Face organization or user to bill using X-HF-Bill-To.",
    )


MINIMAX_MODELS = [
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M3",
]


@register_llm
class MiniMaxLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    provider: Literal[ServiceProviders.MINIMAX] = ServiceProviders.MINIMAX
    model: str = Field(
        default="MiniMax-M2.7",
        description="MiniMax chat model.",
        json_schema_extra={"examples": MINIMAX_MODELS, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.minimax.io/v1",
        description="MiniMax OpenAI-compatible API endpoint.",
    )
    temperature: float = Field(
        default=1.0,
        gt=0.0,
        le=2.0,
        description="Sampling temperature. MiniMax requires > 0.",
    )


@register_llm
class SarvamLLMConfiguration(PipelineLLMTuning, BaseLLMConfiguration):
    model_config = SARVAM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="sarvam-105b",
        description=(
            "Sarvam chat model. sarvam-105b is the current generation; "
            "sarvam-30b was retired by Sarvam and now returns a 400."
        ),
        json_schema_extra={"examples": SARVAM_LLM_MODELS, "allow_custom_input": True},
    )
    temperature: float = Field(
        default=0.5,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature. Sarvam recommends 0.5 for balanced "
            "conversational responses."
        ),
    )


OPENAI_REALTIME_MODELS = ["gpt-realtime-2"]
OPENAI_REALTIME_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
]


@register_service(ServiceType.REALTIME)
class OpenAIRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = OPENAI_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENAI_REALTIME] = (
        ServiceProviders.OPENAI_REALTIME
    )
    model: str = Field(
        default="gpt-realtime-2",
        description="OpenAI realtime (speech-to-speech) model.",
        json_schema_extra={
            "examples": OPENAI_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="alloy",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": OPENAI_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )


DECIBYL_REALTIME_MODELS = ["default"]


@register_service(ServiceType.REALTIME)
class DecibylRealtimeConfiguration(BaseLLMConfiguration):
    """Speech-to-speech on our key, chosen as a tier rather than a vendor.

    The realtime counterpart of the managed STT/LLM/TTS classes, and it exists
    for the same reason: a customer picking "managed" is choosing not to hold a
    key, and without a Decibyl variant in the realtime union that choice was
    unrepresentable — speech-to-speech was BYOK-only, which is why the old UI
    listed it beside BYOK as though it were an alternative to it.

    ``voice`` is carried because a realtime model speaks directly; there is no
    separate TTS section to hold it.
    """

    model_config = DECIBYL_PROVIDER_MODEL_CONFIG
    # No key from the customer — managed_resolution substitutes ours.
    api_key: str | list[str] = ""
    provider: Literal[ServiceProviders.DECIBYL] = ServiceProviders.DECIBYL
    model: str = Field(
        default="default",
        description="Decibyl speech-to-speech tier.",
        json_schema_extra={"examples": DECIBYL_REALTIME_MODELS},
    )
    voice: str = Field(
        default="default",
        description="Voice the model speaks in.",
        json_schema_extra={"allow_custom_input": True},
    )


GROK_REALTIME_MODELS = ["grok-voice-think-fast-1.0"]
GROK_REALTIME_VOICES = ["ara", "rex", "sal", "eve", "leo"]
ULTRAVOX_REALTIME_MODELS = ["ultravox-v0.7", "fixie-ai/ultravox"]


@register_service(ServiceType.REALTIME)
class GrokRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = GROK_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GROK_REALTIME] = ServiceProviders.GROK_REALTIME
    model: str = Field(
        default="grok-voice-think-fast-1.0",
        description="Grok realtime voice-agent model.",
        json_schema_extra={
            "examples": GROK_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="ara",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GROK_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )


@register_service(ServiceType.REALTIME)
class UltravoxRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = ULTRAVOX_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.ULTRAVOX_REALTIME] = (
        ServiceProviders.ULTRAVOX_REALTIME
    )
    model: str = Field(
        default="ultravox-v0.7",
        description="Ultravox realtime voice-agent model.",
        json_schema_extra={
            "examples": ULTRAVOX_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Mark",
        description="Ultravox voice name or voice ID.",
    )


@register_service(ServiceType.REALTIME)
class GoogleRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = GOOGLE_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE_REALTIME] = (
        ServiceProviders.GOOGLE_REALTIME
    )
    model: str = Field(
        default="gemini-3.1-flash-live-preview",
        description="Gemini Live model on Google AI Studio (not Vertex).",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Puck",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="auto",
        description=(
            "ISO 639-1 language code, or 'auto' to let the model follow the "
            "caller. Native-audio Live models switch language mid-conversation "
            "on their own and ignore a pinned code; pinning one is only "
            "meaningful on a half-cascade model."
        ),
        json_schema_extra={
            "examples": GOOGLE_REALTIME_LANGUAGES,
            "allow_custom_input": True,
        },
    )


@register_service(ServiceType.REALTIME)
class GoogleVertexRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = GOOGLE_VERTEX_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE_VERTEX_REALTIME] = (
        ServiceProviders.GOOGLE_VERTEX_REALTIME
    )
    model: str = Field(
        default="google/gemini-live-2.5-flash-native-audio",
        description="Vertex AI publisher/model identifier.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Charon",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="auto",
        description=(
            "BCP-47 language code (e.g. 'en-US'), or 'auto' to let the model "
            "follow the caller. The default model here is a native-audio one, "
            "which switches language mid-conversation on its own and ignores a "
            "pinned code."
        ),
        json_schema_extra={
            "examples": GOOGLE_VERTEX_REALTIME_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    project_id: str = Field(description="Google Cloud project ID for Vertex AI.")
    location: str = Field(
        default="global",
        description="GCP region for the Vertex AI endpoint (e.g. 'global').",
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire service-account JSON file contents. If omitted, "
            "falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description=(
            "Not used for Vertex AI — authentication is via the service account "
            "in `credentials` (or ADC). Leave blank."
        ),
    )


@register_service(ServiceType.REALTIME)
class AzureRealtimeLLMConfiguration(BaseLLMConfiguration):
    model_config = AZURE_REALTIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AZURE_REALTIME] = ServiceProviders.AZURE_REALTIME
    model: str = Field(
        default="gpt-realtime",
        description="Azure OpenAI realtime deployment name.",
        json_schema_extra={
            "examples": AZURE_REALTIME_MODELS,
            "allow_custom_input": True,
        },
    )
    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )
    voice: str = Field(
        default="alloy",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": AZURE_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    api_version: str = Field(
        default="v1",
        description=(
            "Azure OpenAI Realtime protocol version. Use 'v1' for the GA API; "
            "date-based versions select the deprecated preview endpoint."
        ),
        json_schema_extra={
            "examples": AZURE_REALTIME_API_VERSIONS,
        },
    )


REALTIME_PROVIDERS = {
    ServiceProviders.OPENAI_REALTIME.value,
    ServiceProviders.GROK_REALTIME.value,
    ServiceProviders.ULTRAVOX_REALTIME.value,
    ServiceProviders.GOOGLE_REALTIME.value,
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
    ServiceProviders.AZURE_REALTIME.value,
}


LLMConfig = Annotated[
    Union[
        OpenAILLMService,
        GoogleVertexLLMConfiguration,
        GroqLLMService,
        OpenRouterLLMConfiguration,
        GoogleLLMService,
        AzureLLMService,
        DecibylLLMService,
        AWSBedrockLLMConfiguration,
        SpeachesLLMConfiguration,
        HuggingFaceLLMConfiguration,
        MiniMaxLLMConfiguration,
        SarvamLLMConfiguration,
        CustomLLMConfiguration,
    ],
    Field(discriminator="provider"),
]

RealtimeConfig = Annotated[
    Union[
        OpenAIRealtimeLLMConfiguration,
        GrokRealtimeLLMConfiguration,
        UltravoxRealtimeLLMConfiguration,
        GoogleRealtimeLLMConfiguration,
        GoogleVertexRealtimeLLMConfiguration,
        AzureRealtimeLLMConfiguration,
        DecibylRealtimeConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### TTS ########################################################################


@register_tts
class DeepgramTTSConfiguration(BaseServiceConfiguration):
    model_config = DEEPGRAM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    voice: str = Field(
        default="aura-2-helena-en",
        description="Deepgram voice ID (model is inferred from the 'aura-N' prefix).",
    )

    @computed_field
    @property
    def model(self) -> str:
        # Deepgram model's name is inferred using the voice name.
        # It can either contain aura-2 or aura-1
        if "aura-2" in self.voice:
            return "aura-2"
        elif "aura-1" in self.voice:
            return "aura-1"
        else:
            # Default fallback
            return "aura-2"


ELEVENLABS_TTS_MODELS = ["eleven_flash_v2_5"]


@register_tts
class ElevenlabsTTSConfiguration(BaseServiceConfiguration):
    model_config = ELEVENLABS_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.ELEVENLABS] = ServiceProviders.ELEVENLABS
    voice: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description="ElevenLabs voice ID from your Voice Library.",
    )
    speed: float = Field(default=1.0, ge=0.1, le=2.0, description="Speed of the voice.")
    # These three were literals inside the TTS factory branch, so the only way
    # to change how an ElevenLabs voice sounds was to edit and redeploy. The
    # defaults here are exactly what that branch passed, so declaring them
    # changes no call -- it only makes them reachable.
    #
    # ElevenLabs' own defaults are stability 0.5 and style 0.0; 0.8 is ours,
    # chosen for a phone line where a wandering delivery is worse than a flat
    # one. Somebody reading a script wants it higher, somebody doing warm
    # outbound wants it lower, and neither should need a deploy.
    stability: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "How steady the delivery is. Higher is more consistent and more "
            "monotone; lower is more expressive and more variable between runs."
        ),
    )
    similarity_boost: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description=(
            "How closely the output tracks the original voice. Very high values "
            "can reproduce artefacts present in the source recording."
        ),
    )
    style: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Style exaggeration. Above 0 it costs latency, so it stays off "
            "unless a voice actually needs it."
        ),
    )
    model: str = Field(
        default="eleven_flash_v2_5",
        description="ElevenLabs TTS model.",
        json_schema_extra={
            "examples": ELEVENLABS_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API base URL. Override to use a Data Residency endpoint "
            "(e.g. https://api.eu.residency.elevenlabs.io) for GDPR / HIPAA / "
            "regional compliance."
        ),
    )


@register_tts
class GoogleTTSConfiguration(BaseTTSConfiguration):
    model_config = GOOGLE_CLOUD_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="chirp_3_hd",
        description=(
            "Google Cloud low-latency TTS engine. Decibyl maps this to Pipecat's "
            "streaming Google TTS service for Chirp 3 HD and Journey voices."
        ),
        json_schema_extra={
            "examples": GOOGLE_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="en-US-Chirp3-HD-Charon",
        description="Google Cloud voice name. Use a Chirp 3 HD or Journey voice for streaming TTS.",
        json_schema_extra={
            "examples": GOOGLE_TTS_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": GOOGLE_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=2.0,
        description="Speech speed multiplier for Google streaming TTS.",
    )
    location: str | None = Field(
        default=None,
        description=(
            "Optional Google Cloud regional Text-to-Speech endpoint (for example "
            "'us-central1'). Leave blank to use the default endpoint."
        ),
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire Google Cloud service-account JSON. If omitted, "
            "the server falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Google Cloud TTS. Leave blank.",
    )


OPENAI_TTS_MODELS = ["gpt-4o-mini-tts"]


@register_tts
class OpenAITTSService(BaseTTSConfiguration):
    model_config = OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-mini-tts",
        description="OpenAI TTS model.",
        json_schema_extra={"examples": OPENAI_TTS_MODELS},
    )
    voice: str = Field(
        default="alloy",
        description="OpenAI TTS voice name.",
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local TTS, proxy).",
    )


DECIBYL_TTS_MODELS = ["default"]


@register_tts
class DecibylTTSService(BaseTTSConfiguration):
    model_config = DECIBYL_PROVIDER_MODEL_CONFIG
    # Managed slots carry no key from the customer — that is the entire point
    # of choosing one. ``managed_resolution`` substitutes our platform key at
    # resolution time. Inherited as required from the base, which made a
    # managed slot unsaveable and is the same holdover already documented on
    # DecibylManagedAIModelConfiguration.
    api_key: str | list[str] = ""
    provider: Literal[ServiceProviders.DECIBYL] = ServiceProviders.DECIBYL
    model: str = Field(
        default="default",
        description="Decibyl TTS tier.",
        json_schema_extra={"examples": DECIBYL_TTS_MODELS},
    )
    voice: str = Field(
        default="default",
        description="Voice preset.",
        json_schema_extra={"allow_custom_input": True},
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speed of the voice.")


CARTESIA_TTS_MODELS = ["sonic-3.5", "sonic-3"]
INWORLD_TTS_MODELS = ["inworld-tts-2"]
INWORLD_TTS_VOICES = ["Ashley"]
INWORLD_TTS_LANGUAGES = ["en-US"]


@register_tts
class CartesiaTTSConfiguration(BaseTTSConfiguration):
    model_config = CARTESIA_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="sonic-3.5",
        description="Cartesia TTS model.",
        json_schema_extra={"examples": CARTESIA_TTS_MODELS},
    )
    voice: str = Field(
        default="3faa81ae-d3d8-4ab1-9e44-e50e46d33c30",
        description="Cartesia voice UUID from your Cartesia dashboard.",
    )
    speed: float = Field(default=1.0, ge=0.6, le=1.5, description="Speed of the voice.")
    volume: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Volume multiplier for generated speech.",
    )
    language: str = Field(
        default="en",
        description="Cartesia language code for TTS synthesis (e.g. 'en', 'tr', 'fr', 'de').",
        json_schema_extra={"allow_custom_input": True},
    )


@register_tts
class InworldTTSConfiguration(BaseTTSConfiguration):
    model_config = INWORLD_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.INWORLD] = ServiceProviders.INWORLD
    model: str = Field(
        default="inworld-tts-2",
        description="Inworld TTS model.",
        json_schema_extra={"examples": INWORLD_TTS_MODELS, "allow_custom_input": True},
    )
    voice: str = Field(
        default="Ashley",
        description=(
            "Inworld voice ID. Use Ashley for the default warm English voice, "
            "or a workspace voice ID for a cloned/custom voice."
        ),
        json_schema_extra={"examples": INWORLD_TTS_VOICES, "allow_custom_input": True},
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": INWORLD_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Speech speed multiplier.",
    )
    delivery_mode: Literal["STABLE", "BALANCED", "CREATIVE"] = Field(
        default="BALANCED",
        description=(
            "Controls stability versus expressiveness for inworld-tts-2 "
            "(STABLE, BALANCED, or CREATIVE)."
        ),
    )


@register_tts
class RumikTTSConfiguration(BaseTTSConfiguration):
    """Rumik Silk — the cheapest synthesis on the rate card.

    Hindi and English only, including code-mixed. That is narrower than every
    other Indic option here, so the language field says so rather than offering
    a list the model will not honour.
    """

    model_config = RUMIK_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.RUMIK] = ServiceProviders.RUMIK
    model: str = Field(
        default="mulberry",
        description=(
            "Silk model. mulberry is faster and half the price — use it for "
            "calls. muga is more expressive and supports tone tags."
        ),
        json_schema_extra={"examples": RUMIK_TTS_MODELS},
    )
    voice: str = Field(
        default="ira",
        description="Preset studio voice.",
        json_schema_extra={"examples": RUMIK_VOICES},
    )
    description: str = Field(
        default=RUMIK_DEFAULT_DESCRIPTION,
        description=(
            "Plain-English description of how the voice should sound. Rumik "
            "shapes the preset voice with it, so leaving it blank makes every "
            "agent sound the same."
        ),
    )
    language: str = Field(
        default="hi-IN",
        description="Silk speaks Hindi and English only, including code-mixed.",
        json_schema_extra={"examples": RUMIK_LANGUAGES},
    )


@register_tts
class SarvamTTSConfiguration(BaseTTSConfiguration):
    model_config = SARVAM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="bulbul:v2",
        description="Sarvam TTS model (voice list depends on this).",
        json_schema_extra={"examples": SARVAM_TTS_MODELS},
    )
    voice: str = Field(
        default="anushka",
        description="Sarvam voice name or custom voice ID.",
        json_schema_extra={
            "examples": SARVAM_V2_VOICES,
            "allow_custom_input": True,
            "model_options": {
                "bulbul:v2": SARVAM_V2_VOICES,
                "bulbul:v3": SARVAM_V3_VOICES,
            },
        },
    )
    language: str = Field(
        default="hi-IN",
        description="BCP-47 Indian-language code (e.g. hi-IN, en-IN).",
        json_schema_extra={"examples": SARVAM_LANGUAGES},
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier.",
    )


CAMB_TTS_MODELS = ["mars-flash", "mars-pro", "mars-instruct"]


@register_tts
class CambTTSConfiguration(BaseTTSConfiguration):
    model_config = CAMB_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.CAMB] = ServiceProviders.CAMB
    model: str = Field(
        default="mars-flash",
        description="Camb.ai TTS model.",
        json_schema_extra={"examples": CAMB_TTS_MODELS},
    )
    voice: str = Field(default="147320", description="Camb.ai voice ID.")
    language: str = Field(default="en-us", description="BCP-47 language code.")


RIME_TTS_MODELS = ["arcana", "mistv3", "mistv2", "mist"]
RIME_TTS_LANGUAGES = ["en", "de", "fr", "es", "hi"]


@register_tts
class RimeTTSConfiguration(BaseTTSConfiguration):
    model_config = RIME_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.RIME] = ServiceProviders.RIME
    model: str = Field(
        default="arcana",
        description="Rime TTS model.",
        json_schema_extra={"examples": RIME_TTS_MODELS, "allow_custom_input": True},
    )
    voice: str = Field(
        default="celeste",
        description="Rime voice ID.",
    )
    speed: float = Field(
        default=1.0, ge=0.5, le=2.0, description="Speech speed multiplier."
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": RIME_TTS_LANGUAGES, "allow_custom_input": True},
    )


SPEACHES_TTS_MODELS = ["hexgrad/Kokoro-82M"]


@register_tts
class SpeachesTTSConfiguration(BaseTTSConfiguration):
    model_config = SPEACHES_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="kokoro",
        description="Model name as served by your TTS endpoint (e.g. Kokoro-FastAPI).",
        json_schema_extra={
            "examples": SPEACHES_TTS_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="af_heart",
        json_schema_extra={"allow_custom_input": True},
        description="Voice ID for the TTS engine.",
    )
    base_url: str = Field(
        default="http://localhost:8000/v1",
        description="OpenAI-compatible TTS endpoint (Kokoro-FastAPI, etc.).",
    )
    speed: float = Field(
        default=1.0, ge=0.25, le=4.0, description="Speech speed (0.25 to 4.0)."
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted TTS. Leave blank unless enforced.",
    )


MINIMAX_TTS_MODELS = ["speech-2.8-hd", "speech-2.8-turbo"]
MINIMAX_TTS_VOICES = [
    "English_Graceful_Lady",
    "English_Insightful_Speaker",
    "English_radiant_girl",
    "English_Persuasive_Man",
    "English_Lucky_Robot",
    "English_expressive_narrator",
]


@register_tts
class MiniMaxTTSConfiguration(BaseTTSConfiguration):
    provider: Literal[ServiceProviders.MINIMAX] = ServiceProviders.MINIMAX
    model: str = Field(
        default="speech-2.8-hd",
        description="MiniMax TTS model.",
        json_schema_extra={"examples": MINIMAX_TTS_MODELS},
    )
    voice: str = Field(
        default="English_Graceful_Lady",
        description="MiniMax voice ID.",
        json_schema_extra={"examples": MINIMAX_TTS_VOICES, "allow_custom_input": True},
    )
    base_url: str = Field(
        default="https://api.minimax.io/v1/t2a_v2",
        description=(
            "MiniMax TTS API endpoint (must include the /v1/t2a_v2 path). "
            "Defaults to the global endpoint; override with "
            "https://api.minimaxi.chat/v1/t2a_v2 (mainland China) or "
            "https://api-uw.minimax.io/v1/t2a_v2 (US-West)."
        ),
    )
    speed: float = Field(
        default=1.0, ge=0.5, le=2.0, description="Speech speed (0.5 to 2.0)."
    )
    group_id: str = Field(
        description="MiniMax Group ID (found in your MiniMax dashboard under Account → Group).",
    )


@register_tts
class AzureSpeechTTSConfiguration(BaseTTSConfiguration):
    model_config = AZURE_SPEECH_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AZURE_SPEECH] = ServiceProviders.AZURE_SPEECH
    model: str = Field(
        default="neural",
        description="Azure Speech synthesis engine (neural voices only).",
        json_schema_extra={"examples": ["neural"]},
    )
    region: str = Field(
        default="eastus",
        description="Azure region for Speech Services (e.g. 'eastus', 'westeurope').",
        json_schema_extra={
            "examples": AZURE_SPEECH_REGIONS,
        },
    )
    voice: str = Field(
        default="en-US-AriaNeural",
        description="Azure Neural voice name (e.g. 'en-US-AriaNeural').",
        json_schema_extra={
            "examples": AZURE_SPEECH_TTS_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for synthesis.",
        json_schema_extra={
            "examples": AZURE_SPEECH_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier (0.5 to 2.0).",
    )


SMALLEST_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Smallest AI",
    description="Smallest AI ultralow-latency TTS (Waves) and STT (Pulse) APIs.",
    provider_docs_url="https://smallest.ai/docs",
)


@register_tts
class SmallestAITTSConfiguration(BaseTTSConfiguration):
    model_config = SMALLEST_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SMALLEST] = ServiceProviders.SMALLEST
    model: str = Field(
        default="lightning_v3.1",
        description="Smallest AI TTS model. lightning_v3.1_pro is the premium pool (American, British, Indian accents); lightning_v3.1 is the standard pool with 217 voices across 12 languages.",
        json_schema_extra={"examples": SMALLEST_TTS_MODELS},
    )
    voice: str = Field(
        default="sophia",
        description="Smallest AI voice ID. Available voices differ by model: lightning_v3.1 has a broad multilingual pool; lightning_v3.1_pro has premium American, British, and Indian accent voices (English + Hindi only).",
        json_schema_extra={
            "examples": list(SMALLEST_TTS_VOICES),
            "allow_custom_input": True,
            "model_options": {
                "lightning_v3.1": list(SMALLEST_TTS_VOICES),
                "lightning_v3.1_pro": list(SMALLEST_TTS_PRO_VOICES),
            },
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code for synthesis.",
        json_schema_extra={
            "examples": SMALLEST_TTS_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed multiplier (0.5 to 2.0).",
    )


XAI_TTS_VOICES = ["eve", "ara", "leo", "rex", "sal"]


@register_tts
class XAITTSConfiguration(BaseServiceConfiguration):
    model_config = XAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.XAI] = ServiceProviders.XAI
    voice: str = Field(
        default="eve",
        description="xAI voice persona.",
        json_schema_extra={"examples": XAI_TTS_VOICES, "allow_custom_input": True},
    )
    language: str = Field(
        default="en",
        description="BCP-47 language code for synthesis (e.g. 'en', 'fr', 'de'), or 'auto' for automatic language detection.",
        json_schema_extra={"allow_custom_input": True},
    )

    @computed_field
    @property
    def model(self) -> str:
        # xAI TTS has no separate model selector; the voice fully specifies the
        # output. A constant keeps the shared `.model` contract satisfied.
        return "xai-tts"


TTSConfig = Annotated[
    Union[
        DeepgramTTSConfiguration,
        GoogleTTSConfiguration,
        OpenAITTSService,
        ElevenlabsTTSConfiguration,
        CartesiaTTSConfiguration,
        InworldTTSConfiguration,
        DecibylTTSService,
        SarvamTTSConfiguration,
        RumikTTSConfiguration,
        CambTTSConfiguration,
        RimeTTSConfiguration,
        SpeachesTTSConfiguration,
        MiniMaxTTSConfiguration,
        AzureSpeechTTSConfiguration,
        SmallestAITTSConfiguration,
        XAITTSConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### STT ########################################################################


@register_stt
class DeepgramSTTConfiguration(BaseSTTConfiguration):
    model_config = DEEPGRAM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.DEEPGRAM] = ServiceProviders.DEEPGRAM
    # Flux, not Nova-3. Flux reports end of turn itself, from acoustic and
    # semantic context, so the pipeline takes the external-turn path and skips
    # the VAD silence wait plus the speech timeout that follows it — roughly
    # half a second of dead air on every turn, before anything downstream has
    # started. Nothing further down the pipeline can win that back.
    #
    # Multilingual rather than -en: it auto-detects across ten languages
    # including Hindi, and an account that needs one of them should not have to
    # know to change the model. English-only workflows can still pick
    # flux-general-en, which is both faster to route and cheaper.
    #
    # Languages outside that set — Tamil, Telugu, Kannada, Marathi, Bengali —
    # are not offered on Flux by `model_options` below, and those workflows fall
    # back to Nova-3 and its silence wait. That gap is Deepgram's, not ours.
    model: str = Field(
        default="flux-general-multi",
        description="Deepgram STT model.",
        json_schema_extra={"examples": DEEPGRAM_STT_MODELS},
    )
    language: str = Field(
        default="multi",
        description=(
            "Language code. 'multi' enables Nova-3 auto-detect and omits "
            "language hints for Flux multilingual auto-detect."
        ),
        json_schema_extra={
            "examples": DEEPGRAM_LANGUAGES,
            "model_options": {
                "nova-3-general": DEEPGRAM_LANGUAGES,
                "flux-general-en": ("en",),
                "flux-general-multi": DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGE_OPTIONS,
            },
        },
    )


@register_stt
class CartesiaSTTConfiguration(BaseSTTConfiguration):
    model_config = CARTESIA_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.CARTESIA] = ServiceProviders.CARTESIA
    model: str = Field(
        default="ink-whisper",
        description="Cartesia STT model.",
        json_schema_extra={"examples": CARTESIA_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code. ink-2 currently supports English only.",
        json_schema_extra={
            "examples": CARTESIA_STT_LANGUAGES,
            "model_options": {
                "ink-2": CARTESIA_INK_2_STT_LANGUAGES,
                "ink-whisper": CARTESIA_INK_WHISPER_STT_LANGUAGES,
            },
        },
    )


OPENAI_STT_MODELS = ["gpt-4o-transcribe"]


@register_stt
class OpenAISTTConfiguration(BaseSTTConfiguration):
    model_config = OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="gpt-4o-transcribe",
        description="OpenAI transcription model.",
        json_schema_extra={"examples": OPENAI_STT_MODELS},
    )
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local STT, proxy).",
    )


@register_stt
class GoogleSTTConfiguration(BaseSTTConfiguration):
    model_config = GOOGLE_CLOUD_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GOOGLE] = ServiceProviders.GOOGLE
    model: str = Field(
        default="latest_long",
        description="Google Cloud Speech-to-Text V2 recognition model.",
        json_schema_extra={
            "examples": GOOGLE_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en-US",
        description="Primary BCP-47 language code for recognition.",
        json_schema_extra={
            "examples": GOOGLE_STT_LANGUAGES,
            "allow_custom_input": True,
            "docs_url": "https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages",
        },
    )
    location: str = Field(
        default="global",
        description="Google Cloud Speech-to-Text region (for example 'global' or 'us-central1').",
    )
    credentials: str | None = Field(
        default=None,
        description=(
            "Paste the entire Google Cloud service-account JSON. If omitted, "
            "the server falls back to Application Default Credentials (ADC)."
        ),
        json_schema_extra={"multiline": True},
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Not used for Google Cloud STT. Leave blank.",
    )


# Decibyl STT Service
DECIBYL_STT_MODELS = ["default"]
DECIBYL_STT_LANGUAGES = DEEPGRAM_LANGUAGES
# Languages auto-detected when the Decibyl STT language is "multi". Decibyl STT runs
# Deepgram Flux multilingual under the hood, which only auto-detects this subset —
# not the full DECIBYL_STT_LANGUAGES list offered for explicit single-language selection.
DECIBYL_MULTILINGUAL_AUTODETECT_LANGUAGES = DEEPGRAM_FLUX_MULTILINGUAL_LANGUAGES


@register_stt
class DecibylSTTService(BaseSTTConfiguration):
    model_config = DECIBYL_PROVIDER_MODEL_CONFIG
    # Managed slots carry no key from the customer — that is the entire point
    # of choosing one. ``managed_resolution`` substitutes our platform key at
    # resolution time. Inherited as required from the base, which made a
    # managed slot unsaveable and is the same holdover already documented on
    # DecibylManagedAIModelConfiguration.
    api_key: str | list[str] = ""
    provider: Literal[ServiceProviders.DECIBYL] = ServiceProviders.DECIBYL
    model: str = Field(
        default="default",
        description="Decibyl STT tier.",
        json_schema_extra={"examples": DECIBYL_STT_MODELS},
    )
    language: str = Field(
        default="multi",
        description="Language code; use 'multi' for auto-detect.",
        json_schema_extra={"examples": DECIBYL_STT_LANGUAGES},
    )


@register_stt
class SarvamSTTConfiguration(BaseSTTConfiguration):
    model_config = SARVAM_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SARVAM] = ServiceProviders.SARVAM
    model: str = Field(
        default="saaras:v3",
        description=(
            "Sarvam STT model. saaras:v3 covers 22 Indian languages plus "
            "English in one model with automatic detection, and is trained on "
            "code-mixed speech — pick it unless every caller stays in one "
            "language. saarika:v2.5 transcribes a single spoken language."
        ),
        json_schema_extra={"examples": SARVAM_STT_MODELS},
    )
    language: str = Field(
        default="unknown",
        description=(
            "BCP-47 language code. Use unknown for automatic language detection."
        ),
        json_schema_extra={
            "examples": SARVAM_STT_LANGUAGES_V25,
            "model_options": {
                "saarika:v2.5": SARVAM_STT_LANGUAGES_V25,
                "saaras:v3": SARVAM_STT_LANGUAGES_V3,
            },
        },
    )


@register_stt
class SpeechmaticsSTTConfiguration(BaseSTTConfiguration):
    model_config = SPEECHMATICS_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SPEECHMATICS] = ServiceProviders.SPEECHMATICS
    model: str = Field(
        default="enhanced",
        description="Speechmatics operating point: 'standard' or 'enhanced'.",
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": SPEECHMATICS_STT_LANGUAGES},
    )


SPEACHES_STT_MODELS = [
    "Systran/faster-distil-whisper-small.en",
    "Systran/faster-whisper-large-v3",
]
SPEACHES_STT_LANGUAGES = ["en", "ar", "nl", "fr", "de", "hi", "it", "pt", "es"]


@register_stt
class SpeachesSTTConfiguration(BaseSTTConfiguration):
    model_config = SPEACHES_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SPEACHES] = ServiceProviders.SPEACHES
    model: str = Field(
        default="Systran/faster-distil-whisper-small.en",
        description="Whisper model identifier as served by your STT endpoint.",
        json_schema_extra={
            "examples": SPEACHES_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={
            "examples": SPEACHES_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="http://localhost:8000/v1",
        description="OpenAI-compatible STT endpoint (Speaches, etc.).",
    )
    api_key: str | list[str] | None = Field(
        default=None,
        description="Usually not required for self-hosted STT. Leave blank unless enforced.",
    )


HUGGINGFACE_STT_MODELS = [
    "openai/whisper-large-v3-turbo",
    "openai/whisper-large-v3",
]


@register_stt
class HuggingFaceSTTConfiguration(BaseSTTConfiguration):
    model_config = HUGGINGFACE_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.HUGGINGFACE] = ServiceProviders.HUGGINGFACE
    model: str = Field(
        default="openai/whisper-large-v3-turbo",
        description="Hugging Face ASR model identifier served through Inference Providers.",
        json_schema_extra={
            "examples": HUGGINGFACE_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://router.huggingface.co/hf-inference",
        description="Hugging Face Inference Providers router base URL.",
    )
    bill_to: str | None = Field(
        default=None,
        description="Optional Hugging Face organization or user to bill using X-HF-Bill-To.",
    )
    return_timestamps: bool = Field(
        default=False,
        description="Request timestamp chunks when supported by the selected provider/model.",
    )


ASSEMBLYAI_STT_MODELS = ["u3-rt-pro"]
ASSEMBLYAI_STT_LANGUAGES = ["en", "es", "de", "fr", "pt", "it"]


@register_stt
class AssemblyAISTTConfiguration(BaseSTTConfiguration):
    model_config = ASSEMBLYAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.ASSEMBLYAI] = ServiceProviders.ASSEMBLYAI
    model: str = Field(
        default="u3-rt-pro",
        description="AssemblyAI realtime STT model.",
        json_schema_extra={"examples": ASSEMBLYAI_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": ASSEMBLYAI_STT_LANGUAGES},
    )


@register_stt
class GladiaSTTConfiguration(BaseSTTConfiguration):
    model_config = GLADIA_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GLADIA] = ServiceProviders.GLADIA
    model: str = Field(
        default="solaria-1",
        description="Gladia STT model.",
        json_schema_extra={"examples": GLADIA_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={"examples": GLADIA_STT_LANGUAGES},
    )


@register_stt
class AzureSpeechSTTConfiguration(BaseSTTConfiguration):
    model_config = AZURE_SPEECH_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AZURE_SPEECH] = ServiceProviders.AZURE_SPEECH
    model: str = Field(
        default="latest_long",
        description="Azure Speech recognition model (use 'latest_long' for continuous recognition).",
        json_schema_extra={"examples": ["latest_long", "latest_short"]},
    )
    region: str = Field(
        default="eastus",
        description="Azure region for Speech Services (e.g. 'eastus', 'westeurope').",
        json_schema_extra={
            "examples": AZURE_SPEECH_REGIONS,
        },
    )
    language: str = Field(
        default="en-US",
        description="BCP-47 language code for recognition.",
        json_schema_extra={
            "examples": AZURE_SPEECH_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )


SMALLEST_STT_MODELS = ["pulse"]
SMALLEST_STT_LANGUAGES = [
    "en",
    "hi",
    "fr",
    "de",
    "es",
    "it",
    "nl",
    "pl",
    "ru",
    "pt",
    "bn",
    "gu",
    "kn",
    "ml",
    "mr",
    "ta",
    "te",
    "pa",
    "or",
    "bg",
    "cs",
    "da",
    "et",
    "fi",
    "hu",
    "lt",
    "lv",
    "mt",
    "ro",
    "sk",
    "sv",
    "uk",
]


@register_stt
class ElevenlabsSTTConfiguration(BaseSTTConfiguration):
    model_config = ELEVENLABS_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.ELEVENLABS] = ServiceProviders.ELEVENLABS
    model: str = Field(
        default="scribe_v2_realtime",
        description="ElevenLabs realtime STT model.",
        json_schema_extra={
            "examples": ELEVENLABS_STT_MODELS,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description=(
            "ISO 639-1 language code for transcription. "
            "Use 'auto' to let ElevenLabs detect the language."
        ),
        json_schema_extra={
            "examples": ELEVENLABS_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )
    base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API base URL. Override to use a Data Residency endpoint "
            "(e.g. https://api.eu.residency.elevenlabs.io) for GDPR / HIPAA / "
            "regional compliance."
        ),
    )


@register_stt
class SmallestAISTTConfiguration(BaseSTTConfiguration):
    model_config = SMALLEST_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.SMALLEST] = ServiceProviders.SMALLEST
    model: str = Field(
        default="pulse",
        description="Smallest AI STT model. Supports 38 languages with real-time streaming.",
        json_schema_extra={"examples": SMALLEST_STT_MODELS},
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code for transcription.",
        json_schema_extra={
            "examples": SMALLEST_STT_LANGUAGES,
            "allow_custom_input": True,
        },
    )


STTConfig = Annotated[
    Union[
        DeepgramSTTConfiguration,
        CartesiaSTTConfiguration,
        OpenAISTTConfiguration,
        GoogleSTTConfiguration,
        DecibylSTTService,
        SpeechmaticsSTTConfiguration,
        SarvamSTTConfiguration,
        SpeachesSTTConfiguration,
        HuggingFaceSTTConfiguration,
        AssemblyAISTTConfiguration,
        GladiaSTTConfiguration,
        AzureSpeechSTTConfiguration,
        SmallestAISTTConfiguration,
        ElevenlabsSTTConfiguration,
    ],
    Field(discriminator="provider"),
]

###################################################### EMBEDDINGS ########################################################################

OPENAI_EMBEDDING_MODELS = ["text-embedding-3-small"]


@register_embeddings
class OpenAIEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    model_config = OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model.",
        json_schema_extra={"examples": OPENAI_EMBEDDING_MODELS},
    )

    # The field OpenRouter's embeddings class already had and this one did
    # not, so the factory's base_url argument could never be filled for the
    # provider most deployments use. "OpenAI" here names the wire protocol,
    # not the destination: an OpenAI-compatible server — Text Embeddings
    # Inference in front of a local model — answers the same requests, and
    # pointing this at one keeps the document text on hardware you control.
    #
    # Blank means api.openai.com, which is what every existing configuration
    # already resolves to.
    base_url: str = Field(
        default="",
        description=(
            "OpenAI-compatible endpoint. Leave blank for OpenAI itself; set "
            "it to your own server (e.g. a Text Embeddings Inference "
            "container) to keep document text on your infrastructure."
        ),
    )


OPENROUTER_EMBEDDING_MODELS = ["openai/text-embedding-3-small"]


@register_embeddings
class OpenRouterEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    model_config = OPENROUTER_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.OPENROUTER] = ServiceProviders.OPENROUTER
    model: str = Field(
        default="openai/text-embedding-3-small",
        description="OpenRouter-hosted embedding model slug.",
        json_schema_extra={"examples": OPENROUTER_EMBEDDING_MODELS},
    )

    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Override only if proxying OpenRouter through your own gateway.",
    )


@register_embeddings
class AzureOpenAIEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    model_config = AZURE_OPENAI_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.AZURE] = ServiceProviders.AZURE
    model: str = Field(
        default="text-embedding-3-small",
        description=(
            "Azure OpenAI embedding deployment name. The deployment must return "
            "1536-dimensional embeddings."
        ),
        json_schema_extra={
            "examples": AZURE_EMBEDDING_MODELS,
            "allow_custom_input": True,
        },
    )
    endpoint: str = Field(
        description="Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).",
    )
    api_version: str = Field(
        default="2024-02-15-preview",
        description="Azure OpenAI API version for embeddings.",
    )


DECIBYL_EMBEDDING_MODELS = ["decibyl_embedding_v1"]


@register_embeddings
class DecibylEmbeddingsConfiguration(BaseEmbeddingsConfiguration):
    model_config = DECIBYL_PROVIDER_MODEL_CONFIG
    # Managed slots carry no key from the customer — that is the entire point
    # of choosing one. ``managed_resolution`` substitutes our platform key at
    # resolution time. Inherited as required from the base, which made a
    # managed slot unsaveable and is the same holdover already documented on
    # DecibylManagedAIModelConfiguration.
    api_key: str | list[str] = ""
    provider: Literal[ServiceProviders.DECIBYL] = ServiceProviders.DECIBYL
    model: str = Field(
        default="decibyl_embedding_v1",
        description="Decibyl-managed embedding model.",
        json_schema_extra={"examples": DECIBYL_EMBEDDING_MODELS},
    )


EmbeddingsConfig = Annotated[
    Union[
        OpenAIEmbeddingsConfiguration,
        OpenRouterEmbeddingsConfiguration,
        AzureOpenAIEmbeddingsConfiguration,
        DecibylEmbeddingsConfiguration,
    ],
    Field(discriminator="provider"),
]

ServiceConfig = Annotated[
    Union[LLMConfig, RealtimeConfig, TTSConfig, STTConfig, EmbeddingsConfig],
    Field(discriminator="provider"),
]

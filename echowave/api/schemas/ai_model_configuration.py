from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from api.services.configuration.registry import (
    DecibylEmbeddingsConfiguration,
    DecibylLLMService,
    DecibylRealtimeConfiguration,
    DecibylSTTService,
    DecibylTTSService,
    EmbeddingsConfig,
    LLMConfig,
    RealtimeConfig,
    ServiceProviders,
    STTConfig,
    TTSConfig,
)

DECIBYL_SPEED_MIN = 0.5
DECIBYL_SPEED_MAX = 2.0
DECIBYL_SPEED_STEP = 0.1
DECIBYL_SPEED_OPTIONS: tuple[float, ...] = (0.8, 1.0, 1.2)
DECIBYL_DEFAULT_VOICE = "default"
DECIBYL_DEFAULT_LANGUAGE = "multi"


class EffectiveAIModelConfiguration(BaseModel):
    llm: LLMConfig | None = None
    stt: STTConfig | None = None
    tts: TTSConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    realtime: RealtimeConfig | None = None
    is_realtime: bool = False
    managed_service_version: int | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    last_validated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def strip_incomplete_realtime_when_disabled(cls, data):
        """Skip realtime validation when is_realtime is False and api_key is missing."""
        if isinstance(data, dict) and not data.get("is_realtime", False):
            realtime = data.get("realtime")
            if isinstance(realtime, dict) and not realtime.get("api_key"):
                data.pop("realtime", None)
        return data


class DecibylManagedAIModelConfiguration(BaseModel):
    """Managed mode: we hold the vendor keys, the customer holds none.

    ``api_key`` is deliberately optional and empty by default. This field is a
    holdover from when "Decibyl" meant a hosted service the customer received a
    service key for; it now means *we* pay for the inference on our platform
    keys, which ``services/configuration/managed_resolution`` substitutes at
    runtime. Requiring a key here made managed mode impossible to save — the
    entire point of choosing it is not having one.

    It is kept rather than deleted so a stored configuration written by the old
    UI still loads. Nothing reads it.
    """

    api_key: str = ""
    voice: str = DECIBYL_DEFAULT_VOICE
    speed: float = Field(default=1.0, ge=DECIBYL_SPEED_MIN, le=DECIBYL_SPEED_MAX)
    language: str = DECIBYL_DEFAULT_LANGUAGE
    #: Which language-model tier this agent talks on — ``lite``, ``default`` or
    #: ``accurate``, shown to the customer as Lite / Normal / Smart. Managed
    #: mode had no field for it and always ran the default, so the choice
    #: existed in the tier map and nowhere a customer could reach it.
    #:
    #: Not validated against the offered list here: a retired tier name in a
    #: stored configuration still resolves (see ``managed_tiers``), and
    #: rejecting it at load would break an agent that has been dialling
    #: happily for months.
    llm_tier: str = "default"

    #: Which bundle the Simple picker was on when this was saved. Stored so the
    #: picker can show what is currently in force, which it previously could
    #: not: the tiers below describe the stack but two bundles can resolve to
    #: the same pair, so reopening the screen guessed rather than knew.
    #:
    #: A label for the screen, never an input to resolution. A bundle renamed
    #: or withdrawn leaves the tiers below untouched and the agent dialling.
    bundle: str = ""

    #: The speech tiers this account runs on. Defaulted to ``"default"``
    #: because that is the literal these two slots were hardcoded to before the
    #: fields existed, so every configuration stored by the old UI compiles to
    #: exactly what it compiled to then.
    stt_tier: str = "default"
    tts_tier: str = "default"

    #: Non-empty when the account is on a speech-to-speech bundle. It replaces
    #: the transcriber and the voice rather than joining them, so it is checked
    #: first and the pipeline slots are not emitted at all — the same shape
    #: ``agent_options.managed_stack_override`` writes at the agent level, which
    #: managed mode had no way to express at the account level until now.
    realtime_tier: str = ""


# The two classes below no longer reject ``decibyl`` in a slot.
#
# They used to, and that single rule is what made the product's model story
# confusing: it forced a whole account to be either managed or BYOK, so "use
# your Indic STT with my OpenAI key" was unrepresentable and the UI had to
# present the choice as exclusive tabs. Nothing downstream ever required the
# restriction — the config unions already admit the Decibyl variants and
# ``managed_resolution`` already rewrites each section independently — so
# removing it is what makes a mixed stack work, not new machinery.
#
# Kept as the v2 wire shape for stored configurations. New writes use v3, which
# drops the mode wrapper entirely; see the bottom of this module.


class BYOKPipelineAIModelConfiguration(BaseModel):
    llm: LLMConfig
    tts: TTSConfig
    stt: STTConfig
    embeddings: EmbeddingsConfig | None = None


class BYOKRealtimeAIModelConfiguration(BaseModel):
    realtime: RealtimeConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig | None = None


class BYOKAIModelConfiguration(BaseModel):
    mode: Literal["pipeline", "realtime"]
    pipeline: BYOKPipelineAIModelConfiguration | None = None
    realtime: BYOKRealtimeAIModelConfiguration | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "pipeline" and self.pipeline is None:
            raise ValueError("byok.pipeline is required when byok.mode is pipeline")
        if self.mode == "realtime" and self.realtime is None:
            raise ValueError("byok.realtime is required when byok.mode is realtime")
        return self


class OrganizationAIModelConfigurationV2(BaseModel):
    version: Literal[2] = 2
    mode: Literal["decibyl", "byok"]
    decibyl: DecibylManagedAIModelConfiguration | None = None
    byok: BYOKAIModelConfiguration | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "decibyl" and self.decibyl is None:
            raise ValueError("decibyl configuration is required when mode is decibyl")
        if self.mode == "byok" and self.byok is None:
            raise ValueError("byok configuration is required when mode is byok")
        return self


class OrganizationAIModelConfigurationResponse(BaseModel):
    configuration: dict | None
    effective_configuration: dict
    source: Literal["organization_v2", "legacy_user_v1", "empty"]


def compile_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2,
) -> EffectiveAIModelConfiguration:
    if configuration.mode == "decibyl":
        if configuration.decibyl is None:
            raise ValueError("decibyl configuration is required")
        return _compile_decibyl_configuration(configuration.decibyl)

    if configuration.byok is None:
        raise ValueError("byok configuration is required")
    if configuration.byok.mode == "pipeline":
        if configuration.byok.pipeline is None:
            raise ValueError("byok.pipeline is required")
        pipeline = configuration.byok.pipeline
        return EffectiveAIModelConfiguration(
            llm=pipeline.llm,
            tts=pipeline.tts,
            stt=pipeline.stt,
            embeddings=pipeline.embeddings,
            is_realtime=False,
        )

    if configuration.byok.realtime is None:
        raise ValueError("byok.realtime is required")
    realtime = configuration.byok.realtime
    return EffectiveAIModelConfiguration(
        llm=realtime.llm,
        realtime=realtime.realtime,
        embeddings=realtime.embeddings,
        is_realtime=True,
    )


def _compile_decibyl_configuration(
    configuration: DecibylManagedAIModelConfiguration,
) -> EffectiveAIModelConfiguration:
    embeddings = DecibylEmbeddingsConfiguration(
        provider=ServiceProviders.DECIBYL,
        api_key=configuration.api_key,
        model="decibyl_embedding_v1",
    )

    realtime_tier = (configuration.realtime_tier or "").strip()
    if realtime_tier:
        # No stt or tts slot at all. A realtime section that also named a
        # transcriber would be two answers to one question, and the compiler
        # would have to pick one. The llm slot still names the same tier
        # because a realtime model *is* the language model.
        return EffectiveAIModelConfiguration(
            llm=DecibylLLMService(
                provider=ServiceProviders.DECIBYL,
                api_key=configuration.api_key,
                model=realtime_tier,
            ),
            realtime=DecibylRealtimeConfiguration(
                provider=ServiceProviders.DECIBYL,
                api_key=configuration.api_key,
                model=realtime_tier,
                voice=configuration.voice,
            ),
            embeddings=embeddings,
            is_realtime=True,
            managed_service_version=2,
        )

    return EffectiveAIModelConfiguration(
        llm=DecibylLLMService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            # The tier, not a vendor model name — managed_resolution reads this
            # field as the tier to resolve.
            model=configuration.llm_tier,
        ),
        tts=DecibylTTSService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model=configuration.tts_tier,
            voice=configuration.voice,
            speed=configuration.speed,
        ),
        stt=DecibylSTTService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model=configuration.stt_tier,
            language=configuration.language,
        ),
        embeddings=embeddings,
        is_realtime=False,
        managed_service_version=2,
    )


# ---------------------------------------------------------------------------
# v3 — one stack, each slot independently managed or BYOK
# ---------------------------------------------------------------------------
#
# v2 made "managed" and "BYOK" mutually exclusive for the whole account, and
# then folded a third, unrelated choice into the same switch: whether the
# pipeline is a cascade (STT -> LLM -> TTS) or a single speech-to-speech model.
# The UI rendered that as three tabs — "Speech to Speech", "Decibyl", "BYOK" —
# which read as three alternatives when they are not even the same kind of
# thing. Two are key-ownership; one is an architecture. A customer could not
# express "your Indic STT, my OpenAI key" at all, and could not have managed
# speech-to-speech under any combination.
#
# v3 separates the two axes that were tangled:
#
#   architecture  — pipeline | realtime. How the conversation is produced.
#   each slot     — provider "decibyl" means managed (we hold the key and bill
#                   it through); any other provider means the account's own key,
#                   resolved from its vault.
#
# There is deliberately no ``mode`` field. Mode was the thing that forced the
# all-or-nothing choice, and reintroducing it as a summary would let it drift
# out of agreement with the slots it claims to summarise. Whether a stack is
# "managed" is derived, and it is a per-slot property that happens to be
# uniform sometimes.
#
# Nothing downstream changed to make this work. ``LLMConfig`` and friends were
# already discriminated unions including the Decibyl variants, and
# ``managed_resolution`` already rewrote *each section* independently. Mixed
# stacks were representable and resolvable the whole time; a validator in the
# v2 wrapper was the only thing rejecting them.


class AIModelStackV3(BaseModel):
    """The models one agent (or one account, as its default) runs on.

    ``llm`` is required under both architectures. Under ``realtime`` the
    speech-to-speech model carries the conversation, but an LLM is still needed
    for variable extraction and QA — the realtime model is not asked to do
    structured extraction mid-call.
    """

    architecture: Literal["pipeline", "realtime"] = "pipeline"

    llm: LLMConfig
    stt: STTConfig | None = None
    tts: TTSConfig | None = None
    realtime: RealtimeConfig | None = None
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def validate_architecture(self):
        if self.architecture == "pipeline":
            missing = [
                name
                for name, value in (("stt", self.stt), ("tts", self.tts))
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"A pipeline stack needs {' and '.join(missing)}. Choose a "
                    "provider for each, or switch to speech-to-speech."
                )
        elif self.realtime is None:
            raise ValueError(
                "A speech-to-speech stack needs a realtime model. Choose one, "
                "or switch to a pipeline."
            )
        return self

    def managed_slots(self) -> list[str]:
        """Which slots run on our keys. Derived, never stored."""
        return [
            name
            for name in ("stt", "llm", "tts", "realtime", "embeddings")
            if _is_managed(getattr(self, name, None))
        ]

    def byok_slots(self) -> list[str]:
        """Which slots run on the account's own keys. Derived, never stored."""
        return [
            name
            for name in ("stt", "llm", "tts", "realtime", "embeddings")
            if getattr(self, name, None) is not None
            and not _is_managed(getattr(self, name))
        ]


class OrganizationAIModelConfigurationV3(BaseModel):
    version: Literal[3] = 3
    stack: AIModelStackV3


def _is_managed(service) -> bool:
    return service is not None and getattr(service, "provider", None) in (
        ServiceProviders.DECIBYL,
        ServiceProviders.DECIBYL.value,
    )


def compile_ai_model_configuration_v3(
    configuration: OrganizationAIModelConfigurationV3,
) -> EffectiveAIModelConfiguration:
    """Flatten a v3 stack into the shape the pipeline consumes.

    Almost an identity: the effective configuration has always been a flat set
    of sections, and v3 is the stored form finally matching it. The slots that
    say ``decibyl`` are left saying it — ``managed_resolution`` turns those into
    a real vendor and our key later, and it is the only thing that should know
    how a tier maps to a provider.
    """
    stack = configuration.stack
    is_realtime = stack.architecture == "realtime"
    return EffectiveAIModelConfiguration(
        llm=stack.llm,
        stt=None if is_realtime else stack.stt,
        tts=None if is_realtime else stack.tts,
        realtime=stack.realtime if is_realtime else None,
        embeddings=stack.embeddings,
        is_realtime=is_realtime,
    )


def upgrade_v2_to_v3(
    configuration: OrganizationAIModelConfigurationV2,
) -> OrganizationAIModelConfigurationV3:
    """Read a stored v2 configuration as a v3 stack.

    Every v2 configuration is expressible in v3, because v3 only removed a
    restriction. Managed becomes four managed slots; BYOK-pipeline and
    BYOK-realtime become the same slots with the customer's providers and the
    architecture they implied.

    Kept as a read-time upgrade rather than a data migration on purpose. A
    migration would have to rewrite every account's configuration in one step,
    and a mistake there is an outage on the next call for everyone; reading the
    old shape costs nothing and leaves the stored row untouched until the
    customer next saves.
    """
    effective = compile_ai_model_configuration_v2(configuration)
    return OrganizationAIModelConfigurationV3(
        version=3,
        stack=AIModelStackV3(
            architecture="realtime" if effective.is_realtime else "pipeline",
            llm=effective.llm,
            stt=effective.stt,
            tts=effective.tts,
            realtime=effective.realtime,
            embeddings=effective.embeddings,
        ),
    )

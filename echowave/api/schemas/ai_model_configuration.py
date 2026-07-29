from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from api.services.configuration.registry import (
    DecibylEmbeddingsConfiguration,
    DecibylLLMService,
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
    api_key: str
    voice: str = DECIBYL_DEFAULT_VOICE
    speed: float = Field(default=1.0, ge=DECIBYL_SPEED_MIN, le=DECIBYL_SPEED_MAX)
    language: str = DECIBYL_DEFAULT_LANGUAGE


class BYOKPipelineAIModelConfiguration(BaseModel):
    llm: LLMConfig
    tts: TTSConfig
    stt: STTConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_decibyl_providers(self):
        _reject_decibyl_provider("llm", self.llm)
        _reject_decibyl_provider("tts", self.tts)
        _reject_decibyl_provider("stt", self.stt)
        _reject_decibyl_provider("embeddings", self.embeddings)
        return self


class BYOKRealtimeAIModelConfiguration(BaseModel):
    realtime: RealtimeConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_decibyl_providers(self):
        _reject_decibyl_provider("llm", self.llm)
        _reject_decibyl_provider("embeddings", self.embeddings)
        return self


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
    return EffectiveAIModelConfiguration(
        llm=DecibylLLMService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model="default",
        ),
        tts=DecibylTTSService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model="default",
            voice=configuration.voice,
            speed=configuration.speed,
        ),
        stt=DecibylSTTService(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model="default",
            language=configuration.language,
        ),
        embeddings=DecibylEmbeddingsConfiguration(
            provider=ServiceProviders.DECIBYL,
            api_key=configuration.api_key,
            model="decibyl_embedding_v1",
        ),
        is_realtime=False,
        managed_service_version=2,
    )


def _reject_decibyl_provider(section: str, service) -> None:
    if service is None:
        return
    if getattr(service, "provider", None) == ServiceProviders.DECIBYL:
        raise ValueError(f"BYOK {section} cannot use Decibyl provider")

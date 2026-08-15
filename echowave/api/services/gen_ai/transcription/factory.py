"""Which service transcribes an uploaded file for this organization.

The answer is already stored: an account picks its speech-to-text provider and
key on the Model Configurations screen, and that is what its live calls use.
Batch transcription had been ignoring all of it and going to MPS instead —
which meant an account with a perfectly good Deepgram key could not transcribe
an uploaded recording on a deployment that could not reach MPS.

So this resolves the same configuration the voice path resolves, and only falls
back to MPS for the managed tier, which by design holds no vendor keys of its
own.
"""

from __future__ import annotations

from loguru import logger

from .base import BaseTranscriptionService, TranscriptionNotSupportedError
from .providers import (
    DeepgramTranscriptionService,
    MPSTranscriptionService,
    OpenAICompatibleTranscriptionService,
    SarvamTranscriptionService,
)

#: Providers whose prerecorded endpoint is the OpenAI shape. One
#: implementation, a different base_url each.
_OPENAI_COMPATIBLE = {"openai", "groq", "openrouter", "speaches"}

#: Everything with a batch endpoint we implement. Named in the error message
#: for anything else, because "unsupported" without alternatives is a dead end.
SUPPORTED = sorted(_OPENAI_COMPATIBLE | {"deepgram", "sarvam", "decibyl"})


async def build_transcription_service(
    *,
    organization_id: int | None,
    created_by: str | None = None,
) -> BaseTranscriptionService:
    """Resolve this organization's speech-to-text config into a service.

    Falls back to MPS when nothing is configured at all — an account that has
    not been near the model screen yet is on the managed defaults, and that is
    what the managed defaults mean.
    """
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None

    if organization_id is not None:
        from api.services.configuration.ai_model_configuration import (
            get_resolved_ai_model_configuration,
        )

        try:
            resolved = await get_resolved_ai_model_configuration(
                organization_id=organization_id
            )
            stt = getattr(resolved.effective, "stt", None)
            if stt is not None:
                provider = getattr(stt, "provider", None)
                provider = getattr(provider, "value", provider)
                api_key = getattr(stt, "api_key", None)
                model = getattr(stt, "model", None)
                base_url = getattr(stt, "base_url", None)
        except Exception as exc:
            # A configuration lookup that fails is not a reason to refuse the
            # upload outright: MPS is still there for accounts that have it.
            logger.warning(
                "Could not resolve STT configuration for organization {}; "
                "falling back to managed transcription: {}",
                organization_id,
                exc,
            )

    if provider is None or provider == "decibyl":
        return MPSTranscriptionService(
            organization_id=organization_id, created_by=created_by
        )

    if provider in _OPENAI_COMPATIBLE:
        return OpenAICompatibleTranscriptionService(
            api_key=api_key, model=model, base_url=base_url
        )

    if provider == "deepgram":
        return DeepgramTranscriptionService(api_key=api_key, model=model)

    if provider == "sarvam":
        return SarvamTranscriptionService(api_key=api_key, model=model)

    raise TranscriptionNotSupportedError(
        f"No batch transcription implementation for provider {provider!r}",
        user_message=(
            f"Uploaded-file transcription is not available for {provider}. "
            f"Your live calls are unaffected. To transcribe uploads, set "
            f"Speech to Text to one of: {', '.join(SUPPORTED)}."
        ),
    )

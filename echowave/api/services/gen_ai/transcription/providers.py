"""The vendor implementations, one prerecorded endpoint each.

Three shapes cover everything an account can configure for batch work:

* **OpenAI-compatible** — OpenAI itself, and every vendor that mirrors its
  ``/audio/transcriptions`` route (Groq, OpenRouter, Speaches). One
  implementation, pointed at a different ``base_url``.
* **Deepgram** — raw bytes to ``/v1/listen``, transcript nested under
  ``results.channels[0].alternatives[0]``.
* **Sarvam** — multipart to ``/speech-to-text``, and the one that matters most
  here, because the Indic stack is what the managed tier defaults to.

A provider outside those three raises
:class:`TranscriptionNotSupportedError` rather than failing obscurely. Its
live-call STT is unaffected, and saying so is the difference between "pick a
different provider for this screen" and "something is broken".

Every call is bounded by a timeout. An upload that hangs holds a request
worker, and the recordings screen is one a person sits and watches.
"""

from __future__ import annotations

import httpx
from loguru import logger

from api.utils.url_security import validate_user_configured_service_url

from .base import (
    BaseTranscriptionService,
    Transcription,
    TranscriptionError,
)

#: Generous, because a long recording is legitimately slow, and short enough
#: that a wedged vendor does not hold a worker indefinitely.
TIMEOUT = httpx.Timeout(180.0)

#: What a person is told when the vendor failed and there is nothing they can
#: do about it. Vendor status codes and our URLs stay in the log.
VENDOR_FAILED = (
    "We could not transcribe this recording. The problem is on our side or "
    "with the speech provider — try again in a few minutes."
)


def _missing_key(provider: str) -> TranscriptionError:
    return TranscriptionError(
        f"No API key configured for {provider} transcription",
        user_message=(
            "No speech-to-text API key is configured. Add one under Model "
            "Configurations > Speech to Text, then try again."
        ),
    )


class OpenAICompatibleTranscriptionService(BaseTranscriptionService):
    """OpenAI's ``/audio/transcriptions``, and everything shaped like it."""

    DEFAULT_MODEL = "gpt-4o-transcribe"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL
        if base_url:
            # A base_url is customer-typed on the BYOK screen, so it goes
            # through the same SSRF guard as every other user-configured URL.
            validate_user_configured_service_url(base_url, field_name="stt.base_url")
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def get_model_id(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio_data: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
    ) -> Transcription:
        if not self._api_key:
            raise _missing_key("OpenAI")

        data = {"model": self._model}
        if language:
            # OpenAI wants ISO-639-1. A BCP-47 tag like "en-IN" is rejected
            # outright, and the region is not information this endpoint uses.
            data["language"] = language.split("-")[0]

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio_data, content_type)},
                data=data,
            )

        if response.status_code != 200:
            logger.error(
                "OpenAI-compatible transcription failed: {} - {}",
                response.status_code,
                response.text[:500],
            )
            raise TranscriptionError(
                f"Transcription failed: {response.status_code}",
                user_message=VENDOR_FAILED,
            )

        payload = response.json()
        return Transcription(
            transcript=(payload.get("text") or "").strip(),
            language=payload.get("language") or language,
            duration_seconds=payload.get("duration"),
            model=self._model,
        )


class DeepgramTranscriptionService(BaseTranscriptionService):
    """Deepgram's prerecorded endpoint."""

    DEFAULT_MODEL = "nova-2"
    BASE_URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, *, api_key: str | None, model: str | None = None) -> None:
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL

    def get_model_id(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio_data: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
    ) -> Transcription:
        if not self._api_key:
            raise _missing_key("Deepgram")

        params = {"model": self._model, "smart_format": "true"}
        if language:
            params["language"] = language

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                self.BASE_URL,
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Content-Type": content_type,
                },
                params=params,
                content=audio_data,
            )

        if response.status_code != 200:
            logger.error(
                "Deepgram transcription failed: {} - {}",
                response.status_code,
                response.text[:500],
            )
            raise TranscriptionError(
                f"Transcription failed: {response.status_code}",
                user_message=VENDOR_FAILED,
            )

        payload = response.json()
        # Deepgram nests the transcript four levels down and returns an empty
        # alternatives list rather than an error for silent audio, so every
        # step is guarded: an IndexError here would surface as a 500.
        channels = (payload.get("results") or {}).get("channels") or []
        alternatives = (channels[0].get("alternatives") or []) if channels else []
        transcript = (alternatives[0].get("transcript") or "") if alternatives else ""

        return Transcription(
            transcript=transcript.strip(),
            language=language,
            duration_seconds=(payload.get("metadata") or {}).get("duration"),
            model=self._model,
        )


class SarvamTranscriptionService(BaseTranscriptionService):
    """Sarvam's speech-to-text, the Indic default on the managed tier."""

    DEFAULT_MODEL = "saarika:v2.5"
    BASE_URL = "https://api.sarvam.ai/speech-to-text"

    def __init__(self, *, api_key: str | None, model: str | None = None) -> None:
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL

    def get_model_id(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio_data: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
    ) -> Transcription:
        if not self._api_key:
            raise _missing_key("Sarvam")

        data = {"model": self._model}
        if language:
            # Sarvam wants a BCP-47 tag with a region. A bare "en" is not
            # accepted, and "unknown" asks it to detect — which is the right
            # default for an uploaded file whose language nobody declared.
            data["language_code"] = language if "-" in language else "unknown"

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                self.BASE_URL,
                headers={"api-subscription-key": self._api_key},
                files={"file": (filename, audio_data, content_type)},
                data=data,
            )

        if response.status_code != 200:
            logger.error(
                "Sarvam transcription failed: {} - {}",
                response.status_code,
                response.text[:500],
            )
            raise TranscriptionError(
                f"Transcription failed: {response.status_code}",
                user_message=VENDOR_FAILED,
            )

        payload = response.json()
        return Transcription(
            transcript=(payload.get("transcript") or "").strip(),
            language=payload.get("language_code") or language,
            duration_seconds=None,
            model=self._model,
        )


class MPSTranscriptionService(BaseTranscriptionService):
    """The original path, kept for the managed tier.

    A Decibyl-managed account does not hold vendor keys — that is the whole
    point of the tier — so its transcription has to go through the service that
    does. This is now one option among several rather than the only one.
    """

    def __init__(
        self,
        *,
        organization_id: int | None = None,
        created_by: str | None = None,
        model: str = "default",
    ) -> None:
        self._organization_id = organization_id
        self._created_by = created_by
        self._model = model

    def get_model_id(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio_data: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
    ) -> Transcription:
        from api.services.mps_service_key_client import mps_service_key_client

        try:
            payload = await mps_service_key_client.transcribe_audio(
                audio_data=audio_data,
                filename=filename,
                content_type=content_type,
                language=language,
                model=self._model,
                organization_id=self._organization_id,
                created_by=self._created_by,
            )
        except Exception as exc:
            logger.error("MPS transcription failed: {}", exc)
            raise TranscriptionError(
                f"MPS transcription failed: {exc}",
                user_message=VENDOR_FAILED,
            ) from exc

        return Transcription(
            transcript=(payload.get("transcript") or "").strip(),
            language=payload.get("language") or language,
            duration_seconds=payload.get("duration_seconds"),
            model=payload.get("model") or self._model,
        )

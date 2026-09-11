"""Record one sentence in one voice, for the picker to play.

Lifted out of ``scripts/generate_voice_samples.py`` so the runtime and the
script speak to the vendors through one copy of the same code. Two copies of
"how do you ask Sarvam for audio" is how the script and the live path drift
until a sample sounds like a voice nobody is served by.

Nothing here decides *whether* to record — that is ``voice_samples`` — and
nothing here touches storage. One sentence in, audio bytes out.
"""

from __future__ import annotations

import base64

import httpx
from loguru import logger

from api.services.configuration import voice_samples
from api.services.configuration.options.rumik import (
    RUMIK_DEFAULT_DESCRIPTION,
    RUMIK_GATEWAY_URL,
    RUMIK_LANGUAGES,
)
from api.services.configuration.registry import ServiceProviders

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
RUMIK_TTS_PATH = "/v1/tts"

#: The Rumik model with preset speakers. ``muga`` takes none -- it is directed
#: by tone tags in the text -- so there is nothing to name and nothing to
#: sample there; see voice_catalogue._rumik, which returns an empty picker.
RUMIK_SAMPLE_MODEL = "mulberry"

#: Sarvam wants a region-qualified tag; the sample lines are keyed by the bare
#: language.
LANGUAGE_CODES = {
    "en": "en-IN",
    "hi": "hi-IN",
    "ta": "ta-IN",
    "kn": "kn-IN",
    "te": "te-IN",
}


class UnsupportedVoice(ValueError):
    """This vendor, model or language pair has no sample to record."""


def rumik_languages() -> tuple[str, ...]:
    """Only the languages Rumik actually speaks.

    ``RUMIK_LANGUAGES`` is hi-IN and en-IN, and the module that declares it
    says Rumik is "a cost win for Hindi/English agents and unusable for a
    Telugu one". Recording the Tamil, Kannada and Telugu lines anyway would
    not fail -- it would produce confident samples of a model mispronouncing
    a language it does not know, which is worse than no sample at all.
    """
    served = {code.split("-")[0] for code in RUMIK_LANGUAGES}
    return tuple(lang for lang in voice_samples.SAMPLE_LANGUAGES if lang in served)


async def synthesise_sarvam(
    client: httpx.AsyncClient, *, api_key: str, voice: str, language: str, model: str
) -> bytes:
    """One sentence, one Bulbul speaker, as WAV bytes."""
    response = await client.post(
        SARVAM_TTS_URL,
        headers={"api-subscription-key": api_key},
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "target_language_code": LANGUAGE_CODES[language],
            "speaker": voice,
            "model": model,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()

    # Sarvam returns base64 WAV chunks under "audios".
    chunks = payload.get("audios") or []
    if not chunks:
        raise RuntimeError(f"No audio returned for {voice}/{language}: {payload}")
    return b"".join(base64.b64decode(chunk) for chunk in chunks)


async def synthesise_elevenlabs(
    client: httpx.AsyncClient, *, api_key: str, voice_id: str, language: str
) -> bytes:
    """One sentence, one ElevenLabs voice, as MP3 bytes.

    Multilingual v2 speaks every sample language with the same voice, so the
    model is fixed here rather than taken from the agent's stack: the point is
    to hear the *speaker*, and swapping the model between samples would change
    what is being compared.
    """
    response = await client.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id),
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": api_key, "Accept": "audio/mpeg"},
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "model_id": ELEVENLABS_MODEL,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


async def synthesise_rumik(
    client: httpx.AsyncClient, *, api_key: str, voice: str, language: str
) -> bytes:
    """One sentence, one Mulberry voice, as WAV bytes.

    The contract is ``pipecat_rumik.RumikHttpTTSService``'s, which is what the
    call path uses: POST the text with the speaker name, get WAV back. A
    description rides along because Rumik requires one even when a preset
    speaker is named -- without it every voice drifts toward the model's
    neutral prior, which would make twelve samples sound like one.
    """
    response = await client.post(
        f"{RUMIK_GATEWAY_URL.rstrip('/')}{RUMIK_TTS_PATH}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "model": RUMIK_SAMPLE_MODEL,
            "speaker": voice,
            "description": RUMIK_DEFAULT_DESCRIPTION,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


def extension_for(provider: str) -> str:
    """What the vendor hands back. The browser plays either."""
    return "mp3" if provider == ServiceProviders.ELEVENLABS.value else "wav"


async def synthesise(
    *, provider: str, model: str, voice: str, language: str, api_key: str
) -> bytes:
    """One sentence in one voice, from whichever vendor serves it.

    Raises :class:`UnsupportedVoice` for a pair there is no honest sample for
    -- a language the vendor does not speak, or a model with no named
    speakers -- so the caller can decline rather than store a bad recording.
    """
    if language not in voice_samples.SAMPLE_LINES:
        raise UnsupportedVoice(f"No sample line is written for {language!r}.")

    async with httpx.AsyncClient() as client:
        if provider == ServiceProviders.SARVAM.value:
            return await synthesise_sarvam(
                client, api_key=api_key, voice=voice, language=language, model=model
            )
        if provider == ServiceProviders.ELEVENLABS.value:
            return await synthesise_elevenlabs(
                client, api_key=api_key, voice_id=voice, language=language
            )
        if provider == ServiceProviders.RUMIK.value:
            if (model or RUMIK_SAMPLE_MODEL).strip().lower() == "muga":
                raise UnsupportedVoice(
                    "Muga takes no preset speaker, so there is no voice to record."
                )
            if language not in rumik_languages():
                raise UnsupportedVoice(
                    f"Rumik does not speak {language!r}; a sample would be a "
                    "confident mispronunciation."
                )
            return await synthesise_rumik(
                client, api_key=api_key, voice=voice, language=language
            )

    logger.debug("No sample recorder for provider {}", provider)
    raise UnsupportedVoice(f"No sample recorder is written for {provider!r}.")

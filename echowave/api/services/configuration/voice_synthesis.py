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
from api.services.configuration.options.smallest import SMALLEST_TTS_LANGUAGES
from api.services.configuration.registry import ServiceProviders

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
RUMIK_TTS_PATH = "/v1/tts"
SMALLEST_TTS_URL = "https://api.smallest.ai/waves/v1/tts"

#: The model a sample is recorded on when the caller names none. Lightning
#: v3.1 is the cheaper rung and the one a customer lands on by default, so it
#: is the honest thing to hear first.
SMALLEST_SAMPLE_MODEL = "lightning_v3.1"

#: Which languages a Smallest sample may be recorded in: the ones this
#: repository already claims the vendor speaks, narrowed to the ones a sample
#: line exists for.
#:
#: Derived rather than written out, and that is the point. Smallest's current
#: documentation lists ten Indic languages including Telugu; this repository's
#: own ``SMALLEST_TTS_LANGUAGES`` does not have Telugu, and neither does
#: pipecat's map. Hardcoding the documentation here would have let a Telugu
#: sample through on the strength of one page read once, against two sources
#: in the tree saying otherwise -- and a sample in a language a vendor cannot
#: speak is the confident mispronunciation this guard exists to prevent.
#:
#: So the vendor's language support stays declared in one place. If Telugu is
#: genuinely supported, adding it to ``SMALLEST_TTS_LANGUAGES`` is the change,
#: and every caller gets it rather than just this one.
SMALLEST_LANGUAGES = frozenset(SMALLEST_TTS_LANGUAGES) & frozenset(
    voice_samples.SAMPLE_LANGUAGES
)

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
    client: httpx.AsyncClient,
    *,
    api_key: str,
    voice_id: str,
    language: str,
    model: str | None = None,
) -> bytes:
    """One sentence, one ElevenLabs voice, on one model, as MP3 bytes.

    The model used to be fixed at Multilingual v2 so that browsing a list of
    speakers compared speakers and nothing else. That is right for the list
    and wrong for the decision *behind* the list: ElevenLabs ships three
    models here at two prices, they do not sound alike on Tamil, and with the
    model fixed there was nowhere in the product to hear the difference.

    Comparing speakers is preserved, because the sample is now keyed by model
    as well: a picker showing one model still varies only the voice. What
    changes is that switching the model switches the recording too.
    """
    response = await client.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id),
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": api_key, "Accept": "audio/mpeg"},
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "model_id": (model or "").strip() or ELEVENLABS_MODEL,
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


async def synthesise_smallest(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    voice: str,
    language: str,
    model: str | None = None,
) -> bytes:
    """One sentence, one Waves voice, as WAV bytes.

    The field names are the ones pipecat's own ``SmallestTTSService`` puts on
    the wire for a live call -- ``voice_id``, ``model``, ``language``,
    ``sample_rate`` -- so a sample is recorded through the same contract that
    will speak on the phone. Only the transport differs: one HTTP request here
    against the websocket the call path holds open.

    ``language`` is the bare ISO code rather than the region-qualified tag
    Sarvam wants, which is why this does not go through ``LANGUAGE_CODES``.
    """
    response = await client.post(
        SMALLEST_TTS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "voice_id": voice,
            "model": (model or "").strip() or SMALLEST_SAMPLE_MODEL,
            "language": language,
            "sample_rate": 24000,
            "output_format": "wav",
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


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
                client,
                api_key=api_key,
                voice_id=voice,
                language=language,
                model=model,
            )
        if provider == ServiceProviders.SMALLEST.value:
            if language not in SMALLEST_LANGUAGES:
                raise UnsupportedVoice(
                    f"Smallest does not speak {language!r}; a sample would be a "
                    "confident mispronunciation."
                )
            return await synthesise_smallest(
                client,
                api_key=api_key,
                voice=voice,
                language=language,
                model=model,
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

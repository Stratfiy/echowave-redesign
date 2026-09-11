"""Generate the audio samples the voice picker plays.

Run once per managed voice catalogue change. Nothing at runtime depends on
this having been run — a voice with no sample simply shows no play button —
so it is safe to run late, re-run, or skip.

    export SARVAM_API_KEY=...
    source venv/bin/activate && set -a && source api/.env && set +a
    python -m scripts.generate_voice_samples            # only what is missing
    python -m scripts.generate_voice_samples --force    # re-record everything

Uses the platform's own stored key when the environment does not carry one, so
a deployment does not need a second credential just to record seven sentences.
It exits non-zero when there are voices to sample and no key to sample them
with — it used to exit 0, which is how a deployment came to have a voice picker
with no play buttons and no sign anything was wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys

import httpx
from loguru import logger

from api.services.configuration import voice_catalogue, voice_samples
from api.services.configuration.options.rumik import (
    RUMIK_DEFAULT_DESCRIPTION,
    RUMIK_GATEWAY_URL,
    RUMIK_LANGUAGES,
    RUMIK_VOICES,
)
from api.services.storage import get_storage

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
RUMIK_TTS_PATH = "/v1/tts"

#: The Rumik model with preset speakers. ``muga`` takes none — it is directed
#: by tone tags in the text — so there is nothing to name and nothing to sample
#: there; see voice_catalogue._rumik, which returns an empty picker for it.
RUMIK_SAMPLE_MODEL = "mulberry"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL = "eleven_multilingual_v2"

#: The tiers the picker offers. v2 and v3 have entirely different speaker
#: names (anushka/karun on v2, shubh/aditya on v3), and an agent on either can
#: reach the picker — so both are sampled, or half the voices show no play
#: button. Sampling a model nobody is served by would put a voice in front of a
#: customer their calls will not use, so this is exactly the served tiers, no
#: more.
SAMPLE_MODELS = ("bulbul:v2", "bulbul:v3")

_LANGUAGE_CODES = {
    "en": "en-IN",
    "hi": "hi-IN",
    "ta": "ta-IN",
    "kn": "kn-IN",
    "te": "te-IN",
}


async def _vendor_key(env_name: str, provider: str) -> str | None:
    """The vendor key to record with: the environment first, then the vault.

    This file's own instructions have always said it "uses the platform's own
    Sarvam key when one is stored, so a deployment does not need a second
    credential just to record seven sentences". It did not. Both keys were read
    from the environment and nowhere else, so on a deployment that holds its
    Sarvam key in the platform vault — which is every managed deployment — the
    script found nothing, said so at INFO, and exited 0. A provisioning script
    that succeeds silently while doing nothing is worse than one that fails:
    the samples were never recorded and nobody had a reason to look.

    The environment still wins, so recording against a scratch key without
    touching the vault keeps working.
    """
    from_env = os.getenv(env_name)
    if from_env:
        return from_env

    try:
        from api.db import db_client
        from api.enums import CostComponent
        from api.services.configuration import platform_credentials

        async with db_client.async_session() as session:
            key = await platform_credentials.resolve_api_key(
                session, component=CostComponent.TTS, provider=provider
            )
    except Exception as exc:  # noqa: BLE001 - a script, and the caller reports it
        logger.warning("Could not read the platform {} key: {}", provider, exc)
        return None

    if key:
        logger.info("Using the platform's stored {} key.", provider)
    return key


async def _synthesise(
    client: httpx.AsyncClient, *, api_key: str, voice: str, language: str, model: str
) -> bytes:
    """One sentence, one voice, as WAV bytes."""
    response = await client.post(
        SARVAM_TTS_URL,
        headers={"api-subscription-key": api_key},
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "target_language_code": _LANGUAGE_CODES[language],
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


async def _synthesise_elevenlabs(
    client: httpx.AsyncClient, *, api_key: str, voice_id: str, language: str
) -> bytes:
    """One sentence, one ElevenLabs voice, as MP3 bytes. Multilingual v2
    speaks every sample language with the same voice."""
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


async def _synthesise_rumik(
    client: httpx.AsyncClient, *, api_key: str, voice: str, language: str
) -> bytes:
    """One sentence, one Mulberry voice, as WAV bytes.

    The contract is ``pipecat_rumik.RumikHttpTTSService``'s, which is what the
    call path uses: POST the text with the speaker name, get WAV back. A
    description rides along because Rumik requires one even when a preset
    speaker is named — without it every voice drifts toward the model's neutral
    prior, which would make twelve samples sound like one.
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


def _rumik_languages() -> tuple[str, ...]:
    """Only the languages Rumik actually speaks.

    RUMIK_LANGUAGES is hi-IN and en-IN, and the module that declares it says
    Rumik is "a cost win for Hindi/English agents and unusable for a Telugu
    one". Recording the Tamil, Kannada and Telugu lines anyway would not fail —
    it would produce twelve confident samples of a model mispronouncing a
    language it does not know, which is worse than no sample at all.
    """
    served = {code.split("-")[0] for code in RUMIK_LANGUAGES}
    return tuple(
        language for language in voice_samples.SAMPLE_LANGUAGES if language in served
    )


async def _rumik_samples(force: bool) -> tuple[int, int, int]:
    """Mulberry's preset voices, in the two languages it speaks."""
    api_key = await _vendor_key("RUMIK_API_KEY", "rumik")
    if not api_key:
        logger.info(
            "No Rumik key in the environment or the platform vault; "
            "skipping the Rumik voices."
        )
        return 0, 0, 0

    languages = _rumik_languages()
    storage = get_storage()

    # Sample paths are keyed by voice id alone, and so is the URL the picker
    # builds, so two vendors sharing a name cannot both have a recording: the
    # second run overwrites the first and the picker then plays one vendor's
    # voice under the other's label. "sophia" is in both catalogues today.
    #
    # Sarvam is the managed default, so its recording wins and the clash is
    # skipped rather than silently overwritten. One voice loses its play button;
    # nothing is ever mislabelled. Namespacing the path by provider would fix
    # this properly, but it changes a convention the UI depends on and orphans
    # every sample already recorded — not a thing to do as a side effect of
    # adding a vendor.
    reserved = {voice_id.lower() for voice_id, _ in _sarvam_voices_to_sample()}

    written = skipped = failed = 0
    async with httpx.AsyncClient() as client:
        for voice in RUMIK_VOICES:
            if voice.lower() in reserved:
                logger.warning(
                    "Skipping Rumik '{}': the name is also a Sarvam voice and "
                    "they would share one sample path.",
                    voice,
                )
                continue
            for language in languages:
                path = voice_samples.sample_path(voice, language)
                if not force and await storage.aget_file_metadata(path) is not None:
                    skipped += 1
                    continue
                try:
                    audio = await _synthesise_rumik(
                        client, api_key=api_key, voice=voice, language=language
                    )
                    await storage.acreate_file_from_bytes(path, audio)
                    logger.info(f"wrote {path} ({len(audio)} bytes)")
                    written += 1
                except Exception as exc:
                    # One bad voice must not abandon the rest, same as Sarvam.
                    logger.error(f"failed {voice}/{language}: {exc}")
                    failed += 1

    return written, skipped, failed


async def _elevenlabs_samples(force: bool) -> tuple[int, int, int]:
    """The template gallery's suggested voices, each in its own language."""
    api_key = await _vendor_key("ELEVENLABS_API_KEY", "elevenlabs")
    if not api_key:
        logger.info(
            "No ElevenLabs key in the environment or the platform vault; "
            "skipping the gallery voices."
        )
        return 0, 0, 0
    from api.services.agent_templates import list_templates

    wanted = {
        (v.voice_id, v.language)
        for t in list_templates()
        for v in t.suggested_voices
        if v.provider == "elevenlabs"
    }
    storage = get_storage()
    written = skipped = failed = 0
    async with httpx.AsyncClient() as client:
        for voice_id, language in sorted(wanted):
            path = voice_samples.sample_path(voice_id, language, "mp3")
            if not force and await storage.aget_file_metadata(path) is not None:
                skipped += 1
                continue
            try:
                audio = await _synthesise_elevenlabs(
                    client, api_key=api_key, voice_id=voice_id, language=language
                )
                await storage.acreate_file_from_bytes(path, audio)
                logger.info(f"wrote {path} ({len(audio)} bytes)")
                written += 1
            except Exception as exc:
                logger.error(f"failed elevenlabs {voice_id}/{language}: {exc}")
                failed += 1
    return written, skipped, failed


def _sarvam_voices_to_sample() -> list[tuple[str, str]]:
    """(voice_id, model) for every Sarvam voice the picker can show, once each.

    Deduped by voice_id: the sample path is keyed by voice alone, so a name
    shared across tiers needs only one recording, taken under the first tier
    that lists it.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for model in SAMPLE_MODELS:
        for voice in voice_catalogue.for_provider("sarvam", model=model).voices:
            if voice.voice_id in seen:
                continue
            seen.add(voice.voice_id)
            out.append((voice.voice_id, model))
    return out


async def main(force: bool) -> int:
    eleven_written, eleven_skipped, eleven_failed = await _elevenlabs_samples(force)
    logger.info(
        f"elevenlabs: {eleven_written} written, {eleven_skipped} skipped, {eleven_failed} failed"
    )

    rumik_written, rumik_skipped, rumik_failed = await _rumik_samples(force)
    logger.info(
        f"rumik: {rumik_written} written, {rumik_skipped} skipped, {rumik_failed} failed"
    )
    eleven_written += rumik_written
    eleven_failed += rumik_failed
    api_key = await _vendor_key("SARVAM_API_KEY", "sarvam")
    voices = _sarvam_voices_to_sample()

    if not api_key:
        if voices:
            # There is work to do and no way to do it. This used to return 0,
            # so the run looked clean and the picker stayed silent.
            logger.error(
                "No Sarvam key in the environment or the platform vault, and "
                "{} managed voice(s) have no sample. Set SARVAM_API_KEY or "
                "store the key under Provider keys, then run this again.",
                len(voices),
            )
            return 1
        logger.info("No Sarvam key and no managed voices to sample.")
        return 1 if eleven_failed and not eleven_written else 0

    storage = get_storage()
    if not voices:
        logger.error("The voice catalogue is empty; nothing to sample.")
        return 1

    written = skipped = failed = 0
    async with httpx.AsyncClient() as client:
        for voice_id, model in voices:
            for language in voice_samples.SAMPLE_LANGUAGES:
                path = voice_samples.sample_path(voice_id, language)

                if not force and await storage.aget_file_metadata(path) is not None:
                    logger.info(f"exists, skipping: {path}")
                    skipped += 1
                    continue

                try:
                    audio = await _synthesise(
                        client,
                        api_key=api_key,
                        voice=voice_id,
                        language=language,
                        model=model,
                    )
                    await storage.acreate_file_from_bytes(path, audio)
                    logger.info(f"wrote {path} ({len(audio)} bytes)")
                    written += 1
                except Exception as exc:
                    # One bad voice must not abandon the rest — a partial set
                    # is a picker with some play buttons, which is strictly
                    # better than none.
                    logger.error(f"failed {voice_id}/{language}: {exc}")
                    failed += 1

    logger.info(f"done: {written} written, {skipped} skipped, {failed} failed")
    return 1 if failed and not written else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="Re-record samples that already exist"
    )
    sys.exit(asyncio.run(main(parser.parse_args().force)))

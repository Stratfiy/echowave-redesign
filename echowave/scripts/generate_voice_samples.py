"""Generate the audio samples the voice picker plays.

Run once per managed voice catalogue change. Nothing at runtime depends on
this having been run — a voice with no sample simply shows no play button —
so it is safe to run late, re-run, or skip.

    export SARVAM_API_KEY=...
    source venv/bin/activate && set -a && source api/.env && set +a
    python -m scripts.generate_voice_samples            # only what is missing
    python -m scripts.generate_voice_samples --force    # re-record everything

Uses the platform's own Sarvam key when one is stored, so a deployment does not
need a second credential just to record seven sentences.
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
from api.services.storage import get_storage

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
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


async def _elevenlabs_samples(force: bool) -> tuple[int, int, int]:
    """The template gallery's suggested voices, each in its own language."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logger.info("ELEVENLABS_API_KEY is not set; skipping the gallery voices.")
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
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        logger.info("SARVAM_API_KEY is not set; the managed voices were not sampled.")
        return 1 if eleven_failed and not eleven_written else 0

    storage = get_storage()
    voices = _sarvam_voices_to_sample()
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

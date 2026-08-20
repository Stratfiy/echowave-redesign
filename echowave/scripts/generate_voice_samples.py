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

#: The tier the picker offers. Sampling a model nobody is served by would put
#: a voice in front of a customer that their calls will not use.
SAMPLE_MODEL = "bulbul:v2"

_LANGUAGE_CODES = {"en": "en-IN", "hi": "hi-IN"}


async def _synthesise(
    client: httpx.AsyncClient, *, api_key: str, voice: str, language: str
) -> bytes:
    """One sentence, one voice, as WAV bytes."""
    response = await client.post(
        SARVAM_TTS_URL,
        headers={"api-subscription-key": api_key},
        json={
            "text": voice_samples.SAMPLE_LINES[language],
            "target_language_code": _LANGUAGE_CODES[language],
            "speaker": voice,
            "model": SAMPLE_MODEL,
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


async def main(force: bool) -> int:
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        logger.error("SARVAM_API_KEY is not set. Nothing to record with.")
        return 1

    storage = get_storage()
    catalogue = voice_catalogue.for_provider("sarvam", model=SAMPLE_MODEL)
    if not catalogue.voices:
        logger.error("The voice catalogue is empty; nothing to sample.")
        return 1

    written = skipped = failed = 0
    async with httpx.AsyncClient() as client:
        for voice in catalogue.voices:
            for language in voice_samples.SAMPLE_LANGUAGES:
                path = voice_samples.sample_path(voice.voice_id, language)

                if not force and await storage.aget_file_metadata(path) is not None:
                    logger.info(f"exists, skipping: {path}")
                    skipped += 1
                    continue

                try:
                    audio = await _synthesise(
                        client,
                        api_key=api_key,
                        voice=voice.voice_id,
                        language=language,
                    )
                    await storage.acreate_file_from_bytes(path, audio)
                    logger.info(f"wrote {path} ({len(audio)} bytes)")
                    written += 1
                except Exception as exc:
                    # One bad voice must not abandon the rest — a partial set
                    # is a picker with some play buttons, which is strictly
                    # better than none.
                    logger.error(f"failed {voice.voice_id}/{language}: {exc}")
                    failed += 1

    logger.info(f"done: {written} written, {skipped} skipped, {failed} failed")
    return 1 if failed and not written else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="Re-record samples that already exist"
    )
    sys.exit(asyncio.run(main(parser.parse_args().force)))

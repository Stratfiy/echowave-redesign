"""A meeting's words, from Sarvam, one short segment at a time.

The batch transcription package (``services/gen_ai/transcription``) already
speaks to Sarvam's speech-to-text; this reuses its ``SarvamTranscriptionService``
rather than a second client. Three things are specific to meetings:

* **Sarvam, always.** The founder's decision is Sarvam for voice in Indian
  languages, so a meeting is transcribed by Sarvam whatever the workspace's
  call STT is set to. The key is the workspace's own Sarvam key when its STT
  is Sarvam, else the platform's Sarvam key. With neither, transcription is
  ``needs_setup`` -- said on the screen before anything is recorded, never
  discovered after an hour of talking.
* **Short pieces.** Sarvam's synchronous endpoint takes about thirty seconds
  of audio. Live capture already arrives in pieces shorter than that; an
  upload is cut into ``SEGMENT_SECONDS`` pieces with ffmpeg first.
* **The audio does not stay.** A segment's audio is dropped the moment its
  words are stored (recording retention is off by default, handoff 24). A
  segment that failed keeps its audio, so "Try again" has something to try.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.services.gen_ai.transcription import TranscriptionError
from api.services.gen_ai.transcription.providers import SarvamTranscriptionService
from api.services.shell import languages

#: Length of one piece of an upload. Under Sarvam's limit with room to spare.
SEGMENT_SECONDS = 25
#: Largest single live segment accepted (about a minute of compressed audio
#: at a generous bitrate).
MAX_SEGMENT_BYTES = 5 * 1024 * 1024

#: Sarvam's codes for the launch languages it hears. ``unknown`` asks it to
#: detect; it is also what a language without voice support falls back to.
SARVAM_CODES = {
    language.code: f"{language.code}-IN"
    for language in languages.LANGUAGES
    if language.voice
}

NEEDS_SETUP = (
    "Transcription needs setup: no Sarvam speech-to-text key is configured "
    "for this workspace or the platform."
)


def sarvam_code(language: str | None) -> str:
    return SARVAM_CODES.get((language or "").strip(), "unknown")


def meeting_languages() -> list[dict]:
    """The languages a meeting can be held in, by their own names; the ones
    Sarvam cannot hear are left out of the picker, with automatic detection
    as the first choice."""
    return [
        {"code": "unknown", "native": "Detect automatically", "english": "Detect"},
        *(
            {"code": item["code"], "native": item["native"], "english": item["english"]}
            for item in languages.as_dicts()
            if item["voice"]
        ),
    ]


def is_meeting_language(code: str | None) -> bool:
    return code == "unknown" or code in SARVAM_CODES


@dataclass(frozen=True)
class SarvamSetup:
    api_key: str | None
    model: str | None
    #: ``workspace`` or ``platform``, for the capability line; never the key.
    key_source: str | None

    @property
    def available(self) -> bool:
        return bool(self.api_key)


async def sarvam_setup(organization_id: int) -> SarvamSetup:
    """Which Sarvam key and model a meeting in this workspace would use."""
    from api.enums import CostComponent
    from api.services.configuration import platform_credentials

    model = None
    try:
        from api.services.configuration.ai_model_configuration import (
            get_resolved_ai_model_configuration,
        )

        resolved = await get_resolved_ai_model_configuration(
            organization_id=organization_id
        )
        stt = getattr(resolved.effective, "stt", None)
        provider = getattr(stt, "provider", None) if stt is not None else None
        provider = getattr(provider, "value", provider)
        if provider == "sarvam":
            model = getattr(stt, "model", None)
            key = getattr(stt, "api_key", None)
            if key:
                return SarvamSetup(api_key=key, model=model, key_source="workspace")
    except Exception as exc:  # noqa: BLE001 - the platform key may still serve
        logger.warning(
            "Could not read the STT configuration for org {}: {}", organization_id, exc
        )
    try:
        async with db_client.async_session() as session:
            key = await platform_credentials.resolve_api_key(
                session, component=CostComponent.STT, provider="sarvam"
            )
    except Exception as exc:  # noqa: BLE001 - reported as needs setup
        logger.warning("Could not read the platform Sarvam key: {}", exc)
        key = None
    if key:
        return SarvamSetup(api_key=key, model=model, key_source="platform")
    return SarvamSetup(api_key=None, model=None, key_source=None)


def build_service(setup: SarvamSetup) -> SarvamTranscriptionService:
    """The one place a Sarvam client is made, so a test swaps one name."""
    return SarvamTranscriptionService(api_key=setup.api_key, model=setup.model)


# --- action cues ------------------------------------------------------------

#: Words that often mark a commitment. Used only to show "possible actions so
#: far" during capture (screen 11's optional strip); the real suggestions come
#: from reading the whole transcript afterwards. English and Hindi only for
#: now; another language simply shows no strip, never a wrong one.
_CUES = re.compile(
    r"\b(i'?ll|we'?ll|i will|we will|will send|will share|will call|follow[- ]?up|"
    r"action item|deadline|remind|by (monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|tomorrow|tonight|next week|end of (the )?(day|week))|eod)\b"
    r"|करेंगे|करूँगा|करूंगा|करूंगी|भेज|कल तक|याद दिला",
    re.IGNORECASE,
)


def has_action_cue(words: str | None) -> bool:
    return bool(words) and bool(_CUES.search(words or ""))


# --- ffmpeg -----------------------------------------------------------------


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    return process.returncode or 0, out, err


async def probe_seconds(path: str) -> float | None:
    code, out, _ = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    )
    if code != 0:
        return None
    try:
        return float(out.decode().strip())
    except ValueError:
        return None


async def to_wav(audio: bytes) -> bytes | None:
    """16 kHz mono WAV, the shape every Sarvam model takes. None when ffmpeg
    is missing or cannot read it -- the caller then sends the bytes as they
    are and lets Sarvam decide."""
    if not ffmpeg_available():
        return None
    with tempfile.TemporaryDirectory(prefix="meeting-") as folder:
        source = os.path.join(folder, "in")
        target = os.path.join(folder, "out.wav")
        with open(source, "wb") as handle:
            handle.write(audio)
        code, _, err = await _run(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source,
            "-ac",
            "1",
            "-ar",
            "16000",
            target,
        )
        if code != 0 or not os.path.exists(target):
            logger.warning("ffmpeg could not convert a meeting segment: {}", err[:300])
            return None
        with open(target, "rb") as handle:
            return handle.read()


class SplitRefused(ValueError):
    """An upload that cannot be processed, with the line a person reads."""


async def split_upload(
    audio: bytes, *, max_seconds: int
) -> list[tuple[int, int, bytes]]:
    """Cut an uploaded recording into ``SEGMENT_SECONDS`` WAV pieces.

    Returns ``(start_ms, end_ms, wav)`` per piece. Refuses, in words, a file
    ffmpeg cannot read and one longer than ``max_seconds`` -- checked before
    a single piece is sent anywhere.
    """
    if not ffmpeg_available():
        raise SplitRefused(
            "Uploaded recordings need setup on this server (audio tools are "
            "missing). Paste notes instead, or record on this device."
        )
    with tempfile.TemporaryDirectory(prefix="meeting-upload-") as folder:
        source = os.path.join(folder, "upload")
        with open(source, "wb") as handle:
            handle.write(audio)
        seconds = await probe_seconds(source)
        if seconds is None or seconds <= 0:
            raise SplitRefused(
                "This file could not be read as audio. Try an MP3, M4A, WAV, "
                "OGG or WebM recording."
            )
        if seconds > max_seconds:
            raise SplitRefused(
                f"This recording is {round(seconds / 60)} minutes long; the most "
                f"that can be transcribed is {max_seconds // 60} minutes."
            )
        pattern = os.path.join(folder, "piece-%05d.wav")
        code, _, err = await _run(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "segment",
            "-segment_time",
            str(SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            pattern,
        )
        if code != 0:
            logger.warning("ffmpeg could not split an upload: {}", err[:300])
            raise SplitRefused("This recording could not be split for transcription.")
        pieces = sorted(
            name for name in os.listdir(folder) if name.startswith("piece-")
        )
        out: list[tuple[int, int, bytes]] = []
        total_ms = int(seconds * 1000)
        for index, name in enumerate(pieces):
            with open(os.path.join(folder, name), "rb") as handle:
                data = handle.read()
            start = index * SEGMENT_SECONDS * 1000
            end = min(total_ms, start + SEGMENT_SECONDS * 1000)
            out.append((start, end, data))
        return out


# --- one segment ------------------------------------------------------------


async def transcribe_segment(
    segment_id: int, organization_id: int, language: str
) -> str:
    """Transcribe one pending segment, if nobody else is. Returns the state
    the segment ended in (``done``, ``failed``, or ``skipped`` when another
    worker had it)."""
    segment = await db_client.claim_meeting_segment(
        segment_id, organization_id=organization_id
    )
    if segment is None:
        return "skipped"
    if not segment.audio:
        await db_client.update_meeting_segment(
            segment.id,
            organization_id=organization_id,
            status="failed",
            error="The audio for this part did not arrive.",
        )
        return "failed"
    setup = await sarvam_setup(organization_id)
    if not setup.available:
        await db_client.update_meeting_segment(
            segment.id,
            organization_id=organization_id,
            status="failed",
            error=NEEDS_SETUP,
        )
        return "failed"
    audio = bytes(segment.audio)
    wav = await to_wav(audio)
    try:
        result = await build_service(setup).transcribe(
            wav or audio,
            filename="segment.wav" if wav else "segment.webm",
            content_type="audio/wav" if wav else (segment.content_type or "audio/webm"),
            language=sarvam_code(language),
        )
    except TranscriptionError as exc:
        logger.warning("Meeting segment {} not transcribed: {}", segment.id, exc)
        await db_client.update_meeting_segment(
            segment.id,
            organization_id=organization_id,
            status="failed",
            error=exc.user_message,
        )
        return "failed"
    except Exception as exc:  # noqa: BLE001 - said on the segment, kept for retry
        logger.error("Meeting segment {} transcription broke: {}", segment.id, exc)
        await db_client.update_meeting_segment(
            segment.id,
            organization_id=organization_id,
            status="failed",
            error="This part could not be transcribed. Try again.",
        )
        return "failed"
    words = (result.transcript or "").strip()
    await db_client.update_meeting_segment(
        segment.id,
        organization_id=organization_id,
        status="done",
        text=words,
        language=result.language,
        error=None,
        has_action_cue=has_action_cue(words),
        transcribed_at=datetime.now(UTC),
        # The words are kept; the audio is not (retention off by default).
        audio=None,
    )
    return "done"

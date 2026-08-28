"""Letting somebody hear a voice before they put it on their phone line.

A voice is the one thing on the picker nobody can evaluate by reading. Seven
Indian first names and a gender is a list, not a choice — and the vendor
publishes no per-voice description for bulbul:v2 to borrow, so there is nothing
honest to write beside them either. The sample *is* the information.

**Pre-generated, not synthesised on demand.** Somebody auditioning voices
clicks every one, twice, and then again after changing the language. Doing that
live would be fourteen TTS calls to answer a question whose answer never
changes, on the screen where a first-time customer is deciding whether to
continue.

**Served by convention, not by a lookup.** The key is derived from the voice id
and language, so the UI can build the URL itself and a missing sample is a
quiet 404 on an audio element rather than a broken page. Generating them is a
one-off script; nothing at runtime depends on it having been run.
"""

from __future__ import annotations

import asyncio

from api.services.storage import get_storage

#: Where samples live. Separate prefix so a retention sweep over call audio
#: never touches them — they are product assets, not customer data.
SAMPLE_PREFIX = "voice-samples"

#: What each voice says. Chosen to exercise what a caller actually hears in the
#: first two seconds: a greeting, a company name, and a question. Not a
#: pangram — the point is how it sounds answering a phone, not coverage.
SAMPLE_LINES: dict[str, str] = {
    "en": (
        "Hello, thanks for calling Sunrise Clinic. "
        "Am I speaking with the patient, or someone calling on their behalf?"
    ),
    "hi": ("नमस्ते, सनराइज़ क्लिनिक में कॉल करने के लिए धन्यवाद। क्या मैं मरीज़ से बात कर रही हूँ?"),
}

#: The languages a sample is generated in. Kept to two deliberately: the point
#: is to hear the voice, and a picker with seven voices times ten languages is
#: the reading problem this was meant to solve.
SAMPLE_LANGUAGES = ("en", "hi")

#: How long one sample lookup may take before the picker stops waiting for it.
#:
#: Catching the exception below was not enough on its own. An object store that
#: is *unreachable* does not fail fast — the client retries with backoff, and
#: the call eventually succeeds at failing. Serially, once per voice per
#: language, that turned "no samples" into a model picker that took 468 seconds
#: to answer with the store down, which a browser experiences as a screen that
#: never loads. A sample is optional; the screen it sits on is not.
#:
#: Generous against a healthy store — a metadata HEAD against MinIO or S3
#: answers in milliseconds — and short enough that the whole catalogue,
#: looked up in parallel, costs about a second in the worst case.
SAMPLE_LOOKUP_TIMEOUT_SECONDS = 1.5


def sample_path(voice_id: str, language: str) -> str:
    """Storage key for one voice in one language. Derived, never stored."""
    return f"{SAMPLE_PREFIX}/{voice_id.strip().lower()}-{language}.wav"


async def sample_url(voice_id: str, language: str = "en") -> str | None:
    """A URL the browser can play, or ``None`` when there is no sample yet.

    Returning ``None`` rather than a URL that 404s keeps the decision in one
    place: the picker renders a play button only for voices it can actually
    play, instead of offering one that fails when clicked.
    """
    if language not in SAMPLE_LANGUAGES:
        return None

    storage = get_storage()
    path = sample_path(voice_id, language)
    try:
        return await asyncio.wait_for(
            _resolve(storage, path),
            timeout=SAMPLE_LOOKUP_TIMEOUT_SECONDS,
        )
    except (Exception, asyncio.TimeoutError):
        # Storage being unreachable must not take the model picker down with
        # it — neither by raising nor by making it wait. No sample is a worse
        # picker; either of those is no picker.
        return None


async def _resolve(storage, path: str) -> str | None:
    if await storage.aget_file_metadata(path) is None:
        return None
    return await storage.aget_signed_url(path)


async def sample_urls(
    voice_ids: list[str],
    languages: tuple[str, ...] = SAMPLE_LANGUAGES,
) -> dict[tuple[str, str], str | None]:
    """Every voice's sample URL, looked up at once.

    Concurrent rather than serial because the lookups are independent and the
    caller needs all of them to draw one screen: in sequence, the deadline
    above is paid once per voice per language, and the catalogue is dozens of
    voices — a wait nobody sits through, and one that grows every time a voice
    is added. Failures stay per-lookup — a store that answers for some keys
    and not others still gets its play buttons.
    """
    keys = [(voice_id, language) for voice_id in voice_ids for language in languages]
    urls = await asyncio.gather(
        *(sample_url(voice_id, language) for voice_id, language in keys)
    )
    return dict(zip(keys, urls))

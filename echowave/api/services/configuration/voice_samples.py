"""Letting somebody hear a voice before they put it on their phone line.

A voice is the one thing on the picker nobody can evaluate by reading. Seven
Indian first names and a gender is a list, not a choice — and the vendor
publishes no per-voice description for bulbul:v2 to borrow, so there is nothing
honest to write beside them either. The sample *is* the information.

**Recorded once, on the first person to ask.** Somebody auditioning voices
clicks every one, twice, and then again after changing the language -- so the
answer has to be cached, because it never changes. It used to be cached by
having an operator run a script, which is the same idea with a human in the
middle, and the human is why this never worked: the script was never run
against production, so every picker showed sixteen names and no play buttons
for months while the feature was repeatedly reported as missing.

So the cache fills itself. :func:`sample_url` stays a pure lookup, because the
picker asks it about every voice at once and forty TTS calls to draw a list is
not a page anybody waits for. :func:`ensure_sample_url` is the one that
records, and it is called when somebody actually presses play on a voice
nobody has pressed play on before. That costs one vendor call, once, for every
customer who ever opens that picker afterwards.

**Served by convention, not by a lookup.** The key is derived from the voice id
and language, so the UI can build the URL itself and a missing sample is a
quiet 404 on an audio element rather than a broken page.
"""

from __future__ import annotations

from loguru import logger

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
    "ta": "வணக்கம், சன்ரைஸ் கிளினிக். நான் நோயாளியிடம் பேசுகிறேனா, அல்லது அவர் சார்பாக அழைக்கிறீர்களா?",
    "kn": "ನಮಸ್ಕಾರ, ಸನ್‌ರೈಸ್ ಕ್ಲಿನಿಕ್. ನಾನು ರೋಗಿಯೊಂದಿಗೆ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆಯೇ, ಅಥವಾ ಅವರ ಪರವಾಗಿ ಕರೆ ಮಾಡುತ್ತಿದ್ದೀರಾ?",
    "te": "నమస్కారం, సన్‌రైజ్ క్లినిక్. నేను రోగితో మాట్లాడుతున్నానా, లేక వారి తరఫున కాల్ చేస్తున్నారా?",
}

#: The languages a sample is generated in. Kept to two deliberately: the point
#: is to hear the voice, and a picker with seven voices times ten languages is
#: the reading problem this was meant to solve.
SAMPLE_LANGUAGES = ("en", "hi", "ta", "kn", "te")


def sample_path(voice_id: str, language: str, ext: str = "wav") -> str:
    """Storage key for one voice in one language. Derived, never stored."""
    return f"{SAMPLE_PREFIX}/{voice_id.strip().lower()}-{language}.{ext}"


async def sample_url(voice_id: str, language: str = "en") -> str | None:
    """A URL the browser can play, or ``None`` when there is no sample yet.

    Returning ``None`` rather than a URL that 404s keeps the decision in one
    place: the picker renders a play button only for voices it can actually
    play, instead of offering one that fails when clicked.
    """
    if language not in SAMPLE_LANGUAGES:
        return None

    storage = get_storage()
    # WAV from Sarvam, MP3 from ElevenLabs; the browser plays either.
    for ext in ("wav", "mp3"):
        path = sample_path(voice_id, language, ext)
        try:
            if await storage.aget_file_metadata(path) is None:
                continue
            return await storage.aget_signed_url(path)
        except Exception:
            # Storage being unreachable must not take the model picker down
            # with it. No sample is a worse picker; an exception is no picker.
            return None
    return None


async def _vendor_key(provider: str) -> str | None:
    """The platform's own key for this vendor, or None.

    The customer's key is deliberately not used. A sample is a product asset
    shown to everyone -- it belongs on our key and our bill, not on the bill
    of whichever account happened to click play first.
    """
    from api.db import db_client
    from api.enums import CostComponent
    from api.services.configuration import platform_credentials

    try:
        async with db_client.async_session() as session:
            return await platform_credentials.resolve_api_key(
                session, component=CostComponent.TTS, provider=provider
            )
    except Exception as exc:  # noqa: BLE001 - no sample beats no picker
        logger.warning("Could not read the platform {} key: {}", provider, exc)
        return None


async def ensure_sample_url(
    *, provider: str, model: str, voice_id: str, language: str = "en"
) -> str | None:
    """A URL for this voice, recording it first if nobody has yet.

    Returns ``None`` rather than raising for every reason a sample might not
    exist -- an unsupported language, a model with no speakers, no platform
    key, a vendor that is down. The caller is a play button, and the honest
    answer to "can I hear this" is sometimes no.

    Two people pressing play on the same unrecorded voice at once will both
    record it and both write the same bytes to the same key. That is a wasted
    vendor call, not a correctness problem, and locking against it would cost
    more than the call does.
    """
    from api.services.configuration import voice_synthesis

    cached = await sample_url(voice_id, language)
    if cached:
        return cached

    if language not in SAMPLE_LANGUAGES:
        return None

    key = await _vendor_key(provider)
    if not key:
        logger.info(
            "No platform {} key, so {} cannot be recorded yet", provider, voice_id
        )
        return None

    try:
        audio = await voice_synthesis.synthesise(
            provider=provider,
            model=model,
            voice=voice_id,
            language=language,
            api_key=key,
        )
    except voice_synthesis.UnsupportedVoice as exc:
        logger.debug("No sample for {} {}: {}", provider, voice_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a silent play button beats a 500
        logger.warning("Could not record {} {}: {}", provider, voice_id, exc)
        return None

    path = sample_path(voice_id, language, voice_synthesis.extension_for(provider))
    try:
        storage = get_storage()
        await storage.acreate_file_from_bytes(path, audio)
        logger.info("Recorded voice sample {}", path)
        return await storage.aget_signed_url(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recorded {} but could not store it: {}", voice_id, exc)
        return None

"""Translate a managed language code into the vendor's own vocabulary.

A managed section names a language in Decibyl's vocabulary, which is
Deepgram-shaped: ``multi`` for auto-detect, otherwise a bare ISO code like
``en``, ``hi``, ``te``. That vocabulary is customer-facing and must not change
when a tier moves from one vendor to another — the whole point of a tier is
that the vendor behind it can be swapped without touching agents.

Sarvam speaks a different dialect: ``unknown`` for auto-detect and region-tagged
codes like ``en-IN``, ``hi-IN``, ``te-IN``. Until this existed, the Decibyl code
travelled straight through :func:`managed_resolution.apply` into the Sarvam
section, which produced two failures:

- **Speech to text refused the call outright**, with
  ``Language 'multi' is not supported by saarika:v2.5 model``. The greeting
  played, the caller spoke, and nothing was ever transcribed.
- **Text to speech failed silently and spoke Hindi.** The service factory maps
  Sarvam codes to pipecat's ``Language`` enum with ``Language.HI`` as the
  fallback, so an untranslated code did not raise — it just answered every
  caller in Hindi, which is worse, because nothing in the logs says so.

An unsupported language degrades to auto-detect rather than raising. A caller
is already on the line by the time this matters, and a call that transcribes in
the wrong language is recoverable where a call that drops is not.
"""

from __future__ import annotations

from api.services.configuration.options.sarvam import (
    SARVAM_STT_LANGUAGES_V3,
    SARVAM_STT_LANGUAGES_V25,
)

#: What Decibyl calls auto-detect.
DECIBYL_AUTODETECT = "multi"

#: What Sarvam calls auto-detect, on speech to text.
SARVAM_AUTODETECT = "unknown"

#: Sarvam has no auto-detect for synthesis — it must be told what to speak. A
#: caller whose language we have not pinned down gets Indian English, which is
#: what the default greeting is written in. Hindi was the previous behaviour and
#: it was an accident of a dict lookup rather than a decision.
SARVAM_TTS_FALLBACK = "en-IN"

#: Bare ISO code to Sarvam's region-tagged code. Odia is the one that is not a
#: simple suffix: ISO 639-1 says ``or``, Sarvam says ``od-IN``.
_ISO_TO_SARVAM = {
    "as": "as-IN",
    "bn": "bn-IN",
    "brx": "brx-IN",
    "doi": "doi-IN",
    "en": "en-IN",
    "gu": "gu-IN",
    "hi": "hi-IN",
    "kn": "kn-IN",
    "kok": "kok-IN",
    "ks": "ks-IN",
    "mai": "mai-IN",
    "ml": "ml-IN",
    "mni": "mni-IN",
    "mr": "mr-IN",
    "ne": "ne-IN",
    "or": "od-IN",
    "pa": "pa-IN",
    "sa": "sa-IN",
    "sat": "sat-IN",
    "sd": "sd-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "ur": "ur-IN",
}


def _sarvam_supported(model: str | None) -> frozenset[str]:
    """Which codes the chosen Sarvam model accepts.

    ``saaras:v3`` adds regional languages on top of the ``saarika:v2.5`` set, so
    a language that is fine on one model is refused by the other. Defaults to
    the narrower set: offering a language the model rejects is the failure this
    module exists to prevent.
    """
    if (model or "").strip().lower().startswith("saaras"):
        return frozenset(SARVAM_STT_LANGUAGES_V3)
    return frozenset(SARVAM_STT_LANGUAGES_V25)


def _to_sarvam_code(language: str) -> str | None:
    """``en`` → ``en-IN``; ``en-US`` → ``en-IN``; ``te-IN`` → ``te-IN``."""
    value = (language or "").strip()
    if not value:
        return None

    # Already region-tagged the way Sarvam wants it.
    if value in _ISO_TO_SARVAM.values():
        return value

    base = value.replace("_", "-").split("-")[0].lower()
    return _ISO_TO_SARVAM.get(base)


def for_stt(provider: str, model: str | None, language: str | None) -> str | None:
    """The language to send to a resolved speech-to-text provider.

    Returns the value unchanged for any provider that already speaks Decibyl's
    vocabulary, so adding a vendor here is opt-in rather than something every
    caller has to remember.
    """
    if (provider or "").strip().lower() != "sarvam":
        return language

    value = (language or "").strip().lower()
    if not value or value == DECIBYL_AUTODETECT:
        return SARVAM_AUTODETECT

    code = _to_sarvam_code(value)
    if code is None or code not in _sarvam_supported(model):
        # Auto-detect beats refusing the call. Sarvam is good at it, and the
        # alternative is a live caller hearing silence.
        return SARVAM_AUTODETECT
    return code


def for_tts(provider: str, model: str | None, language: str | None) -> str | None:
    """The language to send to a resolved text-to-speech provider.

    Unlike transcription there is no auto-detect: synthesis has to pick a
    language to speak in. ``multi`` and anything unrecognised become Indian
    English rather than defaulting to a specific regional language nobody chose.
    """
    if (provider or "").strip().lower() != "sarvam":
        return language

    value = (language or "").strip().lower()
    if not value or value == DECIBYL_AUTODETECT:
        return SARVAM_TTS_FALLBACK

    code = _to_sarvam_code(value)
    if code is None or code == SARVAM_AUTODETECT:
        return SARVAM_TTS_FALLBACK
    return code

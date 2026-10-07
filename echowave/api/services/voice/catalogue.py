"""Which languages live voice speaks, and whether a chosen voice can speak.

The person chooses their voice in Settings -> Voice and language (stream
`settings`), which saves the speaker's plain id for the workspace's voice
model (``member_preferences.voice``, e.g. ``kavya``). Live voice applies it
only when the session's voice model has that speaker and speaks the
session's language; otherwise the workspace voice speaks and the session
says the choice was not applied -- never a silent swap (screen 19).

Sarvam is the voice for Indian languages (LAUNCH-PLAN.md, founder decision).
The languages are the ones Sarvam's text-to-speech maps in
``services/pipecat/service_factory.py``; the speakers are the ones its
options list (``services/configuration/options/sarvam.py``).
"""

from __future__ import annotations

from api.services.configuration.options.sarvam import (
    SARVAM_V2_VOICES,
    SARVAM_V3_VOICES,
)

#: The languages Sarvam's text-to-speech maps (service_factory's TTS table).
#: A language outside it can still be typed and captioned, never spoken.
SPOKEN = frozenset(
    {
        "bn-IN",
        "en-IN",
        "gu-IN",
        "hi-IN",
        "kn-IN",
        "ml-IN",
        "mr-IN",
        "od-IN",
        "pa-IN",
        "ta-IN",
        "te-IN",
    }
)

#: Model -> its speakers. Every Bulbul speaker speaks every Bulbul language.
MODELS: dict[str, tuple[str, ...]] = {
    "bulbul:v3": SARVAM_V3_VOICES,
    "bulbul:v2": SARVAM_V2_VOICES,
}


def spoken_language(code: str | None) -> str | None:
    """The language Sarvam would speak for this preference, or None."""
    if not code:
        return None
    if code == "en":
        return "en-IN"
    return code if code in SPOKEN else None


def is_speaker(voice: str | None, model: str | None) -> bool:
    """Whether ``voice`` is one of ``model``'s speakers."""
    return bool(voice) and voice in MODELS.get(model or "", ())


def compatible(voice: str | None, language: str | None, model: str | None) -> bool:
    """Whether the session can speak ``language`` in ``voice`` on ``model``."""
    return spoken_language(language) is not None and is_speaker(voice, model)

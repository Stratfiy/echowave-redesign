"""Which languages live voice speaks, and which voices speak each (screen 19).

Built from what the Sarvam pipeline already lists (``services/configuration/
options/sarvam.py``) and what its text-to-speech maps
(``services/pipecat/service_factory.py``): Sarvam is the voice for Indian
languages (LAUNCH-PLAN.md, founder decision). Nothing here is invented: a
voice is a name Sarvam documents for a model, and its gender is not listed
because the catalogue does not say -- a guessed label would be a promise the
voice may not keep.

A voice id is ``sarvam:<model>:<speaker>``, which fits the member preference
column's pattern (``services/member_preferences._VOICE``).
"""

from __future__ import annotations

from dataclasses import dataclass

from api.services.configuration.options.sarvam import (
    SARVAM_V2_VOICES,
    SARVAM_V3_VOICES,
)

#: The member preference language tags (services/member_preferences.LANGUAGES)
#: by name, with whether Sarvam's voice can speak them. ``en`` (no region) is
#: spoken as Indian English, the one English voice Sarvam has.
LANGUAGE_NAMES: dict[str, tuple[str, str]] = {
    "en-IN": ("English (India)", "English"),
    "en": ("English", "English"),
    "hi-IN": ("Hindi", "हिन्दी"),
    "bn-IN": ("Bengali", "বাংলা"),
    "ta-IN": ("Tamil", "தமிழ்"),
    "te-IN": ("Telugu", "తెలుగు"),
    "kn-IN": ("Kannada", "ಕನ್ನಡ"),
    "ml-IN": ("Malayalam", "മലയാളം"),
    "mr-IN": ("Marathi", "मराठी"),
    "gu-IN": ("Gujarati", "ગુજરાતી"),
    "pa-IN": ("Punjabi", "ਪੰਜਾਬੀ"),
    "od-IN": ("Odia", "ଓଡ଼ିଆ"),
}

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

#: Model -> its speakers, newest first: the order the screen offers them.
MODELS: dict[str, tuple[str, ...]] = {
    "bulbul:v3": SARVAM_V3_VOICES,
    "bulbul:v2": SARVAM_V2_VOICES,
}

DEFAULT_MODEL = "bulbul:v3"
SPEED_MIN = 0.5
SPEED_MAX = 2.0


@dataclass(frozen=True)
class Voice:
    id: str
    provider: str
    model: str
    speaker: str

    @property
    def label(self) -> str:
        return self.speaker.capitalize()


def spoken_language(code: str | None) -> str | None:
    """The language Sarvam would speak for this preference, or None."""
    if not code:
        return None
    if code == "en":
        return "en-IN"
    return code if code in SPOKEN else None


def voices_for(language: str | None) -> list[Voice]:
    """The voices that speak ``language``; empty when none does.

    Every Bulbul speaker speaks every Bulbul language, so the filter is the
    language itself: a language Sarvam cannot speak has no voices, and the
    screen says so rather than substituting one (screen 19).
    """
    if spoken_language(language) is None:
        return []
    return [
        Voice(
            id=f"sarvam:{model}:{speaker}",
            provider="sarvam",
            model=model,
            speaker=speaker,
        )
        for model, speakers in MODELS.items()
        for speaker in speakers
    ]


def parse(voice_id: str | None) -> Voice | None:
    """The voice an id names, or None for an id the catalogue does not hold."""
    if not voice_id:
        return None
    parts = voice_id.split(":")
    if len(parts) != 4 or parts[0] != "sarvam":
        return None
    model = f"{parts[1]}:{parts[2]}"
    speaker = parts[3]
    if speaker not in MODELS.get(model, ()):
        return None
    return Voice(id=voice_id, provider="sarvam", model=model, speaker=speaker)


def compatible(voice_id: str | None, language: str | None) -> bool:
    voice = parse(voice_id)
    return voice is not None and any(v.id == voice.id for v in voices_for(language))


def as_dict() -> dict:
    """The whole catalogue for the settings screen."""
    languages = []
    for code, (english, native) in LANGUAGE_NAMES.items():
        voices = voices_for(code)
        languages.append(
            {
                "code": code,
                "english": english,
                "native": native,
                "spoken": bool(voices),
                "voices": [
                    {"id": v.id, "label": v.label, "model": v.model} for v in voices
                ],
            }
        )
    return {"languages": languages, "speed_min": SPEED_MIN, "speed_max": SPEED_MAX}

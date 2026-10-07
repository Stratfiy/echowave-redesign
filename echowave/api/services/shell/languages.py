"""The languages a person can choose at the door (screen 02).

Each is listed by its own name first, then in English, so a person who reads
Tamil finds "தமிழ்" rather than hunting for "Tamil". ``voice`` says whether
live voice and dictation can run in it today (Sarvam's documented speech
languages); a language without voice is still a full text language, and the
screen says so rather than hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    native: str
    english: str
    voice: bool


LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", "English", True),
    Language("hi", "हिन्दी", "Hindi", True),
    Language("ta", "தமிழ்", "Tamil", True),
    Language("te", "తెలుగు", "Telugu", True),
    Language("kn", "ಕನ್ನಡ", "Kannada", True),
    Language("ml", "മലയാളം", "Malayalam", True),
    Language("mr", "मराठी", "Marathi", True),
    Language("bn", "বাংলা", "Bengali", True),
    Language("gu", "ગુજરાતી", "Gujarati", True),
    Language("pa", "ਪੰਜਾਬੀ", "Punjabi", True),
    Language("od", "ଓଡ଼ିଆ", "Odia", True),
    Language("ur", "اردو", "Urdu", False),
    Language("as", "অসমীয়া", "Assamese", False),
)

BY_CODE: dict[str, Language] = {language.code: language for language in LANGUAGES}


def is_supported(code: str | None) -> bool:
    return bool(code) and code in BY_CODE


def as_dicts() -> list[dict]:
    return [
        {
            "code": language.code,
            "native": language.native,
            "english": language.english,
            "voice": language.voice,
        }
        for language in LANGUAGES
    ]

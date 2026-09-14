"""Translate and transliterate, on the platform's Sarvam key.

Offered where somebody needs it, never as a tool they configure: a message on
a thread written in an Indian script gets a "Translate" action, and a draft
typed in an Indian script gets "Translate to English" and "Roman script"
under the composer. Sarvam's translate takes 1000 characters a request, so
longer text is cut at sentence ends and sent as several; the joins are what
you would get pasting the pieces back together.

Not metered yet. Sarvam bills Rs20 per 10,000 characters and the platform
key pays it; a message is a few hundred characters. When translation is a
line on a receipt it will be a ``translation`` cost component, priced per
thousand characters like TTS -- see PRICING-DECISIONS.md.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx
from loguru import logger

from api.db import db_client
from api.enums import CostComponent
from api.services.configuration import platform_credentials

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
SARVAM_TRANSLITERATE_URL = "https://api.sarvam.ai/transliterate"
PROVIDER = "sarvam"
TRANSLATE_MODEL = "mayura:v1"
#: Sarvam's limit on mayura:v1. Longer text is split.
CHUNK_CHARS = 1000
#: What one request to us may carry. A whole document is a different job.
MAX_INPUT_CHARS = 8000
ENGLISH = "en-IN"

LANGUAGES = {
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

#: The Indic script blocks, as one class. Enough to say "this is written in an
#: Indian language" without guessing which; Sarvam's `auto` does the guessing.
_INDIC = re.compile("[ऀ-ॿঀ-৿਀-੿઀-૿଀-୿஀-௿ఀ-౿ಀ-೿ഀ-ൿ]")


def is_indic(text: str) -> bool:
    """Whether the text carries any Indian-script letters."""
    return bool(_INDIC.search(text or ""))


class TranslationUnavailable(RuntimeError):
    """No platform key for the vendor; the screen says so rather than hanging."""


def chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Pieces no longer than ``limit``, cut at sentence ends where possible."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(
            window.rfind(". "),
            window.rfind("। "),
            window.rfind("\n"),
            window.rfind(" "),
        )
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        out.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


async def _key() -> str:
    async with db_client.async_session() as session:
        key = await platform_credentials.resolve_api_key(
            session, component=CostComponent.TTS, provider=PROVIDER
        )
    if not key:
        raise TranslationUnavailable("No Sarvam platform key is stored.")
    return key


async def _post(
    url: str, key: str, body: dict, client: Optional[httpx.AsyncClient]
) -> dict:
    headers = {"api-subscription-key": key}
    if client is not None:
        response = await client.post(url, json=body, headers=headers, timeout=20.0)
    else:
        async with httpx.AsyncClient() as own:
            response = await own.post(url, json=body, headers=headers, timeout=20.0)
    response.raise_for_status()
    return response.json()


async def translate(
    text: str,
    *,
    target: str = ENGLISH,
    source: str = "auto",
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[str, Optional[str]]:
    """The text in ``target``, and the language Sarvam thought it was in."""
    if target not in LANGUAGES:
        raise ValueError(f"Unknown language {target!r}")
    key = await _key()
    pieces: list[str] = []
    detected: Optional[str] = None
    for piece in chunks(text):
        data = await _post(
            SARVAM_TRANSLATE_URL,
            key,
            {
                "input": piece,
                "source_language_code": source,
                "target_language_code": target,
                "model": TRANSLATE_MODEL,
                "mode": "formal",
            },
            client,
        )
        pieces.append(str(data.get("translated_text") or ""))
        detected = detected or data.get("source_language_code")
    logger.info("Translated {} characters to {}", len(text), target)
    return " ".join(p for p in pieces if p).strip(), detected


async def transliterate(
    text: str,
    *,
    target: str = ENGLISH,
    source: str = "auto",
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[str, Optional[str]]:
    """The same words in ``target``'s script -- Roman for en-IN."""
    if target not in LANGUAGES:
        raise ValueError(f"Unknown language {target!r}")
    key = await _key()
    pieces: list[str] = []
    detected: Optional[str] = None
    for piece in chunks(text):
        data = await _post(
            SARVAM_TRANSLITERATE_URL,
            key,
            {
                "input": piece,
                "source_language_code": source,
                "target_language_code": target,
                "numerals_format": "international",
            },
            client,
        )
        pieces.append(str(data.get("transliterated_text") or ""))
        detected = detected or data.get("source_language_code")
    return " ".join(p for p in pieces if p).strip(), detected

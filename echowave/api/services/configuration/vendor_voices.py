"""The voices an account actually has, asked of the vendor that holds them.

``voice_catalogue`` serves what is knowable locally, which is most providers:
their voices are a fixed list that ships with the code. ElevenLabs and Cartesia
are not, because both let a customer clone voices into their own workspace. So
those two return empty with a reason, and the picker says "paste a voice ID".

Pasting an id is not a product. A customer on a managed tier has chosen not to
hold a key -- that is what managed means -- so telling them to open the
vendor's dashboard and copy one asks them to do the thing they paid us to
avoid.

This asks the vendor instead, with the platform key. What a managed customer
sees is the platform's library, which is the library their calls will really
use: the same key synthesises the audio, so a voice listed here is a voice that
will work.

**Cached, because a config screen is not a place for a network call.** Somebody
auditioning voices clicks every one, twice, and again after changing the
language; the answer does not change between clicks. A short TTL keeps a newly
cloned voice from being invisible for long without making the picker wait on a
third party.

**Failure is never a blank picker.** Any error -- no key, a timeout, a shape we
do not recognise -- falls back to the local catalogue's reason, so the screen
degrades to what it says today rather than to nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from loguru import logger

from api.enums import CostComponent
from api.services.configuration import platform_credentials
from api.services.configuration.registry import ServiceProviders

#: How long a fetched list is reused. Long enough that auditioning voices costs
#: one request, short enough that a voice cloned during setup appears without
#: anyone restarting anything.
_TTL_SECONDS = 600

#: Beyond this the picker is worth more empty than late: the caller is waiting
#: on a screen, and the fallback message is a usable answer.
_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class VendorVoice:
    voice_id: str
    name: str
    gender: str | None = None
    accent: str | None = None
    language: str | None = None
    description: str | None = None
    #: The vendor's own hosted sample. Passed through rather than re-recorded:
    #: they already host one per voice, and our sample pipeline exists for
    #: providers that publish none.
    preview_url: str | None = None


#: provider -> (expires_at, voices). One platform key per provider, so the
#: provider name is the whole cache key.
_CACHE: dict[str, tuple[float, list[VendorVoice]]] = {}


def _cached(provider: str) -> list[VendorVoice] | None:
    hit = _CACHE.get(provider)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def clear_cache() -> None:
    """Drop everything cached. For tests, and for a key being replaced."""
    _CACHE.clear()


def _elevenlabs_voices(payload: dict) -> list[VendorVoice]:
    """Map ElevenLabs' /v1/voices body to what the picker needs.

    Their ``labels`` is a free-form dict a voice's author fills in, so every
    field read from it is optional by nature rather than by caution.
    """
    voices: list[VendorVoice] = []
    for raw in payload.get("voices") or []:
        voice_id = raw.get("voice_id")
        if not voice_id:
            continue
        labels = raw.get("labels") or {}
        voices.append(
            VendorVoice(
                voice_id=voice_id,
                name=raw.get("name") or voice_id,
                gender=labels.get("gender"),
                accent=labels.get("accent"),
                language=labels.get("language"),
                description=raw.get("description") or labels.get("description"),
                preview_url=raw.get("preview_url"),
            )
        )
    return voices


async def _fetch_elevenlabs(api_key: str) -> list[VendorVoice]:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
        )
        response.raise_for_status()
        return _elevenlabs_voices(response.json())


#: Providers this module can ask, and the component whose platform key to ask
#: with. Adding Cartesia is a fetcher and a row.
_FETCHERS = {
    ServiceProviders.ELEVENLABS.value: (CostComponent.TTS, _fetch_elevenlabs),
}


def can_fetch(provider: str) -> bool:
    """Whether asking the vendor is even possible for this provider."""
    return provider in _FETCHERS


async def fetch(provider: str) -> list[VendorVoice] | None:
    """The provider's voices on the platform key, or None if we cannot get them.

    None rather than an empty list on failure: an account with no voices and an
    account we could not ask are different things, and the caller shows a
    different screen for each.
    """
    entry = _FETCHERS.get(provider)
    if entry is None:
        return None

    cached = _cached(provider)
    if cached is not None:
        return cached

    component, fetcher = entry
    try:
        # Imported here rather than at module scope: db_client pulls in the
        # engine, and this module is imported by a route that must load whether
        # or not a database is reachable.
        from api.db import db_client

        async with db_client.async_session() as session:
            api_key = await platform_credentials.resolve_api_key(
                session, component=component, provider=provider
            )
    except Exception as exc:
        logger.warning("Could not read the platform {} key: {}", provider, exc)
        return None

    if not api_key:
        return None

    try:
        voices = await fetcher(api_key)
    except Exception as exc:
        logger.warning("Could not list {} voices: {}", provider, exc)
        return None

    _CACHE[provider] = (time.monotonic() + _TTL_SECONDS, voices)
    return voices

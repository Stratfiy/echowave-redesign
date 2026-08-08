"""Voices for the picker, served from what we already know.

The voice list used to come from an external managed service that no longer
exists, so every provider's picker returned "Failed to load voices" and TTS
could not be configured at all — which quietly blocked the whole product, since
an agent with no voice cannot place a call.

The registry already holds the voice catalogue for the providers whose voices
are a fixed list rather than an account-specific one. Serving from there removes
a network call from a config screen, works offline, and cannot fail because a
third party is down. It is also honest about its limits: ElevenLabs and Cartesia
let a customer clone voices into their own account, so their real catalogue is
only knowable by asking them with that customer's key. Those return empty with a
reason rather than a misleading partial list.

``decibyl`` resolves through the managed tier to whichever provider actually
serves it, so a managed customer sees the voices they will really get.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.enums import CostComponent
from api.services.configuration import managed_tiers
from api.services.configuration.options.google import GOOGLE_TTS_VOICES
from api.services.configuration.options.rumik import (
    RUMIK_FEMALE_VOICES,
    RUMIK_VOICES,
)
from api.services.configuration.options.sarvam import (
    SARVAM_V2_VOICES,
    SARVAM_V3_VOICES,
)
from api.services.configuration.options.smallest import (
    SMALLEST_TTS_PRO_VOICES,
    SMALLEST_TTS_VOICES,
)
from api.services.configuration.registry import ServiceProviders

#: Sarvam publishes gender with its voices; the picker filters on it, and
#: guessing from a name would be both wrong and rude.
_SARVAM_GENDER = {
    "anushka": "female",
    "manisha": "female",
    "vidya": "female",
    "arya": "female",
    "abhilash": "male",
    "karun": "male",
    "hitesh": "male",
}


@dataclass(frozen=True)
class Voice:
    voice_id: str
    name: str
    gender: str | None = None
    language: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Catalogue:
    provider: str
    voices: list[Voice]
    #: Set when we cannot know the catalogue locally. The picker shows this
    #: instead of an empty list, so "no voices" and "we cannot list them"
    #: do not look identical.
    unavailable_reason: str | None = None


def _sarvam(model: str | None) -> list[Voice]:
    """Bulbul's voices, which differ by model version.

    v3 is the larger set; v2 is what most accounts are on. Returning the wrong
    version's names produces a voice id the vendor rejects at call time, which
    is a confusing way to discover a config error.
    """
    names = SARVAM_V3_VOICES if (model or "").endswith("v3") else SARVAM_V2_VOICES
    return [
        Voice(
            voice_id=name,
            name=name.title(),
            gender=_SARVAM_GENDER.get(name),
            language="hi",  # Indic multi-speaker; the model switches per request
        )
        for name in names
    ]


def _rumik(model: str | None) -> list[Voice]:
    """Silk's preset studio voices.

    Muga takes no preset speaker — it is directed by tone tags in the text — so
    offering names there would produce a picker whose choices do nothing.

    Naming a voice Rumik does not recognise is not an error at their end: it
    generates a voice from the description instead. So an out-of-date list here
    does not fail loudly, it quietly gives the caller a stranger.
    """
    if (model or "").strip().lower() == "muga":
        return []
    return [
        Voice(
            voice_id=name,
            name=name.title(),
            gender="female" if name in RUMIK_FEMALE_VOICES else "male",
            language="hi",  # Hindi and English, code-mixed within one request
        )
        for name in RUMIK_VOICES
    ]


#: Providers whose catalogue is fixed and therefore knowable without asking.
_LOCAL = {
    ServiceProviders.SARVAM.value: _sarvam,
    ServiceProviders.RUMIK.value: _rumik,
    ServiceProviders.SMALLEST.value: lambda model: [
        Voice(voice_id=v, name=v.title())
        for v in (
            SMALLEST_TTS_PRO_VOICES if "pro" in (model or "") else SMALLEST_TTS_VOICES
        )
    ],
    ServiceProviders.GOOGLE.value: lambda _model: [
        Voice(voice_id=v, name=v) for v in GOOGLE_TTS_VOICES
    ],
}

#: Providers whose catalogue is per-account — cloned and custom voices live in
#: the customer's own workspace, so only they can enumerate it.
_ACCOUNT_SPECIFIC = {
    ServiceProviders.ELEVENLABS.value: (
        "ElevenLabs voices are specific to your account, including any you have "
        "cloned. Paste a voice ID from your ElevenLabs dashboard."
    ),
    ServiceProviders.CARTESIA.value: (
        "Cartesia voices are specific to your account. Paste a voice ID from "
        "your Cartesia dashboard."
    ),
}


def for_provider(provider: str, *, model: str | None = None) -> Catalogue:
    """Voices for a provider, resolving the managed tier where necessary."""
    resolved = provider
    resolved_model = model

    if provider == ServiceProviders.DECIBYL.value:
        # A managed customer should see the voices they will actually get, not
        # a list belonging to whichever vendor they imagine is behind the tier.
        upstream = managed_tiers.resolve(CostComponent.TTS, model)
        resolved, resolved_model = upstream.provider, upstream.model

    if resolved in _LOCAL:
        return Catalogue(provider=resolved, voices=_LOCAL[resolved](resolved_model))

    if resolved in _ACCOUNT_SPECIFIC:
        return Catalogue(
            provider=resolved, voices=[], unavailable_reason=_ACCOUNT_SPECIFIC[resolved]
        )

    return Catalogue(
        provider=resolved,
        voices=[],
        unavailable_reason=(
            f"No voice catalogue is published for {resolved}. Enter a voice ID "
            "from that provider's dashboard."
        ),
    )


def filtered(
    provider: str,
    *,
    model: str | None = None,
    q: str | None = None,
    gender: str | None = None,
) -> Catalogue:
    """The catalogue narrowed by the picker's controls.

    Filtering here rather than in the route keeps the route thin and means the
    facets below are computed from the same list the caller sees.
    """
    catalogue = for_provider(provider, model=model)
    voices = catalogue.voices

    if gender:
        wanted = gender.lower()
        voices = [v for v in voices if (v.gender or "").lower() == wanted]
    if q:
        needle = q.lower()
        voices = [
            v
            for v in voices
            if needle in v.name.lower() or needle in v.voice_id.lower()
        ]

    return Catalogue(
        provider=catalogue.provider,
        voices=voices,
        unavailable_reason=catalogue.unavailable_reason,
    )


def facets(provider: str, *, model: str | None = None) -> dict[str, list[str]]:
    """Distinct selector values across the *unfiltered* catalogue.

    Unfiltered deliberately: a picker whose gender dropdown only lists the
    genders surviving the current filter cannot be used to change that filter.
    """
    voices = for_provider(provider, model=model).voices
    return {
        "genders": sorted({v.gender for v in voices if v.gender}),
        "accents": [],
        "languages": sorted({v.language for v in voices if v.language}),
    }

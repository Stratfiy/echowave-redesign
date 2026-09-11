"""Voices for the picker, served from what we already know.

The voice list used to come from an external managed service that no longer
exists, so every provider's picker returned "Failed to load voices" and TTS
could not be configured at all — which quietly blocked the whole product, since
an agent with no voice cannot place a call.

The registry already holds the voice catalogue for the providers whose voices
are a fixed list rather than an account-specific one. Serving from there removes
a network call from a config screen, works offline, and cannot fail because a
third party is down. It is also honest about its limits: Cartesia lets a
customer clone voices into their own account, so its real catalogue is only
knowable by asking with that customer's key, and it returns empty with a reason
rather than a misleading partial list. ElevenLabs is the same for *cloned*
voices, but its premade voices share one public id across every account, so
those six are listed -- they are what the managed "global" voice tier speaks
in, and a tier with no voices to pick from is a picker with nothing to play.

``decibyl`` resolves through the managed tier to whichever provider actually
serves it, so a managed customer sees the voices they will really get.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.enums import CostComponent
from api.services.configuration import managed_tiers
from api.services.configuration.options.deepgram import DEEPGRAM_AURA_VOICES
from api.services.configuration.options.elevenlabs import ELEVENLABS_PREMADE_VOICES
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
    # bulbul:v2
    "anushka": "female",
    "manisha": "female",
    "vidya": "female",
    "arya": "female",
    "abhilash": "male",
    "karun": "male",
    "hitesh": "male",
    # bulbul:v3. Covers every name in SARVAM_V3_VOICES — a v3 voice missing
    # here reads as gender-unknown and disappears from the picker's filter,
    # which is how the whole v3 set became unfilterable when the managed tier
    # moved off v2.
    "ritu": "female",
    "priya": "female",
    "neha": "female",
    "pooja": "female",
    "simran": "female",
    "kavya": "female",
    "ishita": "female",
    "shreya": "female",
    "roopa": "female",
    "amelia": "female",
    "sophia": "female",
    "tanya": "female",
    "shruti": "female",
    "suhani": "female",
    "kavitha": "female",
    "rupali": "female",
    "shubh": "male",
    "aditya": "male",
    "rahul": "male",
    "rohan": "male",
    "amit": "male",
    "dev": "male",
    "ratan": "male",
    "varun": "male",
    "manan": "male",
    "sumit": "male",
    "kabir": "male",
    "aayan": "male",
    "ashutosh": "male",
    "advait": "male",
    "anand": "male",
    "tarun": "male",
    "sunny": "male",
    "mani": "male",
    "gokul": "male",
    "vijay": "male",
    "mohit": "male",
    "rehan": "male",
    "soham": "male",
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


def _elevenlabs(_model: str | None) -> list[Voice]:
    """ElevenLabs' premade voices -- public ids, the same on every account.

    A customer's own cloned voices are not here and cannot be: they live in
    that customer's workspace. The registry keeps the field open to custom
    input for exactly that case.
    """
    return [
        Voice(voice_id=voice_id, name=name, gender=gender, language="en")
        for voice_id, name, gender in ELEVENLABS_PREMADE_VOICES
    ]


#: Providers whose catalogue is fixed and therefore knowable without asking.
def _deepgram(_model: str | None) -> list[Voice]:
    """Aura-2's speakers.

    The voice *is* the model here: ask for ``aura-2-thalia-en`` and Deepgram
    reads the ``aura-2`` off the front of it. So the model argument decides
    nothing, and every voice in the list belongs to the only model on sale.

    English only, deliberately. Aura-2 also speaks Spanish, Dutch, French,
    German, Italian and Japanese, and none of those is a language this product
    serves; it speaks no Indian language at all. Listing what it cannot say
    would put a picker in front of an operator building a Hindi line and let
    them choose a voice that will read Hindi with an American accent.
    """
    return [
        Voice(voice_id=voice_id, name=name, gender=gender, language="en")
        for voice_id, name, gender in DEEPGRAM_AURA_VOICES
    ]


_LOCAL = {
    ServiceProviders.DEEPGRAM.value: _deepgram,
    ServiceProviders.SARVAM.value: _sarvam,
    ServiceProviders.RUMIK.value: _rumik,
    ServiceProviders.ELEVENLABS.value: _elevenlabs,
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


def default_voice_id(
    provider: str,
    *,
    model: str | None = None,
    gender: str,
    language: str | None = None,
) -> str | None:
    """A speaker of ``gender`` from whatever this provider currently offers.

    Resolved at pipeline build rather than stored, so a managed agent that
    asked for a male voice keeps having one after the tier moves to another
    vendor — where the stored name would have become a voice id that vendor
    rejects.

    ``language`` is a preference, not a filter, and today it changes nothing:
    both catalogues published here are genuinely multilingual — one Bulbul
    speaker serves every Indic language and the model switches per request, so
    the language on a Voice is a label rather than a constraint. The parameter
    exists because that stops being true the moment a language-keyed vendor is
    added, and a caller that already passes the language will get the better
    answer for free rather than needing to be found and changed.

    Returns None when the provider publishes no catalogue, or none of its
    voices carry this gender — in which case the caller keeps whatever default
    the vendor would have applied, which is better than a guess.
    """
    voices = filtered(provider, model=model, gender=gender).voices
    if not voices:
        return None

    if language:
        primary = language.split("-")[0].lower()
        matching = [v for v in voices if (v.language or "").lower() == primary]
        if matching:
            return matching[0].voice_id

    return voices[0].voice_id

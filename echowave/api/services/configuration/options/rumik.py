"""Rumik Silk — Indic speech synthesis.

An Indian research lab's voice models, and the cheapest synthesis on the rate
card by a wide margin: Mulberry is a third of Sarvam Bulbul per character, on
the single largest line of a call's provider cost. That is the reason it is
here.

Two models, and they are not interchangeable:

- **Mulberry** is the fast one. Preset studio voices, or a natural-language
  description that generates a voice. Use it for calls.
- **Muga** is the expressive one, with tone tags like ``[happy]`` and inline
  events like ``<laugh>``. Twice the price and slower; worth it when the
  performance matters more than the latency.

**Hindi and English only**, including code-mixed within one request. This is a
narrower language range than Sarvam, which covers eleven Indian languages — so
Rumik is a cost win for Hindi/English agents and unusable for a Telugu one. The
picker must not present them as equivalent.
"""

RUMIK_GATEWAY_URL = "https://silk-api.rumik.ai"

#: ``mulberry`` first: it is the one that belongs on a phone call.
RUMIK_TTS_MODELS = ("mulberry", "muga")

#: Preset studio voices for Mulberry. An unrecognised name is **not** an error
#: at Rumik — it silently generates a voice from the description instead, so a
#: typo produces a stranger's voice rather than a failure. Hence a fixed list
#: rather than free text.
RUMIK_FEMALE_VOICES = (
    "emma",
    "mia",
    "sophia",
    "ava",
    "ira",
    "siya",
    "aisha",
    "zoya",
)
RUMIK_MALE_VOICES = (
    "lucas",
    "noah",
    "theo",
    "adam",
)
RUMIK_VOICES = RUMIK_FEMALE_VOICES + RUMIK_MALE_VOICES

#: What Silk actually supports. Deliberately short — see the module docstring.
RUMIK_LANGUAGES = ("hi-IN", "en-IN")

#: Sent alongside the voice to shape it. Rumik requires a description even when
#: a preset voice is named, so there is a sensible default rather than an empty
#: string that would make every agent sound like the model's neutral prior.
RUMIK_DEFAULT_DESCRIPTION = (
    "a warm Indian voice, natural conversational pacing, clear diction"
)

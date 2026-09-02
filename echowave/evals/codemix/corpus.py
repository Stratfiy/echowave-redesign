"""Seed corpus for code-mixed Indian speech.

Utterances a real caller would say, not sentences written to be transcribed.
Every line here is the shape of an actual call this platform takes: an order
status, a clinic slot, a payment date, a delivery address.

Two mixing patterns, and they behave differently under measurement:

* **Hinglish** is usually written in Devanagari for the Hindi half, so script
  detection finds the tokens that must survive on its own.
* **Tanglish** — and romanised Hindi, which is at least as common in typed
  contexts — carries no script signal at all. "enna panra" is Latin
  characters. So those entries name ``must_survive`` explicitly, or the recall
  metric would read a perfect 1.0 on a transcript that lost every Tamil word.

``audio`` is the recording of somebody actually saying the line. It is
deliberately optional and mostly absent: the honest version of this corpus is
recorded over a telephone at 8kHz by people who speak these languages, and
until it exists this file is the manifest for what to record rather than a
result anyone should quote.

**On synthesising the audio instead.** It is tempting to generate these with
our own TTS and feed them to STT. That measures something, but not the thing:
TTS audio is clean, evenly paced, free of the codec artefacts, background
noise and disfluency that make a real Indian mobile call hard. A provider can
score well on synthetic Hinglish and lose half of it on a live call. Use
synthesis to smoke-test the harness; never to make a claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Utterance:
    """One line of the corpus.

    Attributes:
        id: Stable handle, so a result can be traced to a line after the
            corpus grows.
        language: The mixing pattern, not a locale — what is being tested is
            the switch itself.
        text: What was said, as a person would write it.
        must_survive: Tokens whose loss breaks the call. Required wherever the
            non-English half is romanised; left empty for Devanagari, where
            script detection covers it.
        vertical: Which kind of customer this line belongs to, so a run can be
            filtered to the deployment being judged.
        audio: Path to a recording, relative to this directory. Absent until
            somebody records it.
    """

    id: str
    language: str
    text: str
    vertical: str
    must_survive: tuple[str, ...] = field(default_factory=tuple)
    audio: str | None = None


CORPUS: tuple[Utterance, ...] = (
    # --- Hinglish, Devanagari for the Hindi half ------------------------------
    Utterance(
        id="hi-order-status",
        language="hinglish",
        text="मेरा order kahan hai, delivery date confirm karo",
        vertical="d2c",
    ),
    Utterance(
        id="hi-appointment",
        language="hinglish",
        text="doctor se appointment chahiye kal subah के लिए",
        vertical="clinic",
    ),
    Utterance(
        id="hi-payment-date",
        language="hinglish",
        text="EMI का payment मैं agle mahine kar dunga",
        vertical="lending",
    ),
    Utterance(
        id="hi-address-change",
        language="hinglish",
        text="address change karna hai, नया pincode 560001 है",
        vertical="logistics",
    ),
    Utterance(
        id="hi-callback",
        language="hinglish",
        text="अभी busy हूँ, shaam को call back karna",
        vertical="general",
    ),
    # --- Tanglish, romanised — script detection finds nothing here -------------
    Utterance(
        id="ta-order-status",
        language="tanglish",
        text="enna aachu my order, innum delivery aagala",
        vertical="d2c",
        must_survive=("enna", "aachu", "innum", "aagala"),
    ),
    Utterance(
        id="ta-appointment",
        language="tanglish",
        text="doctor ah paakanum, naalaikku morning slot irukka",
        vertical="clinic",
        must_survive=("paakanum", "naalaikku", "irukka"),
    ),
    Utterance(
        id="ta-price",
        language="tanglish",
        text="idhu evlo aagum, discount ethachum irukka",
        vertical="d2c",
        must_survive=("idhu", "evlo", "aagum", "ethachum", "irukka"),
    ),
    # --- Romanised Hindi, the same blind spot as Tanglish ----------------------
    Utterance(
        id="hi-roman-reschedule",
        language="hinglish-roman",
        text="mujhe appointment reschedule karna hai next week",
        vertical="clinic",
        must_survive=("mujhe", "karna", "hai"),
    ),
    Utterance(
        id="hi-roman-refuse",
        language="hinglish-roman",
        text="mujhe interest nahi hai, phone mat karo dobara",
        vertical="general",
        must_survive=("mujhe", "nahi", "hai", "mat", "karo", "dobara"),
    ),
    # --- Monolingual controls -------------------------------------------------
    # Without these a bad run is unreadable: a provider that scores poorly on
    # everything has a general problem, not a code-switching one, and telling
    # those apart is the point of a control.
    Utterance(
        id="en-control-order",
        language="english",
        text="I want to check the status of my order please",
        vertical="d2c",
    ),
    Utterance(
        id="hi-control-pure",
        language="hindi",
        text="मुझे अपना ऑर्डर देखना है",
        vertical="d2c",
    ),
)


def by_language(language: str) -> tuple[Utterance, ...]:
    return tuple(u for u in CORPUS if u.language == language)


def with_audio() -> tuple[Utterance, ...]:
    """The subset that can actually be run against a transcriber today."""
    return tuple(u for u in CORPUS if u.audio)

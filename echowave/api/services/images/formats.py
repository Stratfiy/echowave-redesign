"""The shapes a poster or an ad creative comes in, and each provider's size.

A person says "Instagram square" or "A4 poster", not 1080x1080. Each format
names the shape the channel wants and, per provider, the nearest size that
provider will actually make: Gemini takes an aspect ratio, OpenAI a size in
multiples of 16 within its pixel bounds, Nova Canvas a size in multiples of
16 under 4.2 megapixels. The image comes back at the provider's size; the
card says the shape, and every channel named here scales to it.

An unknown format is not dropped: it falls back to the square, and the
caller is told which format was used, so a typo produces a square poster
rather than nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    #: The size the channel publishes at, for the card.
    target: str
    #: Gemini's ``aspect_ratio``.
    aspect: str
    #: OpenAI ``size``: WxH, multiples of 16, 655,360-8,294,400 pixels.
    openai_size: str
    #: Nova Canvas width and height: multiples of 16, under 4,194,304 pixels.
    nova_size: tuple[int, int]


FORMATS: dict[str, Format] = {
    f.key: f
    for f in (
        Format(
            "instagram_square",
            "Instagram post (square)",
            "1080x1080",
            "1:1",
            "1024x1024",
            (1024, 1024),
        ),
        Format(
            "instagram_portrait",
            "Instagram post (portrait)",
            "1080x1350",
            "4:5",
            "1024x1280",
            (1024, 1280),
        ),
        Format(
            "story",
            "Instagram or Facebook story",
            "1080x1920",
            "9:16",
            "1008x1792",
            (720, 1280),
        ),
        Format(
            "whatsapp_status",
            "WhatsApp status",
            "1080x1920",
            "9:16",
            "1008x1792",
            (720, 1280),
        ),
        Format(
            "facebook_ad",
            "Facebook or Instagram feed ad (landscape)",
            "1200x628",
            "16:9",
            "1536x800",
            (1200, 624),
        ),
        Format(
            "a4_poster",
            "A4 poster (portrait)",
            "2480x3508",
            "2:3",
            "1024x1536",
            (1024, 1536),
        ),
        Format(
            "banner",
            "Wide banner",
            "1920x1080",
            "16:9",
            "1792x1008",
            (1280, 720),
        ),
    )
}

DEFAULT_FORMAT = "instagram_square"


def resolve(key: str | None) -> tuple[Format, bool]:
    """The format asked for, and whether it was known. Unknown is the square."""
    wanted = (key or "").strip().lower().replace("-", "_").replace(" ", "_")
    if wanted in FORMATS:
        return FORMATS[wanted], True
    return FORMATS[DEFAULT_FORMAT], False


def describe() -> str:
    """One line per format, for a tool description."""
    return "; ".join(f"{f.key} ({f.label}, {f.target})" for f in FORMATS.values())

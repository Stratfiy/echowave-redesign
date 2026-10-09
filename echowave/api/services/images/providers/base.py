"""What every image provider is, in one small interface.

Three vendors, three request shapes, one contract: given a prompt we wrote,
a format and any reference images, return image bytes or raise
``ImageProviderError`` with a sentence a person can act on. Never a retry:
a retried generation is a second charge on somebody's own key, and "it
failed, try again" is a decision for the person, not for this code.

The key is a parameter, never read here. ``None`` means "use the platform's
own credentials" -- which for Bedrock is the box's AWS role, and for the
others the platform key the caller already resolved.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.services.images.formats import Format


class ImageProviderError(Exception):
    """A provider said no, or could not be reached. ``kind`` is one of
    ``auth`` (the key was refused), ``refused`` (the request was declined,
    usually by the vendor's safety filter), ``quota`` (rate or spend limit),
    ``timeout``, ``bad_request`` and ``unavailable``. ``message`` is said to
    the person as is, so it never carries the key."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class InputImage:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    format: Format
    count: int = 1
    #: A logo or a product photo to work from.
    references: tuple[InputImage, ...] = ()
    #: The image being edited, when this is an edit.
    base: InputImage | None = None
    #: Things the image must not contain; Nova Canvas takes them apart.
    avoid: str = ""


@dataclass
class MadeImage:
    data: bytes
    mime_type: str
    width: int | None = None
    height: int | None = None


@dataclass
class ProviderResult:
    images: list[MadeImage]
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    #: What the vendor said when it made fewer images than were asked for
    #: (Nova drops images its safety filter blocks), for the person.
    note: str = ""


@dataclass(frozen=True)
class KeyCheck:
    """``verified``, ``rejected`` or ``unverified`` (could not ask)."""

    outcome: str
    message: str

    @property
    def may_store(self) -> bool:
        return self.outcome != "rejected"


class ImageProvider(Protocol):
    name: str
    label: str
    #: Whether a reference image (a logo, a product photo) can be sent.
    takes_references: bool
    #: Most options one request may ask for.
    max_count: int

    def model(self) -> str: ...

    async def generate(
        self, request: ImageRequest, *, api_key: str | None, own_key: bool = True
    ) -> ProviderResult: ...

    async def check_key(self, api_key: str) -> KeyCheck: ...


def image_size(data: bytes) -> tuple[int | None, int | None]:
    """Width and height read off a PNG, JPEG or WebP header, or (None, None).

    Read from the bytes rather than trusted from the request: a provider may
    return a different size from the one asked for, and the card should say
    what the file actually is.
    """
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    return int(width), int(height)
                length = struct.unpack(">H", data[index + 2 : index + 4])[0]
                index += 2 + length
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X" and len(data) >= 30:
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if chunk == b"VP8 " and len(data) >= 30:
                width, height = struct.unpack("<HH", data[26:30])
                return int(width & 0x3FFF), int(height & 0x3FFF)
            if chunk == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        pass
    return None, None


def sniff_mime(data: bytes, fallback: str = "image/png") -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def made(data: bytes, mime_type: str | None = None) -> MadeImage:
    mime = sniff_mime(data, mime_type or "image/png")
    width, height = image_size(data)
    return MadeImage(data=data, mime_type=mime, width=width, height=height)


def short(text: str, limit: int = 240) -> str:
    """A vendor's error text, trimmed for a sentence to a person."""
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")

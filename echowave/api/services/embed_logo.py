"""The customer's own logo on their embedded widget.

Gnani exposes an icon upload alongside its widget config and we had colour and
button text only, so every widget embedded on a customer's site looked like
ours rather than theirs.

Two decisions here are worth stating, because both are refusals.

**No SVG.** It is the obvious format for a logo and it is a scripting surface:
an SVG can carry ``<script>`` and event handlers. Rendered through ``<img>`` a
browser will not run them, so this is defence in depth rather than a live hole
— but the file is served from our origin to a customer's page, the next person
to render it may reach for CSS ``background`` or inline it, and by then the
decision is invisible. Raster only.

**The declared content type is not trusted.** ``Content-Type`` on a multipart
part is whatever the client typed. What is stored, and what is served back
later, comes from the file's own magic bytes; a mismatch is a rejection rather
than a correction, because a PNG announcing itself as a JPEG is not a mistake
anybody makes by accident.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# Keyed by the content type we will serve. The prefixes are the format's own
# signature, so this is both the allow-list and the sniffer.
_SIGNATURES: tuple[tuple[str, str, bytes], ...] = (
    ("image/png", "png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", "jpg", b"\xff\xd8\xff"),
    ("image/gif", "gif", b"GIF87a"),
    ("image/gif", "gif", b"GIF89a"),
)

# 512 KB. A logo rendered at 24px does not need more, and this is fetched by
# every visitor to the customer's site before they have decided to click.
MAX_LOGO_BYTES = 512 * 1024


class LogoRejected(Exception):
    """The upload is not something we will serve. Carries the reason verbatim."""


def sniff_image(data: bytes) -> tuple[str, str]:
    """Return ``(content_type, extension)`` for supported raster images.

    WebP is checked separately: its signature is ``RIFF....WEBP``, with a
    four-byte length in the middle, so it is not a plain prefix match.
    """
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp"

    for content_type, extension, signature in _SIGNATURES:
        if data.startswith(signature):
            return content_type, extension

    # startswith, not a fixed-width slice. `<svg xmlns=...`[:5] is `<svg ` —
    # with the space — which never equals `<svg`, so a slice comparison caught
    # only the SVGs that happen to open with an XML declaration and let the
    # bare ones fall through to the generic message.
    leading = data.lstrip()[:5].lower()
    if leading.startswith(b"<?xml") or leading.startswith(b"<svg"):
        raise LogoRejected(
            "SVG logos are not supported. Upload a PNG, JPEG, WebP or GIF."
        )

    raise LogoRejected(
        "That file is not an image we recognise. Upload a PNG, JPEG, WebP or GIF."
    )


def validate_logo(data: bytes) -> tuple[str, str]:
    """Check size then format, and say which failed.

    Size first: an oversized file is the common case and the cheaper check, and
    telling somebody their 4 MB photo is "not an image we recognise" sends them
    looking for the wrong problem.
    """
    if not data:
        raise LogoRejected("The file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoRejected(
            f"That file is {len(data) // 1024} KB. The limit is "
            f"{MAX_LOGO_BYTES // 1024} KB — it loads on every visit to your site."
        )
    return sniff_image(data)


def logo_storage_key(organization_id: int, workflow_id: int, extension: str) -> str:
    """Where the object lives.

    A fresh UUID every upload rather than a stable name per workflow. Replacing
    a logo at the same key leaves the old image in every CDN and browser cache
    that already has it, and the symptom — the previous logo still showing on
    the customer's site, for some visitors — reads as our bug and cannot be
    cleared from here.
    """
    return f"embed-logos/{organization_id}/{workflow_id}/{uuid.uuid4().hex}.{extension}"


def merge_logo_into_settings(
    settings: dict[str, Any] | None,
    *,
    key: str | None,
    content_type: str | None,
    backend: str | None,
) -> dict[str, Any]:
    """Put the logo into an existing settings blob without disturbing the rest.

    ``update_embed_token`` replaces ``settings`` wholesale, so an upload that
    passed only its own field would silently drop the account's colour, button
    text and post-call card. Merge here, and pass the whole thing.

    ``key=None`` removes it, which is how the delete path works.
    """
    merged = dict(settings or {})
    if key is None:
        merged.pop("logo", None)
        return merged
    merged["logo"] = {
        "key": key,
        "contentType": content_type,
        "backend": backend,
    }
    return merged


def logo_from_settings(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """The stored logo, or None if there isn't a usable one.

    Defensive about shape because ``settings`` is a free-form dict that has
    been written by several versions of the client: anything without a string
    key is treated as absent rather than allowed to raise deep inside a public
    endpoint a visitor's browser is calling.
    """
    if not isinstance(settings, dict):
        return None
    logo = settings.get("logo")
    if not isinstance(logo, dict):
        return None
    key = logo.get("key")
    if not isinstance(key, str) or not key:
        return None
    return logo


def is_own_logo_key(key: str, organization_id: int, workflow_id: int) -> bool:
    """Is this key one we minted for this token, in this organization?

    The reason this exists is worth writing down, because the route that skips
    it looks harmless.

    ``settings`` on an embed token is a free-form dict supplied by the client
    and stored verbatim. So ``settings.logo.key`` is attacker-controlled: any
    authenticated tenant could set it to another organization's storage key —
    ``recordings/12345.wav`` — and then read that object through the
    *unauthenticated* public logo route, which signs whatever key it is given.
    Recordings, transcripts and campaign exports share one bucket and run ids
    are sequential, so that is bulk exfiltration of other customers' calls with
    no authentication and no access-log entry. The delete paths had the mirror
    of it: point ``logo.key`` at somebody else's recording and remove it.

    ``sanitize_client_settings`` now stops the injection at the door. This is
    the second lock: every path that signs or deletes a logo checks the key it
    was handed actually looks like one of ours, for this tenant. One of the two
    would probably do. Both is correct, because the cost of being wrong here is
    a cross-tenant recording leak.
    """
    if not isinstance(key, str):
        return False
    pattern = rf"embed-logos/{int(organization_id)}/{int(workflow_id)}/[0-9a-f]{{32}}\.(png|jpg|gif|webp)"
    return re.fullmatch(pattern, key) is not None


def sanitize_client_settings(
    incoming: dict[str, Any] | None,
    existing: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Client settings with ``logo`` replaced by whatever the server already had.

    ``logo`` is server-managed: it is written only by the upload route, which
    has seen the bytes and minted the key. A client cannot set it, and — just
    as importantly — a client cannot *unset* it by omission.

    That second half was a live bug rather than a hypothetical: the widget
    editor saves the whole settings object without ``logo``, and
    ``update_embed_token`` replaces settings wholesale, so uploading a logo and
    then pressing Save removed it from the record while leaving the object in
    storage forever.
    """
    merged = dict(incoming or {})
    merged.pop("logo", None)
    preserved = logo_from_settings(existing)
    if preserved is not None:
        merged["logo"] = preserved
    return merged


def public_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The settings blob as a visitor's browser may see it.

    The widget is given a URL to our own logo route, never the storage key —
    that is stated in the route's docstring and was not true of the config
    response, which returned the whole blob including ``logo.key``.
    """
    public = dict(settings or {})
    public.pop("logo", None)
    return public

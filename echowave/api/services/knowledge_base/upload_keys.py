"""Where an uploaded document is allowed to live, and who may point at it.

The upload is two requests with a browser PUT between them: ``/upload-url``
mints a presigned URL and hands back the key it signed, the browser PUTs the
file, and ``/process-document`` says "the file is there, ingest it". That
middle hop is why the second request carries an ``s3_key`` at all.

Which makes the key a **client-supplied pointer to a byte range in a shared
bucket**, and the second request ingests whatever is at it into the caller's
organization. Nothing about the FastAPI signature makes that obvious, and the
consequence is not subtle: send another organization's key and their document
is chunked, embedded and stored under your organization_id, where your agent
will read it out to whoever asks. The bucket is shared; the keys are
guessable in shape and predictable in prefix.

So the key is not accepted as given. It is rebuilt from things the server
already knows — the caller's organization and the document uuid — and the
submitted key has to be exactly that. This module owns both halves of that,
because a construction rule and a validation rule kept in two places drift,
and the direction they drift is always the permissive one.
"""

from __future__ import annotations

import posixpath
import re
from uuid import UUID

#: Every knowledge base object lives under this prefix.
KEY_ROOT = "knowledge_base"

#: Characters that survive into a stored filename. Anything else is replaced,
#: which keeps a key printable, greppable and free of the separators that turn
#: one path component into two.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

#: A filename that is entirely dots is a directory reference, not a name.
_DOTS_ONLY = re.compile(r"^\.+$")


def safe_filename(filename: str) -> str:
    """The stored form of a name that arrived from a browser.

    Strips any directory part before sanitising, so ``../../etc/passwd``
    becomes ``passwd`` rather than a key that walks up out of the
    organization's prefix. A name that survives as nothing usable becomes
    ``document`` — an object with an awkward name is recoverable, an object at
    an unexpected key is not.
    """
    base = posixpath.basename(filename.replace("\\", "/")).strip()
    cleaned = _UNSAFE.sub("_", base)
    if not cleaned or _DOTS_ONLY.match(cleaned):
        return "document"
    return cleaned[:200]


def build_document_key(organization_id: int, document_uuid: str, filename: str) -> str:
    """The one key a document for this organization may be stored at."""
    return f"{KEY_ROOT}/{organization_id}/{document_uuid}/{safe_filename(filename)}"


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def key_belongs_to(organization_id: int, document_uuid: str, s3_key: str) -> bool:
    """Whether this caller may ask for ``s3_key`` to be ingested.

    Rebuilt rather than pattern-matched: the key must be exactly what
    :func:`build_document_key` would have produced for this organization, this
    document uuid and the name at the end of the key. A prefix check would
    accept ``knowledge_base/7/<uuid>/../../4/<other-uuid>/contract.pdf``, and
    the point of this function is that no cleverness in the key gets past it.

    The uuid is required to be a real one for the same reason — it is a path
    component, and ``..`` is a valid string.
    """
    if not s3_key or not _is_uuid(document_uuid):
        return False

    prefix = f"{KEY_ROOT}/{organization_id}/{document_uuid}/"
    if not s3_key.startswith(prefix):
        return False

    filename = s3_key[len(prefix) :]
    if not filename or "/" in filename:
        return False

    return s3_key == build_document_key(organization_id, document_uuid, filename)

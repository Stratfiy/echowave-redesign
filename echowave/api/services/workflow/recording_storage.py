"""Where a workflow recording's audio lives, and whose it is.

Uploading a recording is two calls. ``/upload-url`` mints a presigned PUT for a
key this module builds; the browser writes the bytes there; ``POST /`` then
creates the row that names the key. The client carries the key between the two,
which is the whole of the problem: the second call was storing whatever it was
handed.

The lookup behind playback is org-scoped, and that is what made the gap hard to
see. An account could only read rows belonging to itself -- but it could create
a row belonging to itself whose key addressed another tenant's object, and the
scoped lookup then returned it and the runtime fetched the bytes. The tenant
check was running on the row rather than on the thing the row points at.

So the key shape lives in one place and is checked rather than trusted.
``AGENTS.md`` states the rule: never trust an id from the request body to imply
ownership, and a storage key is an id.

**Why the check is a whitelist of shape rather than a scan for traversal.**
``..`` is the obvious escape and not the only one; percent-encoding, backslash
separators and empty segments all resolve differently depending on which layer
normalises first, and the storage backends here do not agree on that. A rule
that enumerates the bad shapes is a rule that is wrong about the next one. So
this instead requires a key to be exactly what this module would have built:
ordinary path segments, the organization's own prefix, nothing clever. The
failure mode of being wrong is a refused upload, which somebody notices --
unlike the reverse.
"""

from __future__ import annotations

import re

#: The one namespace workflow recordings live in. Sharing the bucket with
#: kyc-documents and the rest is exactly why a key from a request body has to
#: be shown to be in here rather than assumed to be.
_ROOT = "recordings"

#: What may appear in a single path segment. Deliberately narrow: letters,
#: digits, dot, dash, underscore and space cover every filename a browser
#: uploads, and admit no separator, no encoding, and no `..` -- which is a
#: single segment of two dots and fails the "at least one non-dot" requirement
#: below.
_SEGMENT = re.compile(r"^[A-Za-z0-9 ._-]+$")


def prefix_for(organization_id: int) -> str:
    """Every key this organization is allowed to name, as a string prefix.

    The trailing slash is load-bearing. ``recordings/4`` is a string prefix of
    ``recordings/42/...``, so a bare ``startswith`` on the id would let one
    organization claim another's objects whenever one id is a prefix of the
    other.
    """
    return f"{_ROOT}/{organization_id}/"


#: Characters replaced rather than dropped when reducing a filename to the
#: shape. Replacing keeps two different uploads two different keys: dropping
#: would map "a#b.mp3" and "a&b.mp3" onto one object, and the second recording
#: would silently overwrite the first.
_UNSAFE_IN_SEGMENT = re.compile(r"[^A-Za-z0-9 ._-]")


def safe_filename(filename: str | None) -> str:
    """The browser's filename, reduced to something this module will accept.

    The upload step takes whatever name the browser sends -- brackets, hashes,
    Devanagari, a path -- and the validation step only accepts plain segments.
    Sanitising here rather than hoping the two agree is what stops an upload of
    "my recording (final).mp3" failing on the *second* call, with an error
    about a storage key that the person naming the file cannot connect to
    anything they did.

    The extension is preserved where there is one: both the storage backends
    and the browser read the suffix, so losing it turns a playable mp3 into an
    octet-stream download.
    """
    name = (filename or "").strip().replace("\\", "/")
    # Only the last path component. A browser occasionally sends a full path,
    # and the directory part is not ours to reproduce.
    name = name.rsplit("/", 1)[-1]

    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""

    stem = _UNSAFE_IN_SEGMENT.sub("_", stem).strip()
    suffix = _UNSAFE_IN_SEGMENT.sub("_", suffix).strip()

    # A name that was nothing but dots, separators or unsafe characters leaves
    # nothing addressable. Naming it is better than refusing the upload of a
    # file somebody really has.
    if stem.strip("._- ") == "":
        stem = "recording"

    return f"{stem}.{suffix}" if suffix else stem


def storage_key_for(*, organization_id: int, recording_id: str, filename: str) -> str:
    """The key a recording's audio is written to.

    Built here rather than at the call site so the upload step and the
    validation step cannot drift. If they did, every upload would fail on the
    second call -- correctly, and confusingly.
    """
    return f"{prefix_for(organization_id)}{recording_id}/{safe_filename(filename)}"


def belongs_to_organization(storage_key: str | None, organization_id: int) -> bool:
    """Whether this organization may name this key.

    False for another tenant's key, for anything outside the recordings
    namespace, and for any key whose segments are not plain names -- see the
    module docstring for why that is a shape requirement rather than a
    traversal scan.
    """
    if not storage_key or not storage_key.strip():
        return False
    if storage_key != storage_key.strip():
        return False
    if not storage_key.startswith(prefix_for(organization_id)):
        return False

    segments = storage_key.split("/")
    for segment in segments:
        if not _SEGMENT.match(segment):
            return False
        # A segment of nothing but dots is `.` or `..` however it is spelled,
        # and neither addresses a file.
        if segment.strip(".") == "":
            return False
    return True

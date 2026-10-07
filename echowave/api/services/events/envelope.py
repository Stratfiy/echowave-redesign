"""The envelope every catalogue event travels in, and what it may carry.

Handoff 35, "Common event contract": event_id, schema_version, occurred_at in
UTC, environment, release, a pseudonymous user_id, workspace_id and task_id
where they apply, trace_id and configuration_version, then typed properties.
Every nullable field is present and None when it does not apply, so a reader
can tell "not applicable" from "forgot to send".

Privacy (handoff 35, "Privacy defaults"; design "Privacy"): no prompts,
transcripts, audio, secrets, KYC documents or raw email. Enforced here, not
hoped for: a property must be named by the event's catalogue entry, may not
be on the forbidden list, and a string value must look like a code (letters,
digits, ``_ . : -``, at most 64 characters). A sentence, an address or a
phone number fails that shape, so it is refused before it is written.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from api import constants
from api.services.events import catalogue

#: Bumped when the envelope's own fields change.
ENVELOPE_VERSION = 1

_CODE = re.compile(r"^[A-Za-z0-9_.:\-]{0,64}$")
MAX_LIST = 10


class EventRefused(ValueError):
    """The event or one of its properties is not allowed. Raised loudly: a
    refused event is a bug in the emitter, never something to drop quietly."""


def pseudonym(prefix: str, value: int | None) -> str | None:
    """A stable, keyed stand-in for an internal id.

    Keyed (HMAC) rather than a bare hash: a bare hash of a small integer is
    reversed by hashing every integer. Not anonymous -- the key holder can
    link it back, which is what deletion requests need -- so it is still
    personal data and handled as such (handoff 35, "Access and retention").
    """
    if value is None:
        return None
    key = constants.ANALYTICS_PSEUDONYM_KEY
    if not key:
        raise EventRefused("ANALYTICS_PSEUDONYM_KEY is not configured")
    digest = hmac.new(key.encode(), f"{prefix}:{value}".encode(), hashlib.sha256)
    return f"{prefix}_{digest.hexdigest()[:24]}"


def _clean_value(event: str, key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if not _CODE.match(value):
            raise EventRefused(f"{event}.{key} must be a short code, not free text")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_LIST:
            raise EventRefused(f"{event}.{key} carries too many values")
        return [_clean_value(event, key, v) for v in value]
    raise EventRefused(f"{event}.{key} has an unsupported type")


def clean_properties(name: str, properties: dict[str, Any] | None) -> dict[str, Any]:
    """The properties, checked against the catalogue. Raises EventRefused."""
    spec = catalogue.get(name)
    out: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if key in catalogue.FORBIDDEN_PROPERTIES:
            raise EventRefused(f"{name}.{key} is never sent to analytics")
        if key not in spec.allowed:
            raise EventRefused(f"{name} does not carry {key}")
        out[key] = _clean_value(name, key, value)
    if "duration_ms" in out and out["duration_ms"] is not None:
        if not isinstance(out["duration_ms"], (int, float)) or out["duration_ms"] < 0:
            raise EventRefused(f"{name}.duration_ms must be a non-negative number")
    return out


def build(
    name: str,
    *,
    user_id: int | None = None,
    organization_id: int | None = None,
    task_id: int | str | None = None,
    trace_id: str | None = None,
    configuration_version: str | None = None,
    properties: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """One envelope, ready for the outbox. Raises EventRefused (unknown
    event, a property it may not carry, or no pseudonym key)."""
    if name not in catalogue.CATALOGUE:
        raise EventRefused(f"{name} is not in the event catalogue")
    spec = catalogue.get(name)
    when = (occurred_at or datetime.now(UTC)).astimezone(UTC)
    if trace_id is not None and not _CODE.match(str(trace_id)):
        raise EventRefused("trace_id must be a short code")
    if task_id is not None and not _CODE.match(str(task_id)):
        raise EventRefused("task_id must be a short code")
    if configuration_version is not None and not _CODE.match(
        str(configuration_version)
    ):
        raise EventRefused("configuration_version must be a short code")
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "name": name,
        "schema_version": spec.version,
        "envelope_version": ENVELOPE_VERSION,
        "owner": spec.owner,
        "occurred_at": when.isoformat(),
        "environment": constants.ENVIRONMENT,
        "release": constants.RELEASE or constants.APP_VERSION,
        "user_id": pseudonym("u", user_id),
        "workspace_id": pseudonym("w", organization_id),
        "task_id": str(task_id) if task_id is not None else None,
        "trace_id": str(trace_id) if trace_id is not None else None,
        "configuration_version": configuration_version,
        "properties": clean_properties(name, properties),
    }

"""Secrets for a person's connections, encrypted at rest.

A pasted token, OAuth tokens and the PKCE verifier of a sign-in in progress
are stored as one Fernet-encrypted JSON object under the platform key. They
are never returned by a route, never logged and never put in a prompt. No
payment card details are ever handled here: the ordering apps take payment
on their own side.
"""

from __future__ import annotations

import json
from typing import Any

from api.constants import PLATFORM_CREDENTIAL_SECRET


class VaultUnavailable(RuntimeError):
    """No platform key: refuse to store a secret in the clear."""


def _cipher():
    from cryptography.fernet import Fernet

    if not PLATFORM_CREDENTIAL_SECRET:
        raise VaultUnavailable(
            "Connections cannot be saved: the platform key is not set."
        )
    return Fernet(PLATFORM_CREDENTIAL_SECRET.encode())


def seal(secret: dict[str, Any] | None) -> str | None:
    if not secret:
        return None
    return _cipher().encrypt(json.dumps(secret).encode()).decode()


def open_(stored: str | None) -> dict[str, Any]:
    if not stored:
        return {}
    try:
        return json.loads(_cipher().decrypt(stored.encode()).decode())
    except VaultUnavailable:
        raise
    except Exception:  # noqa: BLE001 - a key rotated under it reads as nothing
        return {}


__all__ = ["VaultUnavailable", "open_", "seal"]

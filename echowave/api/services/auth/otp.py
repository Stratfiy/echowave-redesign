"""One-time codes: generating them, and storing them safely.

Shared by email verification at signup and phone verification for test numbers.
Extracted rather than duplicated because the two get the security decisions
right or wrong together, and a copy that drifts is a copy that is weaker.
"""

from __future__ import annotations

import hashlib
import secrets

#: Six digits. Short enough to read off a screen and type, which is the whole
#: reason for the attempt ceiling that has to accompany it.
CODE_DIGITS = 6


def generate_code() -> str:
    """A six-digit code from a cryptographic source.

    ``secrets`` rather than ``random``: ``random`` is a Mersenne Twister seeded
    from the clock, so observing a handful of codes lets an attacker predict
    the next — which would let them verify an address or a number they do not
    control.
    """
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def generate_salt() -> str:
    return secrets.token_hex(8)


def hash_code(code: str, salt: str) -> str:
    """Salted SHA-256.

    Deliberately different from ``hash_recovery_code``, which is an unsalted
    digest. That is correct there — recovery codes are full-entropy random
    values with no dictionary to precompute against. A six-digit code has a
    search space of one million, so an unsalted digest of one is a rainbow-table
    lookup. The salt is what forces per-row work on a leaked database.

    Not bcrypt: these live for minutes behind an attempt ceiling, so a slow KDF
    buys very little on a user-facing request path.
    """
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()

"""Proving a customer can answer a number before we agree to dial it.

The feature this enables is trial calling: an account with no rented number can
verify their own mobile and hear an agent, which matters while managed numbers
sit behind carrier KYC.

The hole it closes is larger. ``organization_preferences.test_phone_number`` is
free text and ``routes/telephony.py`` dials it, so today an account can have
Decibyl ring any number its user types. That is a telephone-harassment vector
wearing the costume of a convenience feature, and the fix is the same
mechanism: dial it only once somebody has answered it.

Three limits, each closing a different abuse:

* **Attempts per code.** Six digits is a million possibilities and falls to
  exhaustive guessing in well under a second. Five wrong answers burns the code.
* **Sends per number.** Without a ceiling the verification endpoint is a
  free SMS cannon pointed at a stranger, paid for by us.
* **A cooldown between sends.** Stops the same thing at a slower rate, and
  stops an impatient user paying for four texts to receive one code.

None of the three is optional and none substitutes for the others.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.constants import (
    VERIFICATION_CODE_TTL_MINUTES,
    VERIFICATION_MAX_ATTEMPTS,
    VERIFICATION_MAX_SENDS,
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
)
from api.services.compliance.dnd import normalise_number


class VerificationError(Exception):
    """Something the caller is expected to show the user, not retry."""

    reason: str = "verification_failed"


class NumberNotDialable(VerificationError):
    reason = "not_dialable"


class TooManyAttempts(VerificationError):
    reason = "too_many_attempts"


class TooManySends(VerificationError):
    reason = "too_many_sends"


class ResendTooSoon(VerificationError):
    reason = "resend_too_soon"


class CodeExpired(VerificationError):
    reason = "code_expired"


class CodeIncorrect(VerificationError):
    reason = "code_incorrect"


@dataclass(frozen=True)
class StartedVerification:
    phone_number: str
    #: The plaintext code, returned only so the caller can send it. It is never
    #: stored, never logged, and never returned to the browser — a verification
    #: whose code comes back in the API response verifies nothing.
    code: str
    expires_at: datetime


def generate_code() -> str:
    """A six-digit code from a cryptographic source.

    ``secrets`` rather than ``random`` because ``random`` is a Mersenne Twister
    seeded from the clock: observing a handful of codes lets an attacker predict
    the next, which would let them verify a number they cannot answer.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str, salt: str) -> str:
    """Salted SHA-256.

    Deliberately different from ``hash_recovery_code``, which is an unsalted
    digest. That is correct there — recovery codes are full-entropy random
    values with no dictionary to precompute against. A six-digit code has a
    search space of one million, so an unsalted digest of one is a table
    lookup. The salt is what forces per-row work on a leaked database.

    Not bcrypt: this is checked on a user-facing request path and expires in
    minutes, so the cost of a slow KDF buys very little against a code that
    also has an attempt ceiling and a short life.
    """
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


async def start_verification(
    organization_id: int,
    raw_number: str,
    *,
    user_id: int | None = None,
    label: str | None = None,
    now: datetime | None = None,
    db=None,
) -> StartedVerification:
    """Issue a code for a number, subject to the rate limits.

    Returns the code so the caller can send it by SMS. Deliberately does not
    send it here: the transport belongs to the caller, which knows which
    telephony configuration this organization sends on, and keeping the
    decision out of this module lets the limits be tested without a carrier.
    """
    moment = now or datetime.now(UTC)
    number = normalise_number(raw_number)
    if number is None:
        raise NumberNotDialable(f"{raw_number!r} is not a phone number we can dial.")

    from api.db import db_client as default_db_client

    client = db or default_db_client
    existing = await client.get_verified_number(organization_id, number)

    if existing is not None:
        if existing.send_count >= VERIFICATION_MAX_SENDS:
            raise TooManySends(
                "This number has been sent the maximum number of codes. "
                "Contact support if you still need to verify it."
            )
        last_sent = existing.last_sent_at
        if last_sent is not None:
            # Rows written before this column existed, and any row a migration
            # backfilled, can carry a naive datetime. Comparing naive to aware
            # raises, and a TypeError here would read as "verification is
            # broken" rather than "one row is odd".
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=UTC)
            elapsed = (moment - last_sent).total_seconds()
            if elapsed < VERIFICATION_RESEND_COOLDOWN_SECONDS:
                raise ResendTooSoon(
                    "A code was just sent. Wait "
                    f"{int(VERIFICATION_RESEND_COOLDOWN_SECONDS - elapsed)} seconds "
                    "before asking for another."
                )

    code = generate_code()
    salt = secrets.token_hex(8)
    expires_at = moment + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)

    await client.upsert_verified_number_challenge(
        organization_id,
        number,
        code_hash=hash_code(code, salt),
        code_salt=salt,
        code_expires_at=expires_at,
        sent_at=moment,
        label=label,
        created_by=user_id,
    )

    # The code is never logged. A log line with the code in it turns anyone who
    # can read logs into someone who can verify any number.
    logger.info(
        f"Issued a verification code for organization {organization_id} "
        f"ending {number[-4:]}"
    )
    return StartedVerification(phone_number=number, code=code, expires_at=expires_at)


async def confirm_verification(
    organization_id: int,
    raw_number: str,
    code: str,
    *,
    now: datetime | None = None,
    db=None,
) -> str:
    """Check a code and mark the number verified. Returns the normalised number."""
    moment = now or datetime.now(UTC)
    number = normalise_number(raw_number)
    if number is None:
        raise NumberNotDialable(f"{raw_number!r} is not a phone number we can dial.")

    from api.db import db_client as default_db_client

    client = db or default_db_client
    record = await client.get_verified_number(organization_id, number)

    # An unknown number and a number with no live code get the same answer.
    # Distinguishing them would let someone enumerate which numbers an
    # organization has tried to verify.
    if record is None or not record.code_hash or not record.code_salt:
        raise CodeIncorrect("That code is not right. Ask for a new one.")

    if record.attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise TooManyAttempts(
            "Too many incorrect codes. Ask for a new one."
        )

    expires_at = record.code_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or moment >= expires_at:
        raise CodeExpired("That code has expired. Ask for a new one.")

    # compare_digest, not ==. String equality returns as soon as it finds a
    # difference, and the time it takes leaks how much of the digest matched.
    if not hmac.compare_digest(
        record.code_hash, hash_code(code.strip(), record.code_salt)
    ):
        await client.record_verification_attempt(organization_id, number)
        raise CodeIncorrect("That code is not right.")

    await client.mark_number_verified(organization_id, number, verified_at=moment)
    logger.info(
        f"Organization {organization_id} verified a number ending {number[-4:]}"
    )
    return number


async def is_verified(organization_id: int, raw_number: str, *, db=None) -> bool:
    """Whether this organization has proved it can answer this number."""
    number = normalise_number(raw_number)
    if number is None:
        return False

    from api.db import db_client as default_db_client

    client = db or default_db_client
    record = await client.get_verified_number(organization_id, number)
    return record is not None and record.status == "verified"

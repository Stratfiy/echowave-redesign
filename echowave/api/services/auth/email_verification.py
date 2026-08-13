"""Proving an email address at signup.

Delivered over the existing SMTP path (Resend), which is already configured and
already sends invoices and vouchers in production. That is the whole reason
this is finishable and phone verification is not: email carries no equivalent
of India's DLT registration, so there is no paperwork between the code being
written and a code arriving.

The limits mirror the phone flow deliberately — attempts per code, sends per
address, and a cooldown — because the abuses are the same shape. A six-digit
code falls to exhaustive guessing in under a second without a ceiling, and an
uncapped send endpoint is a way to have our domain mail a stranger repeatedly,
which costs us our sending reputation rather than a carrier bill.

**Nothing here is a hard gate yet.** Every account that existed before this
shipped has email_verified_at NULL, and refusing them would be an outage
dressed as a security improvement. What this provides is the proof and the
record; deciding what to withhold from an unverified account is a separate,
reversible decision.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.constants import (
    VERIFICATION_CODE_TTL_MINUTES,
    VERIFICATION_MAX_ATTEMPTS,
    VERIFICATION_MAX_SENDS,
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
)
from api.services.auth import otp


class EmailVerificationError(Exception):
    reason = "verification_failed"


class TooManyAttempts(EmailVerificationError):
    reason = "too_many_attempts"


class TooManySends(EmailVerificationError):
    reason = "too_many_sends"


class ResendTooSoon(EmailVerificationError):
    reason = "resend_too_soon"


class CodeExpired(EmailVerificationError):
    reason = "code_expired"


class CodeIncorrect(EmailVerificationError):
    reason = "code_incorrect"


class AlreadyVerified(EmailVerificationError):
    reason = "already_verified"


@dataclass(frozen=True)
class StartedVerification:
    email: str
    #: Returned only so the caller can send it. Never stored in plaintext,
    #: never logged, never returned to the browser — a verification whose code
    #: comes back in the API response verifies nothing.
    code: str
    expires_at: datetime


def subject() -> str:
    return "Verify your Decibyl email"


def body(code: str) -> str:
    """Plain text, deliberately.

    A verification mail that renders as a wall of broken HTML in a client we
    did not test is worse than one that is plainly readable everywhere. It also
    keeps the code selectable rather than trapped in a button.
    """
    return (
        f"Your Decibyl verification code is {code}\n\n"
        f"It expires in {VERIFICATION_CODE_TTL_MINUTES} minutes.\n\n"
        "If you did not create a Decibyl account, you can ignore this email — "
        "nobody can use this code without access to your inbox."
    )


async def start_verification(
    user_id: int,
    email: str,
    *,
    now: datetime | None = None,
    db=None,
) -> StartedVerification:
    """Issue a code for this user's address, subject to the limits.

    Returns the code for the caller to send. Sending is not done here so the
    limits can be tested without a mail server, and so a mail failure does not
    have to be untangled from a rate-limit decision.
    """
    moment = now or datetime.now(UTC)
    address = (email or "").strip().lower()
    if not address:
        raise EmailVerificationError("No email address on this account.")

    from api.db import db_client as default_db_client

    client = db or default_db_client

    user = await client.get_user_by_id(user_id)
    if user is not None and getattr(user, "email_verified_at", None) is not None:
        raise AlreadyVerified("This address is already verified.")

    existing = await client.get_email_challenge(user_id)
    if existing is not None:
        if existing.send_count >= VERIFICATION_MAX_SENDS:
            raise TooManySends(
                "We have sent the maximum number of codes to this address. "
                "Contact support if you still need to verify it."
            )
        last_sent = existing.last_sent_at
        if last_sent is not None:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=UTC)
            elapsed = (moment - last_sent).total_seconds()
            if elapsed < VERIFICATION_RESEND_COOLDOWN_SECONDS:
                raise ResendTooSoon(
                    "A code was just sent. Wait "
                    f"{int(VERIFICATION_RESEND_COOLDOWN_SECONDS - elapsed)} "
                    "seconds before asking for another."
                )

    code = otp.generate_code()
    salt = otp.generate_salt()
    expires_at = moment + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)

    await client.upsert_email_challenge(
        user_id,
        email=address,
        code_hash=otp.hash_code(code, salt),
        code_salt=salt,
        expires_at=expires_at,
        sent_at=moment,
    )

    # Never the code, and never the full address either: a log line carrying
    # both is a way to sign in as somebody for anyone who can read logs.
    logger.info(f"Issued an email verification code for user {user_id}")
    return StartedVerification(email=address, code=code, expires_at=expires_at)


async def confirm_verification(
    user_id: int,
    code: str,
    *,
    now: datetime | None = None,
    db=None,
) -> None:
    """Check a code and mark the address verified."""
    moment = now or datetime.now(UTC)

    from api.db import db_client as default_db_client

    client = db or default_db_client
    challenge = await client.get_email_challenge(user_id)

    # No challenge and a wrong code answer identically, so the endpoint cannot
    # be used to work out whether a verification is outstanding.
    if challenge is None:
        raise CodeIncorrect("That code is not right. Ask for a new one.")

    if challenge.attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise TooManyAttempts("Too many incorrect codes. Ask for a new one.")

    expires_at = challenge.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or moment >= expires_at:
        raise CodeExpired("That code has expired. Ask for a new one.")

    # compare_digest, not ==: string equality returns at the first differing
    # byte, and how long that takes leaks how much of the digest matched.
    if not hmac.compare_digest(
        challenge.code_hash, otp.hash_code(code.strip(), challenge.code_salt)
    ):
        await client.record_email_attempt(user_id)
        raise CodeIncorrect("That code is not right.")

    await client.mark_email_verified(user_id, verified_at=moment)
    logger.info(f"User {user_id} verified their email address")

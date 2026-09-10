"""Setting a new password from an inbox.

Until this existed a person who forgot their password had no way back into
their account except asking us, and "email support" is not a recovery flow
for a product that takes money. The mechanics are the email-verification
ones — a six-digit code, hashed and salted, ten minutes, five guesses, five
sends — because a reset code is the same kind of credential as a
verification code and deserves exactly the same handling.

**It never says whether an address has an account.** The forgot route
answers identically for a known and an unknown address, and takes the same
path as far as it can, because a reset form that says "no account with that
email" is a membership oracle for anyone with a list.

**Google-only accounts are told, by mail.** An account with no password has
nothing to reset; mailing them "sign in with Google" answers the person
without answering the oracle.
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
from api.services.auth.email_verification import (
    CodeExpired,
    CodeIncorrect,
    EmailVerificationError,
    ResendTooSoon,
    TooManyAttempts,
    TooManySends,
)
from api.utils.auth import hash_password

#: The floor for a password set from here. Signup has no floor today; when it
#: gets one it should be this constant, not a second number.
MIN_PASSWORD_LENGTH = 8


class PasswordTooShort(EmailVerificationError):
    reason = "password_too_short"


@dataclass(frozen=True)
class StartedReset:
    user_id: int
    email: str
    #: Only so the caller can send it. Never stored, logged or returned.
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class NoPasswordToReset:
    """The address has an account that signs in with Google, not a password."""

    email: str


def subject() -> str:
    return "Reset your Decibyl password"


def body(code: str) -> str:
    return (
        f"Your Decibyl password reset code is {code}\n\n"
        f"It expires in {VERIFICATION_CODE_TTL_MINUTES} minutes. Enter it on the "
        "reset page along with your new password.\n\n"
        "If you did not ask to reset your password, you can ignore this email — "
        "your password has not changed, and nobody can use this code without "
        "access to your inbox."
    )


def google_only_body() -> str:
    return (
        "Somebody asked to reset the password on your Decibyl account.\n\n"
        "This account signs in with Google and has no password to reset. Use "
        '"Sign in with Google" on the login page.\n\n'
        "If this was not you, nothing has changed and there is nothing to do."
    )


async def start_reset(
    email: str,
    *,
    now: datetime | None = None,
    db=None,
) -> StartedReset | NoPasswordToReset | None:
    """Issue a code for the account at this address, if there is one.

    Returns ``None`` for an address with no account, and for one that has hit
    its send limits — the caller treats every outcome the same way towards
    the person asking. Only the mail differs.
    """
    moment = now or datetime.now(UTC)
    address = (email or "").strip().lower()
    if not address:
        return None

    from api.db import db_client as default_db_client

    client = db or default_db_client

    user = await client.get_user_by_email(address)
    if user is None:
        return None
    if not user.password_hash:
        return NoPasswordToReset(email=address)

    existing = await client.get_password_reset_challenge(user.id)
    if existing is not None:
        if existing.send_count >= VERIFICATION_MAX_SENDS:
            raise TooManySends(
                "We have sent the maximum number of codes to this address. "
                "Contact support if you still cannot get in."
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

    await client.upsert_password_reset_challenge(
        user.id,
        email=address,
        code_hash=otp.hash_code(code, salt),
        code_salt=salt,
        expires_at=expires_at,
        sent_at=moment,
    )
    logger.info(f"Issued a password reset code for user {user.id}")
    return StartedReset(
        user_id=user.id, email=address, code=code, expires_at=expires_at
    )


async def confirm_reset(
    email: str,
    code: str,
    new_password: str,
    *,
    now: datetime | None = None,
    db=None,
) -> int:
    """Check the code and set the password. Returns the user id.

    A wrong code, an unknown address and no outstanding reset all raise the
    same :class:`CodeIncorrect`, for the same reason the forgot route says
    nothing: the reset form must not be a second oracle.
    """
    moment = now or datetime.now(UTC)
    address = (email or "").strip().lower()

    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort(
            f"Use at least {MIN_PASSWORD_LENGTH} characters for the new password."
        )

    from api.db import db_client as default_db_client

    client = db or default_db_client

    user = await client.get_user_by_email(address) if address else None
    challenge = (
        await client.get_password_reset_challenge(user.id) if user is not None else None
    )
    if user is None or challenge is None or challenge.email != address:
        raise CodeIncorrect("That code is not right. Ask for a new one.")

    if challenge.attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise TooManyAttempts("Too many incorrect codes. Ask for a new one.")

    expires_at = challenge.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or moment >= expires_at:
        raise CodeExpired("That code has expired. Ask for a new one.")

    if not hmac.compare_digest(
        challenge.code_hash, otp.hash_code(code.strip(), challenge.code_salt)
    ):
        await client.record_password_reset_attempt(user.id)
        raise CodeIncorrect("That code is not right.")

    await client.set_password_and_destroy_challenge(
        user.id, password_hash=hash_password(new_password)
    )
    logger.info(f"User {user.id} set a new password from a reset code")
    return user.id

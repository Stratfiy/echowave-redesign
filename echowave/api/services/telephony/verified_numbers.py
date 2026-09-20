"""Proving a customer can answer a number before we agree to dial it.

The feature this enables is trial calling: an account with no rented number can
verify their own mobile and hear an agent, which matters while managed numbers
sit behind carrier KYC.

The hole it closes is larger. ``organization_preferences.test_phone_number`` is
free text and ``routes/telephony.py`` dials it, so today an account can have
Decibyl ring any number its user types. That is a telephone-harassment vector
wearing the costume of a convenience feature, and the fix is the same
mechanism: dial it only once somebody has answered it.

Five limits, each closing a different abuse:

* **Attempts per code.** Six digits is a million possibilities and falls to
  exhaustive guessing in well under a second. Five wrong answers burns the code.
* **Sends per number.** Without a ceiling the verification endpoint is a
  free SMS cannon pointed at a stranger, paid for by us.
* **A cooldown between sends.** Stops the same thing at a slower rate, and
  stops an impatient user paying for four texts to receive one code.
* **Where the code may be sent.** The code goes out as a call on the platform's
  own carriage, to a destination the caller types. An arbitrary international
  number is our carriage; a premium-rate number pays out to whoever asked us to
  dial it. Indian mobiles only.
* **Sends per account per day.** The three limits above are all per *number*,
  and none of them notices an account working through a thousand different
  ones.

None of the five is optional and none substitutes for the others. The last two
apply only when the platform is paying -- see ``_uses_platform_line``.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.constants import (
    VERIFICATION_CODE_TTL_MINUTES,
    VERIFICATION_MAX_ATTEMPTS,
    VERIFICATION_MAX_DAILY_SENDS,
    VERIFICATION_MAX_SENDS,
    VERIFICATION_RESEND_COOLDOWN_SECONDS,
)
from api.services.auth import otp as _otp
from api.services.compliance.dnd import is_indian_mobile, normalise_number


class VerificationError(Exception):
    """Something the caller is expected to show the user, not retry."""

    reason: str = "verification_failed"


class NumberNotDialable(VerificationError):
    reason = "not_dialable"


class TooManyAttempts(VerificationError):
    reason = "too_many_attempts"


class TooManySends(VerificationError):
    reason = "too_many_sends"


class DailyLimitReached(VerificationError):
    reason = "daily_limit_reached"


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


def _uses_platform_line() -> bool:
    """Whether a code goes out on carriage Decibyl pays for.

    Every real channel does: ``voice`` dials from the shared outbound pool and
    both SMS branches send on the platform's own Plivo or Twilio account,
    deliberately rather than the customer's telephony configuration -- the
    accounts this feature serves have none. Only the dev-only ``log`` channel
    puts nothing on a network.

    So this is the question "is somebody being rung, at our expense", and it is
    what the two limits below hang on. Indirected through a function rather
    than read at import so a test can say which deployment it is describing,
    and so the module does not bake in the channel an operator can change.
    """
    from api.services.telephony import verification_sender

    return verification_sender.uses_platform_line()


#: Re-exported from services/auth/otp so email verification at signup and
#: phone verification here cannot drift apart on the security decisions. The
#: reasoning for the salt and for SHA-256 over bcrypt lives there.
generate_code = _otp.generate_code
hash_code = _otp.hash_code


async def start_verification(
    organization_id: int,
    raw_number: str,
    *,
    user_id: int | None = None,
    label: str | None = None,
    now: datetime | None = None,
    db=None,
    charge=None,
) -> StartedVerification:
    """Issue a code for a number, subject to the rate limits.

    A new number past the account's first two is charged two credits (KAN-56):
    the code goes out as a voice call, which costs carriage. Resends of a
    number already started are not charged again — the charge is keyed on the
    number. ``charge`` is the ledger call, injectable for tests.

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

    # Two limits that only exist when the platform is paying for the carriage,
    # asked before anything is stored: a refused number should not leave a row
    # behind, and must not have been charged for.
    if _uses_platform_line():
        # Where. The caller picks the destination and we are billed for it, so
        # an arbitrary international number is our carriage and a premium-rate
        # number is a payout to whoever asked us to dial. normalise_number
        # accepts any country by design -- it builds a DND comparison key, not
        # a permission -- so the policy has to be stated here.
        if not is_indian_mobile(number):
            raise NumberNotDialable(
                "We can only send verification calls to Indian mobile numbers "
                "(+91). Contact support to verify a number outside that range."
            )

        # How many. The per-number ceiling and the cooldown bound what one
        # number can be sent and say nothing about how many different numbers
        # an account can work through in a day, which is the volume abuse.
        #
        # A trailing window rather than a calendar day: a cap that resets at
        # midnight hands an abuser two full allowances back to back across the
        # boundary.
        sent_today = await client.count_verification_sends_since(
            organization_id, moment - timedelta(days=1)
        )
        if sent_today >= VERIFICATION_MAX_DAILY_SENDS:
            raise DailyLimitReached(
                "This account has requested its daily limit of verification "
                "calls. Try again tomorrow, or contact support."
            )

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

    if existing is None:
        await _charge_a_new_number(
            client, organization_id=organization_id, number=number, charge=charge
        )

    code = generate_code()
    salt = _otp.generate_salt()
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


async def _charge_a_new_number(
    client, *, organization_id: int, number: str, charge
) -> None:
    """Two credits for a new number past the first two. Never raises: a
    verification that could not be charged still goes out, and the ledger
    is reconciled — the customer is not left unable to verify over our
    bookkeeping."""
    from api.services.billing import events as billing_events

    try:
        held = await client.list_verified_numbers(organization_id)
        if len(held) < billing_events.FREE_NUMBER_VERIFICATIONS:
            return
        debit = charge or billing_events.charge_in_own_session
        await debit(
            organization_id=organization_id,
            event=billing_events.NUMBER_VERIFICATION,
            ref_id=f"{organization_id}:{number}",
            note=f"number ending {number[-4:]}",
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "Could not charge the verification of a number for organization {}: {}",
            organization_id,
            exc,
        )


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
        raise TooManyAttempts("Too many incorrect codes. Ask for a new one.")

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

"""Time-based one-time passwords, as a second factor on login.

The most requested control in any security questionnaire, and until now the
honest answer was no: access was a password and a bearer token, and a leaked
password was the whole account.

TOTP rather than SMS. SMS one-time codes are interceptable by SIM swap — a
well-documented attack in India specifically — and they cost money per login.
An authenticator app is free, offline, and stronger.

Three decisions worth stating because each closes an attack the naive version
leaves open:

* **The secret is encrypted at rest**, with the same Fernet key as provider
  credentials. A TOTP secret in plaintext is a password-equivalent: anyone
  reading the table can generate valid codes forever.
* **A used code cannot be replayed.** TOTP codes stay valid for a whole step,
  so a code shoulder-surfed or captured from a proxied login works again inside
  that window. The last accepted counter is recorded and never accepted twice.
* **Recovery codes are stored hashed**, like passwords, and are single-use.
  They are the account's only route back after a lost phone, which also makes
  them a second password — storing them readable would undo the whole feature.

One step of drift is allowed either side, because phone clocks are wrong often
enough that zero tolerance produces support tickets rather than security.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

import pyotp
from loguru import logger

#: Seconds a code is valid for. The RFC default, and what every authenticator
#: app assumes.
STEP_SECONDS = 30

#: Steps of clock drift tolerated either side. One is the usual compromise: it
#: widens the window to 90 seconds, which is far short of useful to an attacker
#: and long enough for a phone that is a minute out.
DRIFT_STEPS = 1

#: How many recovery codes are issued when MFA is enabled.
RECOVERY_CODE_COUNT = 10

#: Bytes of entropy per recovery code. Ten hex characters — enough that guessing
#: is hopeless, short enough that people will actually write them down.
RECOVERY_CODE_BYTES = 5


@dataclass(frozen=True)
class Enrollment:
    """What the user needs to finish setting MFA up.

    ``secret`` is shown exactly once, at enrolment. After that it exists only
    encrypted in the database, and neither the UI nor an administrator can
    retrieve it — a support flow that can read it back is an account takeover
    waiting to be socially engineered.
    """

    secret: str
    uri: str
    recovery_codes: tuple[str, ...]


def _cipher():
    """The shared cipher, so a key rotation covers TOTP secrets too.

    Left out, rotating the secret would lock every person with MFA enabled out
    of their own account — and out of the recovery flow, since it is the same
    secret behind both.
    """
    from api.services import secret_rotation

    try:
        return secret_rotation.cipher()
    except secret_rotation.SecretError as exc:
        raise RuntimeError(
            "PLATFORM_CREDENTIAL_SECRET is not usable. Refusing to store a "
            f"TOTP secret in plaintext. {exc}"
        ) from exc


def encrypt_secret(secret: str) -> str:
    return _cipher().encrypt(secret.encode()).decode()


def decrypt_secret(stored: str) -> str:
    return _cipher().decrypt(stored.encode()).decode()


def hash_recovery_code(code: str) -> str:
    """Recovery codes are hashed, not encrypted.

    Encryption would be reversible, and a reversible store of what amounts to
    ten spare passwords is a worse liability than the passwords themselves.
    SHA-256 rather than bcrypt because these are full-entropy random values, not
    human-chosen strings — there is no dictionary to slow an attacker down with.
    """
    return hashlib.sha256(code.strip().replace("-", "").lower().encode()).hexdigest()


def generate_recovery_codes() -> tuple[str, ...]:
    return tuple(
        secrets.token_hex(RECOVERY_CODE_BYTES) for _ in range(RECOVERY_CODE_COUNT)
    )


def begin_enrollment(*, email: str, issuer: str = "Decibyl") -> Enrollment:
    """A fresh secret, its provisioning URI, and a set of recovery codes.

    Nothing is persisted here. The secret only becomes the account's second
    factor once the user has proved they can generate a code from it — enrolling
    on the strength of the QR code alone locks out anyone whose scan failed.
    """
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret, interval=STEP_SECONDS).provisioning_uri(
        name=email, issuer_name=issuer
    )
    return Enrollment(secret=secret, uri=uri, recovery_codes=generate_recovery_codes())


def verify_code(
    *, secret: str, code: str, last_counter: int | None = None
) -> int | None:
    """Check a code, and return the counter it was valid for.

    ``None`` means rejected. The returned counter must be stored and passed back
    as ``last_counter`` on the next attempt: without that, a code remains usable
    for its whole step and can be replayed by anyone who saw it.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return None

    totp = pyotp.TOTP(secret, interval=STEP_SECONDS)
    import time

    now = int(time.time())
    current = now // STEP_SECONDS

    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        counter = current + offset
        if not secrets.compare_digest(totp.at(counter * STEP_SECONDS), code):
            continue
        if last_counter is not None and counter <= last_counter:
            logger.warning("Rejected a replayed TOTP code (counter {})", counter)
            return None
        return counter

    return None


def verify_recovery_code(code: str, hashed_codes: list[str]) -> str | None:
    """Match a recovery code against the stored hashes.

    Returns the hash that matched so the caller can remove it — a recovery code
    that survives its use is a permanent bypass of the second factor.
    """
    candidate = hash_recovery_code(code)
    for stored in hashed_codes:
        if secrets.compare_digest(candidate, stored):
            return stored
    return None

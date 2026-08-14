"""Rotating ``PLATFORM_CREDENTIAL_SECRET`` without a window of broken calls.

One Fernet key encrypts more than provider keys. It also covers the customers'
own BYOK keys, staff TOTP secrets, Google Calendar refresh tokens, and the
database backups themselves. Changing it was therefore a single-line env edit
that silently broke five subsystems at once — and broke them *quietly*, because
every decrypt path was written to degrade rather than raise: a managed call
falls through to "no key" and fails at the vendor with a 401, an MFA prompt
rejects a correct code, a calendar sync stops. Nothing says "the secret
changed".

Which meant, in practice, that the secret was never rotated. A key that cannot
be rotated is a key that is never rotated, and a leaked one then has no remedy
short of re-entering every credential on the platform by hand.

The fix is the standard one, and Fernet is built for it: **two keys at once.**

``PLATFORM_CREDENTIAL_SECRET`` is what new ciphertext is written under.
``PLATFORM_CREDENTIAL_SECRET_PREVIOUS`` is only ever used to read. So a
rotation is three ordered steps, none of which has a broken window:

1. Move the current secret into ``…_PREVIOUS`` and put the new one in
   ``PLATFORM_CREDENTIAL_SECRET``. Restart. Everything still decrypts — old
   rows under the previous key, new writes under the new one.
2. Run the re-encryption (``scripts/rotate_platform_secret.py``). Every stored
   ciphertext is read and written back under the new key. Re-runnable, and
   safe to run while serving.
3. Once ``pending`` reads zero, drop ``…_PREVIOUS``. Restart.

Step 3 is what makes the old key worthless, and until it happens the rotation
has not actually achieved anything — which is why ``status()`` reports the
count that has to reach zero rather than a boolean anybody could read as done.

The order matters in one direction only: removing the previous key before the
re-encryption finishes strands whatever is left. That is recoverable — put it
back — but only if the operator is told, which is what the readiness check is
for.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    PLATFORM_CREDENTIAL_SECRET,
    PLATFORM_CREDENTIAL_SECRET_PREVIOUS,
)


class SecretError(ValueError):
    """The configured secret cannot be used."""


def _key(value: str | None, name: str) -> Fernet | None:
    if not value:
        return None
    try:
        return Fernet(value.encode())
    except (ValueError, TypeError) as exc:
        raise SecretError(
            f"{name} is not a valid Fernet key. It must be 32 url-safe "
            'base64-encoded bytes. Generate one with: python -c "from '
            "cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        ) from exc


def cipher() -> MultiFernet:
    """The cipher every subsystem encrypts and decrypts through.

    A ``MultiFernet`` rather than a ``Fernet``: identical ``encrypt`` and
    ``decrypt``, except that decryption tries each key in turn while encryption
    always uses the first. That asymmetry is the whole feature — old ciphertext
    stays readable for exactly as long as the previous key is configured, and
    not one write goes out under it.

    Raises rather than falling back when nothing is configured. A default here
    would mean a deployment that forgot to set the secret still appeared to
    work, with every key on the platform encrypted under a value published in
    this repository.
    """
    current = _key(PLATFORM_CREDENTIAL_SECRET, "PLATFORM_CREDENTIAL_SECRET")
    if current is None:
        raise SecretError(
            "PLATFORM_CREDENTIAL_SECRET is not set, so encrypted values cannot "
            'be stored. Generate one with: python -c "from cryptography.fernet '
            'import Fernet; print(Fernet.generate_key().decode())"'
        )
    previous = _key(
        PLATFORM_CREDENTIAL_SECRET_PREVIOUS, "PLATFORM_CREDENTIAL_SECRET_PREVIOUS"
    )
    return MultiFernet([current] + ([previous] if previous else []))


def is_configured() -> bool:
    """Whether anything can be encrypted at all, for a screen to say so."""
    try:
        cipher()
    except SecretError:
        return False
    return True


def rotation_in_progress() -> bool:
    """Whether a previous key is still configured — i.e. step 3 is outstanding."""
    return bool(PLATFORM_CREDENTIAL_SECRET_PREVIOUS)


def _is_current(token: str) -> bool:
    """Whether this ciphertext is already under the *current* key.

    Decrypting with the current key alone, rather than inspecting the token.
    Fernet tokens carry no key id, so "which key wrote this" is only ever
    answerable by trying.
    """
    current = _key(PLATFORM_CREDENTIAL_SECRET, "PLATFORM_CREDENTIAL_SECRET")
    if current is None:
        return False
    try:
        current.decrypt(token.encode())
    except (InvalidToken, TypeError, ValueError):
        return False
    return True


#: Every table holding a Fernet token under this secret, as
#: (model, column, human name). Listed explicitly rather than discovered,
#: because a table missed by a clever scan is a table stranded on the old key
#: — and it would be found months later, by an outage.
def _targets() -> list[tuple[type, str, str]]:
    from api.db.models import (
        GoogleCalendarConnectionModel,
        OrganizationProviderCredentialModel,
        PlatformProviderCredentialModel,
        UserModel,
    )

    targets: list[tuple[type, str, str]] = [
        (PlatformProviderCredentialModel, "encrypted_key", "platform provider keys"),
        (
            OrganizationProviderCredentialModel,
            "encrypted_key",
            "customer provider keys",
        ),
        (UserModel, "mfa_secret_encrypted", "MFA secrets"),
        (GoogleCalendarConnectionModel, "encrypted_refresh_token", "calendar tokens"),
        (GoogleCalendarConnectionModel, "encrypted_access_token", "calendar tokens"),
    ]
    return targets


@dataclass(frozen=True)
class RotationStatus:
    """Where a rotation has got to, in the only terms that matter."""

    #: Rows already written under the current key. Nothing to do.
    current: int
    #: Rows still readable only under the previous key. Dropping
    #: ``…_PREVIOUS`` now would strand every one of these.
    pending: int
    #: Rows that decrypt under neither key. Already lost — the secret was
    #: changed at some point without this ever being run.
    unreadable: int
    #: Whether a previous key is configured at all.
    previous_key_configured: bool

    @property
    def safe_to_drop_previous(self) -> bool:
        return self.pending == 0

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "pending": self.pending,
            "unreadable": self.unreadable,
            "previous_key_configured": self.previous_key_configured,
            "safe_to_drop_previous": self.safe_to_drop_previous,
        }


async def status(session: AsyncSession) -> RotationStatus:
    """Count every stored ciphertext by which key can still read it.

    Read-only, and safe to call from a readiness endpoint: it decrypts in
    memory and returns counts, never plaintext.
    """
    box = cipher()
    current = pending = unreadable = 0

    for model, column, _ in _targets():
        attribute = getattr(model, column)
        rows = (
            await session.scalars(select(attribute).where(attribute.is_not(None)))
        ).all()
        for token in rows:
            if not token:
                continue
            if _is_current(token):
                current += 1
                continue
            try:
                box.decrypt(token.encode())
            except (InvalidToken, TypeError, ValueError):
                unreadable += 1
            else:
                pending += 1

    return RotationStatus(
        current=current,
        pending=pending,
        unreadable=unreadable,
        previous_key_configured=rotation_in_progress(),
    )


@dataclass(frozen=True)
class RotationResult:
    rewritten: int
    skipped: int
    unreadable: int
    by_kind: dict[str, int]


async def reencrypt_all(
    session: AsyncSession, *, dry_run: bool = False
) -> RotationResult:
    """Rewrite every stored ciphertext under the current key.

    Three properties this has to hold, and each is a way a naive loop loses
    data:

    **Re-runnable.** A row already on the current key is skipped, not
    re-encrypted, so an interrupted run is finished by running it again.

    **Never destructive.** A row that decrypts under neither key is counted and
    left exactly as it was. It is already lost, and overwriting it with
    anything — including an empty string — destroys the only evidence of what
    was there, which a support case may still need.

    **One transaction.** The caller commits. A partial rewrite is fine on its
    own terms (the run is re-runnable), but a partial rewrite that also
    half-committed is harder to reason about than one that did nothing.
    """
    box = cipher()
    rewritten = skipped = unreadable = 0
    by_kind: dict[str, int] = {}

    for model, column, name in _targets():
        attribute = getattr(model, column)
        rows = (
            await session.scalars(select(model).where(attribute.is_not(None)))
        ).all()
        for row in rows:
            token = getattr(row, column)
            if not token:
                continue
            if _is_current(token):
                skipped += 1
                continue
            try:
                plaintext = box.decrypt(token.encode())
            except (InvalidToken, TypeError, ValueError):
                # Left untouched on purpose. See the docstring.
                unreadable += 1
                logger.error(
                    "A {} row (id {}) decrypts under neither key and was left "
                    "alone. It was encrypted under a secret that is no longer "
                    "configured.",
                    name,
                    getattr(row, "id", "?"),
                )
                continue

            if not dry_run:
                setattr(row, column, box.encrypt(plaintext).decode())
            rewritten += 1
            by_kind[name] = by_kind.get(name, 0) + 1

    if not dry_run:
        await session.flush()

    logger.info(
        "Re-encryption {}: {} rewritten, {} already current, {} unreadable",
        "dry run" if dry_run else "complete",
        rewritten,
        skipped,
        unreadable,
    )
    return RotationResult(
        rewritten=rewritten,
        skipped=skipped,
        unreadable=unreadable,
        by_kind=by_kind,
    )

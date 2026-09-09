"""Do the platform keys we hold still work?

``managed_availability`` decides whether to offer a managed tier by asking
whether a key is *stored*. Its own docstring names the failure that check
exists to prevent: "the customer picks it, saves, builds an agent, and finds
out at dial time."

A revoked key produces that outcome one step further along. It is stored, it is
``is_active``, every existing check passes — and the vendor answers 401. That
happened: every managed realtime call failed on a rejected OpenAI key while the
model picker went on offering the tier, and the first to notice were inbound
callers listening to silence.

``key_validation`` already asks vendors this question on the way in, for the
customer's own BYOK keys, against the thirty-odd probes in ``check_validity``.
Nothing asked it again afterwards, and nothing ever asked it about *our* keys.
This is that: the same probes, pointed at the platform vault, on a schedule.

Two rules it is built around.

**Unverified is not invalid.** ``key_validation`` distinguishes a vendor
rejecting a key from our being unable to ask — no probe for that vendor, a
timeout, the box offline. Only a rejection is recorded. Getting this backwards
would withdraw working managed tiers every time a vendor had a bad minute,
which is a worse outage than the one this fixes.

**A verdict narrows, it never widens.** A rejection makes a tier unavailable;
an acceptance grants nothing that holding the key did not already grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PlatformProviderCredentialModel
from api.services.configuration import key_validation, platform_credentials

#: How much of the vendor's own words to keep. Enough for an operator to act
#: on, short enough that the column stays readable in a table.
MAX_ERROR_LENGTH = 400

#: What we tell an operator when the ciphertext will not open. No vendor will
#: ever report this one, so it is recorded as a rejection on our own authority.
UNDECRYPTABLE = (
    "The stored key could not be decrypted — PLATFORM_CREDENTIAL_SECRET has "
    "changed since it was saved. Re-enter the key."
)


@dataclass(frozen=True)
class CredentialCheck:
    """What one sweep learned about one stored key.

    ``ok`` is None for the keys we could not ask about. ``changed`` says
    whether this differs from the verdict we already held — the only thing an
    alert should fire on, since the check runs hourly and an outage that lasts
    a day would otherwise be twenty-four identical events with the one that
    mattered buried at the top.
    """

    component: str
    provider: str
    ok: bool | None
    changed: bool = False


def _truncate(text: str | None) -> str | None:
    if not text:
        return None
    return " ".join(text.split())[:MAX_ERROR_LENGTH]


async def validate_stored_credentials(
    session: AsyncSession,
) -> list[CredentialCheck]:
    """Check every active platform key and record what the vendor said.

    Returns one :class:`CredentialCheck` per credential, including whether the
    verdict changed, so a caller can alert on the transition rather than on
    every sweep.

    Only a definite verdict is written. An ``unverified`` outcome leaves the
    previous verdict and its timestamp untouched, so one unreachable vendor
    cannot erase a known-good result, or a known-bad one somebody is acting on.
    """
    rows = (
        await session.scalars(
            select(PlatformProviderCredentialModel).where(
                PlatformProviderCredentialModel.is_active.is_(True)
            )
        )
    ).all()

    results: list[CredentialCheck] = []
    for row in rows:
        # Read before _record overwrites it. A None here means we have never
        # had a verdict, so the first real one is a change worth reporting.
        previous = row.last_check_ok
        # Through resolve_api_key rather than decrypting here: that function
        # documents itself as the only place ciphertext is opened, and a second
        # decryption site is how that stops being true.
        key = await platform_credentials.resolve_api_key(
            session, component=row.component, provider=row.provider
        )
        if not key:
            # We are iterating rows that exist and are active, so the only way
            # to get nothing back is a key that will not decrypt. No vendor
            # will tell us about that, and it is as definite as a rejection.
            _record(row, ok=False, error=UNDECRYPTABLE)
            results.append(
                CredentialCheck(
                    row.component, row.provider, False, changed=previous is not False
                )
            )
            continue

        result = await key_validation.validate_key(row.provider, key)
        if result.outcome == "unverified":
            # Learned nothing. Say so in the log and leave the record alone.
            logger.debug(
                "Platform key {}/{} not judged: {}",
                row.component,
                row.provider,
                result.message,
            )
            # Not a change: we did not learn anything, so nothing moved.
            results.append(CredentialCheck(row.component, row.provider, None))
            continue

        ok = result.outcome == "valid"
        _record(row, ok=ok, error=None if ok else _truncate(result.message))
        results.append(
            CredentialCheck(row.component, row.provider, ok, changed=previous is not ok)
        )

        if not ok:
            logger.error(
                "Platform key for {}/{} is being rejected by the vendor: {}. "
                "Every managed call on it will fail — replace it at "
                "/superadmin/provider-keys.",
                row.component,
                row.provider,
                result.message,
            )

    await session.commit()
    return results


def _record(
    row: PlatformProviderCredentialModel, *, ok: bool, error: str | None
) -> None:
    row.last_check_ok = ok
    row.last_checked_at = datetime.now(UTC)
    row.last_check_error = error


def is_known_bad(credential: PlatformProviderCredentialModel | None) -> bool:
    """True only when a vendor has explicitly rejected this key.

    The single place the NULL rule is enforced. Never-checked and
    could-not-check both answer False, so an unproven key keeps working exactly
    as it did before this module existed.
    """
    if credential is None:
        return False
    return credential.last_check_ok is False

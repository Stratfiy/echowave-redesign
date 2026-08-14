"""Decibyl's own provider API keys.

These are what make an account "managed": a customer who does not bring their
own OpenAI or Deepgram key runs on ours, and that usage appears on their receipt
as a pass-through cost. Before this existed, "managed" resolved through an
external billing service that no longer exists, so the mode had no key source at
all.

Three rules, all of them about the fact that these keys buy inference capacity
billed to us rather than to a tenant:

1. **Encrypted at rest**, not stored as plaintext JSON alongside per-tenant
   configuration. A leak here is a leak of every customer's capacity at once.
2. **Never returned.** Endpoints expose the last four characters and nothing
   else. There is no read path for the plaintext outside the pipeline.
3. **No fallback secret.** If ``PLATFORM_CREDENTIAL_SECRET`` is unset, storing a
   key raises. A default would mean a deployment that forgot to configure it
   still appeared to work, with every key encrypted under a value published in
   the source.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import InvalidToken, MultiFernet
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PlatformProviderCredentialModel
from api.enums import CostComponent
from api.services import secret_rotation

#: Components a platform key can serve. Telephony is deliberately absent —
#: carrier credentials live on telephony_configurations, which already models
#: per-account carrier accounts and the KYC that goes with them.
CREDENTIAL_COMPONENTS = (CostComponent.STT, CostComponent.LLM, CostComponent.TTS)


class PlatformCredentialError(ValueError):
    """A credential could not be stored or read."""


def _cipher() -> MultiFernet:
    """The shared cipher, so a key rotation covers these keys too.

    Encrypts under the current secret and decrypts under either it or the one
    being rotated out — which is what lets the secret change without every
    managed call failing at the vendor until each key is re-entered by hand.
    """
    try:
        return secret_rotation.cipher()
    except secret_rotation.SecretError as exc:
        raise PlatformCredentialError(str(exc)) from exc


def encryption_is_configured() -> bool:
    """Whether keys can be stored at all, for the admin screen to say so."""
    try:
        _cipher()
    except PlatformCredentialError:
        return False
    return True


@dataclass(frozen=True)
class PlatformCredential:
    """A stored key as an operator sees it — never the key itself."""

    id: int
    component: str
    provider: str
    masked_key: str
    label: str | None
    is_active: bool
    updated_at: str | None


def _mask(last_four: str) -> str:
    return f"••••{last_four}"


def _view(row: PlatformProviderCredentialModel) -> PlatformCredential:
    return PlatformCredential(
        id=row.id,
        component=row.component,
        provider=row.provider,
        masked_key=_mask(row.key_last_four),
        label=row.label,
        is_active=bool(row.is_active),
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _normalise(component: CostComponent | str, provider: str) -> tuple[str, str]:
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    try:
        parsed = CostComponent(component_value)
    except ValueError as exc:
        raise PlatformCredentialError(f"Unknown component {component_value!r}") from exc
    if parsed not in CREDENTIAL_COMPONENTS:
        raise PlatformCredentialError(
            f"{parsed.value} keys are not held here. Telephony credentials "
            "belong on a telephony configuration."
        )

    provider = (provider or "").strip().lower()
    if not provider:
        raise PlatformCredentialError("Name the provider this key is for.")
    return parsed.value, provider


async def set_credential(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    component: CostComponent | str,
    provider: str,
    api_key: str,
    label: str | None = None,
) -> PlatformCredential:
    """Store or rotate the platform key for one provider and component.

    Rotating replaces the ciphertext in place. Unlike a rate, a key has no
    history worth keeping — the old one is being revoked at the provider, and
    retaining it would only widen what a database leak exposes.
    """
    component_value, provider = _normalise(component, provider)

    api_key = (api_key or "").strip()
    if len(api_key) < 8:
        raise PlatformCredentialError(
            "That does not look like an API key. Paste the whole value."
        )

    cipher = _cipher()
    existing = await session.scalar(
        select(PlatformProviderCredentialModel).where(
            PlatformProviderCredentialModel.component == component_value,
            PlatformProviderCredentialModel.provider == provider,
        )
    )

    if existing is None:
        existing = PlatformProviderCredentialModel(
            component=component_value, provider=provider
        )
        session.add(existing)

    existing.encrypted_key = cipher.encrypt(api_key.encode()).decode()
    existing.key_last_four = api_key[-4:]
    existing.label = label
    existing.is_active = True
    existing.set_by = actor_user_id

    await session.flush()
    logger.info(
        "Platform {} key for {} set by user {}",
        component_value,
        provider,
        actor_user_id,
    )
    return _view(existing)


async def set_active(
    session: AsyncSession,
    *,
    component: CostComponent | str,
    provider: str,
    is_active: bool,
) -> PlatformCredential:
    """Take a provider in or out of service without discarding the key."""
    component_value, provider = _normalise(component, provider)
    row = await session.scalar(
        select(PlatformProviderCredentialModel).where(
            PlatformProviderCredentialModel.component == component_value,
            PlatformProviderCredentialModel.provider == provider,
        )
    )
    if row is None:
        raise PlatformCredentialError("No key stored for that provider.")
    row.is_active = is_active
    await session.flush()
    return _view(row)


async def delete_credential(
    session: AsyncSession, *, component: CostComponent | str, provider: str
) -> None:
    component_value, provider = _normalise(component, provider)
    row = await session.scalar(
        select(PlatformProviderCredentialModel).where(
            PlatformProviderCredentialModel.component == component_value,
            PlatformProviderCredentialModel.provider == provider,
        )
    )
    if row is None:
        raise PlatformCredentialError("No key stored for that provider.")
    await session.delete(row)
    await session.flush()


async def list_credentials(session: AsyncSession) -> list[PlatformCredential]:
    """Every stored key, masked. Safe to return to a staff screen."""
    rows = (
        await session.scalars(
            select(PlatformProviderCredentialModel).order_by(
                PlatformProviderCredentialModel.component,
                PlatformProviderCredentialModel.provider,
            )
        )
    ).all()
    return [_view(r) for r in rows]


async def resolve_api_key(
    session: AsyncSession, *, component: CostComponent | str, provider: str
) -> str | None:
    """The plaintext key for the pipeline, or None if we hold none.

    The only place ciphertext is decrypted. Returns None rather than raising for
    a missing or deactivated key: the caller's job is to fall back to whatever
    the customer supplied, and a provider we do not manage is an ordinary
    situation rather than an error.

    A key that will not decrypt *is* an error, and a loud one — it means the
    secret was rotated without re-entering the keys, and every managed call on
    that provider is about to fail.
    """
    try:
        component_value, provider = _normalise(component, provider)
    except PlatformCredentialError:
        return None

    row = await session.scalar(
        select(PlatformProviderCredentialModel).where(
            PlatformProviderCredentialModel.component == component_value,
            PlatformProviderCredentialModel.provider == provider,
            PlatformProviderCredentialModel.is_active.is_(True),
        )
    )
    if row is None:
        return None

    try:
        return _cipher().decrypt(row.encrypted_key.encode()).decode()
    except (InvalidToken, PlatformCredentialError):
        logger.error(
            "Platform {} key for {} cannot be decrypted — PLATFORM_CREDENTIAL_SECRET "
            "has changed since it was stored. Re-enter the key.",
            component_value,
            provider,
        )
        return None


async def managed_providers(session: AsyncSession) -> dict[str, list[str]]:
    """Which providers we can serve, by component.

    Drives the customer-facing model picker: an account should only be offered
    "managed" for a provider we actually hold a working key for.
    """
    rows = (
        await session.scalars(
            select(PlatformProviderCredentialModel).where(
                PlatformProviderCredentialModel.is_active.is_(True)
            )
        )
    ).all()

    by_component: dict[str, list[str]] = {}
    for row in rows:
        by_component.setdefault(row.component, []).append(row.provider)
    return by_component

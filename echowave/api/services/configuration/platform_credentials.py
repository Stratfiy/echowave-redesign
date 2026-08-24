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

import re
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import PLATFORM_CREDENTIAL_SECRET
from api.db.models import PlatformProviderCredentialModel
from api.enums import CostComponent
from api.services.configuration.registry import realtime_key_provider

#: Components a platform key can serve. Telephony is deliberately absent —
#: carrier credentials live on telephony_configurations, which already models
#: per-account carrier accounts and the KYC that goes with them.
CREDENTIAL_COMPONENTS = (CostComponent.STT, CostComponent.LLM, CostComponent.TTS)


#: Three or more of any character a dashboard masks a key with. Matched as a
#: run because a single one could conceivably be legitimate; eight in a row
#: never are.
MASK_RUN = re.compile(r"[\u2022\u00b7\u2219\u25cf\u2027*]{3,}")


class PlatformCredentialError(ValueError):
    """A credential could not be stored or read."""


def _cipher() -> Fernet:
    if not PLATFORM_CREDENTIAL_SECRET:
        raise PlatformCredentialError(
            "PLATFORM_CREDENTIAL_SECRET is not set, so provider keys cannot be "
            'stored. Generate one with: python -c "from cryptography.fernet '
            'import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(PLATFORM_CREDENTIAL_SECRET.encode())
    except (ValueError, TypeError) as exc:
        raise PlatformCredentialError(
            "PLATFORM_CREDENTIAL_SECRET is not a valid Fernet key. It must be "
            "32 url-safe base64-encoded bytes."
        ) from exc


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

    # Every vendor's key is ASCII, and it travels to them in an HTTP header,
    # which cannot carry anything else. Storing a non-ASCII one succeeds, and
    # then every call on that provider dies with a UnicodeEncodeError raised
    # from inside httpx — a 500 with no message a reader can act on, arriving
    # whenever someone next uses the feature rather than here.
    #
    # The masked value gets its own message because it is the one people
    # actually paste. A vendor shows the real key once at creation and a masked
    # form ever after (``sk-proj-••••••••``), and that form is the right
    # length, carries the right prefix, and sits on screen beside a copy
    # button. A run of three is required so a lone character cannot trip it.
    if MASK_RUN.search(api_key):
        raise PlatformCredentialError(
            "That is the masked key the dashboard displays, not the key "
            "itself. Most vendors show the real value only once, when the key "
            "is created — create a new key and paste that."
        )

    if any(ord(character) > 127 for character in api_key):
        raise PlatformCredentialError(
            "That key contains characters an API key cannot hold. It usually "
            "means it was copied through something that rewrote the text — "
            "paste it straight from the vendor's dashboard."
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


async def _active_row(session: AsyncSession, component: str, provider: str):
    return await session.scalar(
        select(PlatformProviderCredentialModel).where(
            PlatformProviderCredentialModel.component == component,
            PlatformProviderCredentialModel.provider == provider,
            PlatformProviderCredentialModel.is_active.is_(True),
        )
    )


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

    row = await _active_row(session, component_value, provider)

    if row is None:
        base = realtime_key_provider(provider)
        if base is not None and base != provider:
            # A speech-to-speech provider is the same vendor account as its
            # ordinary sibling: OpenAI Realtime authenticates with your OpenAI
            # key, Gemini Live with your Google key. Requiring a separate row
            # would mean an admin pasting the same key twice and the realtime
            # tier breaking silently whenever they rotated only one — the
            # exact trap the embeddings component avoids by sharing the LLM
            # credential.
            #
            # ``realtime_key_provider`` is an explicit table, not the vendor
            # name with "_realtime" stripped off: Grok Realtime bills through
            # the xAI account, and Ultravox has no ordinary sibling to borrow
            # from at all — it maps to itself, so this branch never fires for
            # it. An exact row still wins over this fallback, so a deployment
            # that already stored one under the realtime name keeps working
            # untouched.
            row = await _active_row(session, component_value, base)
            if row is not None:
                logger.debug(
                    "Serving managed {} for {} with the stored '{}' key.",
                    component_value,
                    provider,
                    base,
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

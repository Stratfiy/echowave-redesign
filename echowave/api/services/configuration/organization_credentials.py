"""A customer's own provider API keys — the BYOK vault.

The mirror of ``platform_credentials``, scoped to one organization. Between
them they are the two places a key can come from, and the symmetry is the
point: ``resolve_api_key`` has the same signature on both, so a slot in a stack
can be flipped between managed and BYOK without anything downstream learning
which source answered.

**Why this exists rather than keys living in the configuration JSON.** They used
to be stored inline, in plaintext, inside the per-organization model
configuration. That is what forced every model screen to also be a key-entry
screen — you could not save a key without choosing a model to attach it to, and
choosing a different provider threw away the key you had just pasted. Pulling
keys out into a vault is what makes "store your keys" and "choose your models"
two separate jobs, which is the whole shape of the redesign.

The three rules from the platform vault carry over unchanged, because a
customer's key deserves at least the care we give our own:

1. **Encrypted at rest**, never plaintext JSON.
2. **Never returned.** Endpoints expose the last four characters. There is no
   read path for the plaintext outside the pipeline.
3. **No fallback secret.** Without ``PLATFORM_CREDENTIAL_SECRET`` storing a key
   raises, rather than a deployment appearing to work with every key encrypted
   under a value published in the source.

One rule is new, and it is the tenant-isolation one: **every read and write
here is filtered by ``organization_id``**. A credential is the single most
damaging row to leak across accounts, so there is no lookup by id alone
anywhere in this module — the organization is a required argument on every
function, not an optional filter.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import PLATFORM_CREDENTIAL_SECRET
from api.db.models import OrganizationProviderCredentialModel
from api.enums import CostComponent
from api.services.configuration.registry import realtime_key_provider

#: Components a customer key can serve. Telephony is absent for the same reason
#: it is absent from the platform vault: carrier credentials live on
#: telephony_configurations, with the KYC that goes with them.
CREDENTIAL_COMPONENTS = (CostComponent.STT, CostComponent.LLM, CostComponent.TTS)


class OrganizationCredentialError(ValueError):
    """A credential could not be stored or read."""


def _cipher() -> Fernet:
    if not PLATFORM_CREDENTIAL_SECRET:
        raise OrganizationCredentialError(
            "PLATFORM_CREDENTIAL_SECRET is not set, so provider keys cannot be "
            "stored. Ask your administrator to configure it."
        )
    try:
        return Fernet(PLATFORM_CREDENTIAL_SECRET.encode())
    except (ValueError, TypeError) as exc:
        raise OrganizationCredentialError(
            "PLATFORM_CREDENTIAL_SECRET is not a valid Fernet key. It must be "
            "32 url-safe base64-encoded bytes."
        ) from exc


def encryption_is_configured() -> bool:
    """Whether keys can be stored at all, so the screen can say so plainly."""
    try:
        _cipher()
    except OrganizationCredentialError:
        return False
    return True


@dataclass(frozen=True)
class OrganizationCredential:
    """A stored key as its owner sees it — never the key itself."""

    id: int
    component: str
    provider: str
    masked_key: str
    label: str | None
    is_active: bool
    updated_at: str | None


def _mask(last_four: str) -> str:
    return f"••••{last_four}"


def _view(row: OrganizationProviderCredentialModel) -> OrganizationCredential:
    return OrganizationCredential(
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
        raise OrganizationCredentialError(
            f"Unknown component {component_value!r}"
        ) from exc
    if parsed not in CREDENTIAL_COMPONENTS:
        raise OrganizationCredentialError(
            f"{parsed.value} keys are not held here. Telephony credentials "
            "belong on a telephony configuration."
        )

    provider = (provider or "").strip().lower()
    if not provider:
        raise OrganizationCredentialError("Name the provider this key is for.")
    if provider == "decibyl":
        # "decibyl" is how a slot says *managed* — it names our key, not one the
        # customer holds. Storing a key under that name would create a BYOK
        # credential that shadows the managed path and make the two sources
        # indistinguishable at resolution time.
        raise OrganizationCredentialError(
            "Decibyl is the managed option and needs no key from you. Choose "
            "the vendor whose key this is."
        )
    return parsed.value, provider


async def _active_row(
    session: AsyncSession, organization_id: int, component: str, provider: str
) -> OrganizationProviderCredentialModel | None:
    return await session.scalar(
        select(OrganizationProviderCredentialModel).where(
            OrganizationProviderCredentialModel.organization_id == organization_id,
            OrganizationProviderCredentialModel.component == component,
            OrganizationProviderCredentialModel.provider == provider,
            OrganizationProviderCredentialModel.is_active.is_(True),
        )
    )


async def set_credential(
    session: AsyncSession,
    *,
    organization_id: int,
    actor_user_id: int | None,
    component: CostComponent | str,
    provider: str,
    api_key: str,
    label: str | None = None,
) -> OrganizationCredential:
    """Store or rotate this account's key for one provider and component.

    Rotating replaces the ciphertext in place. A key has no history worth
    keeping — the old one is being revoked at the provider, and retaining it
    would only widen what a database leak exposes.
    """
    component_value, provider = _normalise(component, provider)

    api_key = (api_key or "").strip()
    if len(api_key) < 8:
        raise OrganizationCredentialError(
            "That does not look like an API key. Paste the whole value."
        )

    cipher = _cipher()

    def _write(row: OrganizationProviderCredentialModel) -> None:
        row.encrypted_key = cipher.encrypt(api_key.encode()).decode()
        row.key_last_four = api_key[-4:]
        row.label = label
        row.is_active = True
        row.set_by = actor_user_id

    async def _find() -> OrganizationProviderCredentialModel | None:
        return await session.scalar(
            select(OrganizationProviderCredentialModel).where(
                OrganizationProviderCredentialModel.organization_id == organization_id,
                OrganizationProviderCredentialModel.component == component_value,
                OrganizationProviderCredentialModel.provider == provider,
            )
        )

    existing = await _find()

    if existing is None:
        # Read-then-insert is a race against the unique constraint. Two saves
        # landing together — a double-click is enough — both read nothing and
        # both insert; one wins and the other takes a duplicate-key error that
        # would reach the customer as a 500 on a screen where they did nothing
        # wrong.
        #
        # The insert goes inside a SAVEPOINT so losing the race costs only the
        # savepoint. Without one the IntegrityError poisons the surrounding
        # transaction, and recovering would mean discarding whatever else the
        # caller had already done in it.
        candidate = OrganizationProviderCredentialModel(
            organization_id=organization_id,
            component=component_value,
            provider=provider,
        )
        _write(candidate)
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            existing = candidate
        except IntegrityError:
            # The other writer got there first. Rotating onto its row is the
            # right outcome: both callers asked for this key to be the stored
            # one, and last-write-wins is what a serial run would have done.
            existing = await _find()
            if existing is None:
                raise

    _write(existing)
    await session.flush()

    # The key itself is never logged, and neither is its last four: this line
    # exists to answer "who changed what, when", not to identify the secret.
    logger.info(
        "Organization {} {} key for {} set by user {}",
        organization_id,
        component_value,
        provider,
        actor_user_id,
    )
    return _view(existing)


async def set_active(
    session: AsyncSession,
    *,
    organization_id: int,
    component: CostComponent | str,
    provider: str,
    is_active: bool,
) -> OrganizationCredential:
    """Take a provider in or out of service without discarding the key."""
    component_value, provider = _normalise(component, provider)
    row = await session.scalar(
        select(OrganizationProviderCredentialModel).where(
            OrganizationProviderCredentialModel.organization_id == organization_id,
            OrganizationProviderCredentialModel.component == component_value,
            OrganizationProviderCredentialModel.provider == provider,
        )
    )
    if row is None:
        raise OrganizationCredentialError("No key stored for that provider.")
    row.is_active = is_active
    await session.flush()
    return _view(row)


async def delete_credential(
    session: AsyncSession,
    *,
    organization_id: int,
    component: CostComponent | str,
    provider: str,
) -> None:
    component_value, provider = _normalise(component, provider)
    row = await session.scalar(
        select(OrganizationProviderCredentialModel).where(
            OrganizationProviderCredentialModel.organization_id == organization_id,
            OrganizationProviderCredentialModel.component == component_value,
            OrganizationProviderCredentialModel.provider == provider,
        )
    )
    if row is None:
        raise OrganizationCredentialError("No key stored for that provider.")
    await session.delete(row)
    await session.flush()


async def list_credentials(
    session: AsyncSession, *, organization_id: int
) -> list[OrganizationCredential]:
    """Every key this account has stored, masked."""
    rows = (
        await session.scalars(
            select(OrganizationProviderCredentialModel)
            .where(
                OrganizationProviderCredentialModel.organization_id == organization_id
            )
            .order_by(
                OrganizationProviderCredentialModel.component,
                OrganizationProviderCredentialModel.provider,
            )
        )
    ).all()
    return [_view(r) for r in rows]


async def resolve_api_key(
    session: AsyncSession,
    *,
    organization_id: int,
    component: CostComponent | str,
    provider: str,
) -> str | None:
    """The plaintext key for the pipeline, or None if this account holds none.

    The only place ciphertext is decrypted. Returns None rather than raising for
    a missing or deactivated key, because the caller's job is to decide what to
    do without one — usually to report that the slot is not runnable — and a
    provider the customer has not brought a key for is an ordinary situation
    rather than an error.

    A key that will not decrypt *is* an error, and a loud one: it means
    PLATFORM_CREDENTIAL_SECRET was rotated without the keys being re-entered,
    and every call on that provider is about to fail.
    """
    try:
        component_value, provider = _normalise(component, provider)
    except OrganizationCredentialError:
        return None

    row = await _active_row(session, organization_id, component_value, provider)

    if row is None:
        base = realtime_key_provider(provider)
        if base is not None and base != provider:
            # A speech-to-speech provider is the same vendor account as its
            # ordinary sibling: an account that has already brought its own
            # OpenAI key does not need to bring a second one under the name
            # "openai_realtime" to use OpenAI Realtime — see the identical
            # fallback in ``platform_credentials.resolve_api_key``, which this
            # mirrors on purpose. Without it, a customer with a working OpenAI
            # key still had their speech-to-speech call refused, or silently
            # billed at the managed rate if they had opted into that fallback
            # — for a key they already held.
            row = await _active_row(session, organization_id, component_value, base)
            if row is not None:
                logger.debug(
                    "Resolved {} key for {} from this account's '{}' key.",
                    component_value,
                    provider,
                    base,
                )

    if row is None:
        return None

    try:
        return _cipher().decrypt(row.encrypted_key.encode()).decode()
    except (InvalidToken, OrganizationCredentialError):
        logger.error(
            "Organization {} {} key for {} cannot be decrypted — "
            "PLATFORM_CREDENTIAL_SECRET has changed since it was stored. "
            "The key must be re-entered.",
            organization_id,
            component_value,
            provider,
        )
        return None


async def available_providers(
    session: AsyncSession, *, organization_id: int
) -> dict[str, list[str]]:
    """Which providers this account can run BYOK on, by component.

    Drives the model picker: a slot should only offer "my own key" for a
    provider the account actually holds a working key for. Offering the choice
    and failing at dial time is the version of this that wastes a call.
    """
    rows = (
        await session.scalars(
            select(OrganizationProviderCredentialModel).where(
                OrganizationProviderCredentialModel.organization_id == organization_id,
                OrganizationProviderCredentialModel.is_active.is_(True),
            )
        )
    ).all()

    by_component: dict[str, list[str]] = {}
    for row in rows:
        by_component.setdefault(row.component, []).append(row.provider)
    for providers in by_component.values():
        providers.sort()
    return by_component

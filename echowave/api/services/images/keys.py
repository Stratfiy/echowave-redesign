"""Which provider a workspace makes images with, and the key it uses.

The choice is one row in ``organization_configurations``
(``IMAGE_GENERATION``: ``{"provider": "google"}``). The key is never stored
here: it lives in the BYOK vault (``organization_credentials``, component
``image``), encrypted at rest under ``PLATFORM_CREDENTIAL_SECRET``, and only
its last four characters ever leave the server. Every function takes the
organisation as a required argument; there is no lookup by anything else.

Where a key comes from, in order:

1. the workspace's own image key for that provider;
2. its own model key for the same vendor (a Gemini or OpenAI key already
   added under Settings -> Models is the same vendor account, and asking
   for it twice would be asking the person to do our lookup);
3. the platform's key, when one is configured (``registry.platform_key``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.configuration import organization_credentials as creds
from api.services.images import registry

CONFIG_KEY = OrganizationConfigurationKey.IMAGE_GENERATION.value
IMAGE = creds.IMAGE_COMPONENT.value
#: Vendors whose model key also makes images.
_SAME_ACCOUNT_AS_LLM = frozenset({"google", "openai"})


class ImageKeyError(ValueError):
    """A key could not be connected; the message is for the person."""


@dataclass(frozen=True)
class Resolved:
    provider: str
    #: None only for Bedrock on the platform's own role.
    api_key: str | None
    #: ``byok`` or ``platform``.
    key_source: str


async def chosen(organization_id: int) -> str | None:
    value = await db_client.get_configuration_value(organization_id, CONFIG_KEY, None)
    if isinstance(value, dict):
        name = str(value.get("provider") or "").strip().lower()
        if name in registry.PROVIDERS:
            return name
        if name:
            # A provider since removed: said, not silently treated as none.
            logger.warning(
                "Organization {} chose image provider {!r}, which is not offered",
                organization_id,
                name,
            )
    return None


async def choose(organization_id: int, provider: str) -> None:
    if provider not in registry.PROVIDERS:
        raise ImageKeyError(f"{provider} does not make images here.")
    await db_client.upsert_configuration(
        organization_id, CONFIG_KEY, {"provider": provider}
    )


async def _own_key(organization_id: int, provider: str) -> str | None:
    async with db_client.async_session() as session:
        key = await creds.resolve_api_key(
            session, organization_id=organization_id, component=IMAGE, provider=provider
        )
        if key is None and provider in _SAME_ACCOUNT_AS_LLM:
            key = await creds.resolve_api_key(
                session,
                organization_id=organization_id,
                component="llm",
                provider=provider,
            )
    return key


async def resolve(organization_id: int, provider: str | None = None) -> Resolved | None:
    """The key ``provider`` (or the chosen one) runs on here, or None when
    there is nothing to run it on."""
    name = provider or await chosen(organization_id)
    if not name or name not in registry.PROVIDERS:
        return None
    own = await _own_key(organization_id, name)
    if own:
        return Resolved(provider=name, api_key=own, key_source="byok")
    platform = registry.platform_key(name)
    if platform is not None:
        return Resolved(provider=name, api_key=platform or None, key_source="platform")
    return None


async def _masked(organization_id: int) -> dict[str, str]:
    async with db_client.async_session() as session:
        stored = await creds.list_credentials(session, organization_id=organization_id)
    out: dict[str, str] = {}
    for credential in stored:
        if not credential.is_active:
            continue
        if credential.component == IMAGE or (
            credential.component == "llm"
            and credential.provider in _SAME_ACCOUNT_AS_LLM
            and credential.provider not in out
        ):
            out[credential.provider] = credential.masked_key
    return out


async def status(organization_id: int) -> dict[str, Any]:
    """What the card and Settings show: each provider, whether it is ready
    and on whose key, and which one is chosen. Never a key."""
    masked = await _masked(organization_id)
    current = await chosen(organization_id)
    providers = []
    for name in registry.names():
        info = registry.INFO[name]
        own = masked.get(name)
        platform = registry.platform_ready(name)
        providers.append(
            {
                "provider": name,
                "label": info.label,
                "blurb": info.blurb,
                "key_label": info.key_label,
                "key_hint": info.key_hint,
                "ready": bool(own) or platform,
                "source": "your_key" if own else ("platform" if platform else None),
                "masked_key": own,
                "takes_references": registry.PROVIDERS[name].takes_references,
            }
        )
    return {
        "chosen": current,
        "ready": any(p["ready"] for p in providers if p["provider"] == current),
        "providers": providers,
        "encryption_configured": creds.encryption_is_configured(),
    }


async def connect(
    organization_id: int,
    *,
    user_id: int | None,
    provider: str,
    api_key: str | None,
    verify: bool = True,
) -> dict[str, Any]:
    """Choose ``provider``, storing ``api_key`` for it first if one is given.

    A key the vendor refuses is not stored; a vendor we could not reach
    stores it and says so, the same rule the model keys follow. Without a
    key the provider must already be ready (the platform's key, or one this
    workspace added before).
    """
    provider = (provider or "").strip().lower()
    implementation = registry.get(provider)
    if implementation is None:
        raise ImageKeyError("Choose Gemini, OpenAI or Amazon Bedrock.")
    verification = "existing"
    message = ""
    key = (api_key or "").strip()
    if key:
        check = await implementation.check_key(key) if verify else None
        if check is not None and not check.may_store:
            raise ImageKeyError(check.message)
        async with db_client.async_session() as session:
            try:
                await creds.set_credential(
                    session,
                    organization_id=organization_id,
                    actor_user_id=user_id,
                    component=IMAGE,
                    provider=provider,
                    api_key=key,
                    label="Images",
                )
            except creds.OrganizationCredentialError as exc:
                raise ImageKeyError(str(exc)) from exc
            await session.commit()
        verification = check.outcome if check else "unverified"
        message = check.message if check else "Stored without checking."
    elif await resolve(organization_id, provider) is None:
        raise ImageKeyError(
            f"Paste your {registry.INFO[provider].key_label} to use "
            f"{registry.INFO[provider].label}."
        )
    await choose(organization_id, provider)
    logger.info(
        "Organization {} makes images with {} (set by user {})",
        organization_id,
        provider,
        user_id,
    )
    return {
        **(await status(organization_id)),
        "verification": verification,
        "verification_message": message,
    }

"""Turn a "Decibyl" section into a real provider running on our key.

A customer who picks ``decibyl`` has chosen not to hold an API key. By the time
the pipeline builds services, that choice has to have become a concrete vendor,
a concrete model and a key that authenticates to it — otherwise every managed
call reaches a provider branch with nothing to authenticate with.

This runs once, where the effective configuration is assembled, and rewrites the
section in place. Everything downstream then sees an ordinary configured
provider and needs no knowledge that a managed tier was involved. That is the
point: the factory has one code path per vendor, not two.

**A missing platform key leaves the section alone.** The call then fails in the
provider branch with that vendor's own error rather than here with a generic
one, and the log line below is what explains it. Silently falling back to a
different vendor would be worse — a customer who bought Indic speech would get
something else and hear about it from their own users.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.enums import CostComponent
from api.services.configuration import managed_tiers, platform_credentials
from api.services.configuration.registry import ServiceProviders

#: Sections that can be served by a managed tier. Telephony is absent for the
#: same reason it is absent from platform credentials: carrier accounts carry
#: their own KYC and live on telephony_configurations.
MANAGED_SECTIONS: tuple[tuple[str, CostComponent], ...] = (
    ("stt", CostComponent.STT),
    ("llm", CostComponent.LLM),
    ("tts", CostComponent.TTS),
)


def _is_managed(section) -> bool:
    return (
        section is not None
        and getattr(section, "provider", None) == ServiceProviders.DECIBYL.value
    )


async def apply(effective) -> None:
    """Rewrite every managed section in place to a real provider and key.

    Mutates rather than returning a copy because the caller already owns the
    object and every consumer reads it by attribute; threading a second config
    through the pipeline would leave two representations of the same thing and
    an obvious way to use the wrong one.
    """
    managed = [
        (name, component)
        for name, component in MANAGED_SECTIONS
        if _is_managed(getattr(effective, name, None))
    ]
    if not managed:
        return

    async with db_client.async_session() as session:
        for name, component in managed:
            section = getattr(effective, name)
            # The customer's "model" on a managed section is the tier they
            # picked — "fast", "accurate" — not a vendor model name.
            upstream = managed_tiers.resolve(component, getattr(section, "model", None))

            api_key = await platform_credentials.resolve_api_key(
                session, component=component, provider=upstream.provider
            )
            if not api_key:
                logger.error(
                    "Managed {} requested but no platform key is stored for {}. "
                    "Add one at /superadmin/provider-keys — this call will fail "
                    "in the {} branch with that vendor's own error.",
                    name,
                    upstream.provider,
                    upstream.provider,
                )
                continue

            section.provider = upstream.provider
            section.model = upstream.model
            section.api_key = api_key
            logger.info(
                "Managed {} resolved to {}/{} on a platform key.",
                name,
                upstream.provider,
                upstream.model,
            )


async def missing_platform_keys() -> list[tuple[str, str]]:
    """``(component, provider)`` pairs a managed tier needs but we do not hold.

    Every one of these is a managed customer whose calls fail at dial time, so
    it is worth surfacing on the readiness check rather than discovering it from
    a support ticket.
    """
    missing = []
    async with db_client.async_session() as session:
        for component, provider in sorted(managed_tiers.upstream_providers()):
            key = await platform_credentials.resolve_api_key(
                session, component=component, provider=provider
            )
            if not key:
                missing.append((component, provider))
    return missing

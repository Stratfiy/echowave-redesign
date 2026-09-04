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
from api.services.configuration import (
    managed_language,
    managed_tiers,
    platform_credentials,
)
from api.services.configuration.registry import ServiceProviders

#: Sections that can be served by a managed tier. Telephony is absent for the
#: same reason it is absent from platform credentials: carrier accounts carry
#: their own KYC and live on telephony_configurations.
MANAGED_SECTIONS: tuple[tuple[str, CostComponent | str], ...] = (
    ("stt", CostComponent.STT),
    ("llm", CostComponent.LLM),
    ("tts", CostComponent.TTS),
    # Speech-to-speech. Metered as LLM usage because that is what the vendor
    # charges it as, so it authenticates with the LLM credential — see
    # _credential_component below.
    ("realtime", managed_tiers.REALTIME_COMPONENT),
    # Emitted by every managed configuration for knowledge-base retrieval.
    # Until this was here the section kept ``provider=decibyl`` all the way to
    # the embeddings factory, which is what put the second "Invalid
    # ServiceProviders.DECIBYL API key" on the model-override screen.
    ("embeddings", managed_tiers.EMBEDDINGS_COMPONENT),
)


def _credential_component(component: CostComponent | str) -> CostComponent:
    """Which stored platform credential authenticates this component.

    Embeddings have no credential slot of their own, and should not get one: a
    vendor issues **one** key that serves chat and embeddings alike, so a
    separate slot would mean an admin pasting the same Google key twice and
    the managed pipeline breaking whenever they updated only one of them.
    """
    if component in (
        managed_tiers.EMBEDDINGS_COMPONENT,
        # A realtime model is a language model that happens to speak. The
        # vendor issues one key for both, and the rate card meters it as LLM.
        managed_tiers.REALTIME_COMPONENT,
    ):
        return CostComponent.LLM
    return component  # type: ignore[return-value]


def _is_managed(section) -> bool:
    return section is not None and section.is_managed


def _provider_value(section) -> str:
    provider = getattr(section, "provider", None)
    return provider.value if hasattr(provider, "value") else str(provider or "")


async def apply(effective) -> None:
    """Rewrite every managed section in place to a real provider and key.

    Two paths converge here, both ending at the same "real vendor, real
    model, our key" shape:

    * ``provider="decibyl"`` — the tier path. ``model`` is a tier name
      ("fast", "accurate", ...), translated to a vendor+model by
      ``managed_tiers``. This is the only shape a saved configuration could
      have before ``use_platform_key`` existed, so it stays exactly as it was.
    * a real provider with ``use_platform_key=True`` — the direct path. The
      customer already picked the exact vendor and model, the same choice
      BYOK offers; there is no tier to translate, only a key to source from
      the platform vault instead of the account's.

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
            is_tier = _provider_value(section) == ServiceProviders.DECIBYL.value

            if is_tier:
                # The customer's "model" on a managed section is the tier
                # they picked — "fast", "accurate" — not a vendor model name.
                upstream = managed_tiers.resolve(
                    component, getattr(section, "model", None)
                )
                upstream_provider, upstream_model = upstream.provider, upstream.model
            else:
                upstream_provider = _provider_value(section)
                upstream_model = getattr(section, "model", None) or ""

            api_key = await platform_credentials.resolve_api_key(
                session,
                component=_credential_component(component),
                provider=upstream_provider,
            )
            if not api_key:
                logger.error(
                    "Managed {} requested but no platform key is stored for {}. "
                    "Add one at /superadmin/provider-keys — this call will fail "
                    "in the {} branch with that vendor's own error.",
                    name,
                    upstream_provider,
                    upstream_provider,
                )
                continue

            section.provider = upstream_provider
            section.model = upstream_model
            section.api_key = api_key

            # A customer-pointed endpoint exists so BYOK can run their own key
            # against a proxy or self-hosted OpenAI-compatible server -- their
            # key, their choice of destination. On a platform-key section that
            # destination would receive *our* key instead of theirs, so any
            # override is discarded back to the vendor's real endpoint rather
            # than trusted. This is not reachable through the tier classes
            # (they carry no such field), only through the direct path, but it
            # is cheap enough to apply unconditionally rather than branch on it.
            for endpoint_field in ("base_url", "endpoint"):
                if not hasattr(section, endpoint_field):
                    continue
                field_info = type(section).model_fields.get(endpoint_field)
                default = field_info.default if field_info is not None else ""
                if getattr(section, endpoint_field) != default:
                    logger.warning(
                        "Managed {} ({}) had a custom {} set while running on "
                        "Decibyl's key; ignoring it for the vendor's real "
                        "endpoint. A customer-chosen endpoint must never "
                        "receive our platform key.",
                        name,
                        upstream_provider,
                        endpoint_field,
                    )
                setattr(section, endpoint_field, default)

            # The customer's language is in Decibyl's vocabulary on the tier
            # path, which is not every vendor's, so it needs translating here
            # — by the time the factory sees it, it looks like any other
            # Sarvam config a customer typed themselves. On the direct path
            # the customer picked a real provider's own form (the same one
            # BYOK renders), so whatever language they chose is already in
            # that vendor's native vocabulary and must be left alone.
            if is_tier and hasattr(section, "language"):
                original = getattr(section, "language", None)
                if name == "stt":
                    section.language = managed_language.for_stt(
                        upstream_provider, upstream_model, original
                    )
                elif name == "tts":
                    section.language = managed_language.for_tts(
                        upstream_provider, upstream_model, original
                    )
                if section.language != original:
                    logger.debug(
                        "Managed {} language {!r} translated to {!r} for {}.",
                        name,
                        original,
                        section.language,
                        upstream_provider,
                    )

            logger.info(
                "Managed {} resolved to {}/{} on a platform key.",
                name,
                upstream_provider,
                upstream_model,
            )


async def managed_availability(session) -> dict[str, bool]:
    """Whether each section can be offered as managed right now.

    A slot is available when we hold an active platform key for whatever
    provider its default tier resolves to. This is what lets the customer's
    model picker offer "managed" only where it actually works, and show the
    rest as not yet available.

    Offering a managed slot we hold no key for is the worst of the three
    outcomes: the customer picks it, saves, builds an agent, and finds out at
    dial time. Showing it as unavailable costs a signup nothing and is honest.

    Takes a session rather than opening one, because the caller is a request
    handler that already has one and this is one query per section.
    """
    available: dict[str, bool] = {}
    for name, component in MANAGED_SECTIONS:
        upstream = managed_tiers.resolve(component, "default")
        key = await platform_credentials.resolve_api_key(
            session,
            component=_credential_component(component),
            provider=upstream.provider,
        )
        available[name] = bool(key)
    return available


async def tier_availability(session) -> dict[str, dict[str, bool]]:
    """Per **tier**, not just per section: can we actually serve this one?

    ``managed_availability`` above asks the same question of each section's
    ``default`` tier only, which is the right answer for "may this slot be set
    to managed at all" and the wrong one for "may this customer buy this
    bundle". A bundle names a specific tier, and a tier that resolves to a
    vendor we hold no key for is a phone call that fails after it connects --
    see this module's own opening note.

    That gap was live: the Natural bundle resolves realtime to
    ``google_realtime``, no Google key was stored, and the wizard offered it
    anyway because the ``default`` realtime tier (OpenAI) was keyed and that is
    all anyone asked about.

    Keyed section -> tier -> whether a key exists for what it resolves to.
    """
    out: dict[str, dict[str, bool]] = {}
    # One resolve+lookup per tier. Small and bounded: three sections with a
    # handful of tiers each, on a screen that already opens a session.
    for name, component in MANAGED_SECTIONS:
        tiers = managed_tiers.tiers_for(component)
        if not tiers:
            continue
        answers: dict[str, bool] = {}
        for tier in tiers:
            upstream = managed_tiers.resolve(component, tier)
            key = await platform_credentials.resolve_api_key(
                session,
                component=_credential_component(component),
                provider=upstream.provider,
            )
            answers[tier] = bool(key)
        out[name] = answers
    return out


async def platform_provider_catalog(session) -> dict[str, list[str]]:
    """Which real providers each section can run on ``use_platform_key`` right
    now — the direct-path counterpart to ``managed_availability``'s per-tier
    answer.

    Keyed by section name, not credential component: ``realtime`` and
    ``embeddings`` both authenticate against the LLM credential slot (see
    ``_credential_component``), so both get the same provider list as
    ``llm`` here even though there is only one underlying set of rows.
    """
    by_component = await platform_credentials.managed_providers(session)
    catalog: dict[str, list[str]] = {}
    for name, component in MANAGED_SECTIONS:
        credential_component = _credential_component(component)
        key = (
            credential_component.value
            if hasattr(credential_component, "value")
            else str(credential_component)
        )
        catalog[name] = by_component.get(key, [])
    return catalog


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
                session,
                component=_credential_component(component),
                provider=provider,
            )
            if not key:
                missing.append((component, provider))
    return missing

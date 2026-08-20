"""Changing which vendor serves a managed tier, safely.

The mapping itself lives in ``managed_tiers``; this is the write side, and it
exists mostly to refuse changes that would break calls.

A tier is not a free-text field. Pointing one at a provider the service factory
cannot build, or at a model the vendor does not serve, does not fail here — it
fails at session open, after a customer's call has already connected, on every
call until somebody notices. So the checks below run before the row is written,
and each one corresponds to a way a call dies:

* **an unbuildable provider** — the factory has no branch for it, so the
  pipeline raises mid-setup.
* **a provider we hold no platform key for** — managed resolution finds no
  credential, logs, and leaves the section unresolved. The call runs on the
  gateway path or not at all.
* **a model with no rate row** — the call works and bills nothing for that
  component, reporting margin we did not earn. Warned rather than refused: a
  vendor's new model routinely lands before anybody prices it, and refusing
  would make the price book a gate on shipping.

The first two refuse. The third warns, and the warning is returned so the
screen can show it beside the save rather than burying it in a log.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ManagedTierMappingModel
from api.services.configuration import managed_tiers


class TierMappingError(ValueError):
    """A tier change that would break calls."""


@dataclass(frozen=True)
class TierView:
    """One tier, and everything an operator needs to decide about it."""

    component: str
    tier: str
    label: str
    blurb: str
    provider: str
    model: str
    #: True when this came from the table rather than the compiled default.
    is_override: bool
    #: An environment variable set for this tier, which the table now outranks.
    #: Surfaced so the precedence is visible instead of surprising.
    env_override: str | None
    #: True when the resolved model has a rate row. False means calls on this
    #: tier report zero provider cost.
    is_priced: bool


def _known_components() -> dict[str, tuple[str, ...]]:
    return {
        "llm": managed_tiers.LLM_TIERS,
        "stt": managed_tiers.STT_TIERS,
        "tts": managed_tiers.TTS_TIERS,
        managed_tiers.REALTIME_COMPONENT: managed_tiers.REALTIME_TIERS,
        managed_tiers.EMBEDDINGS_COMPONENT: managed_tiers.EMBEDDINGS_TIERS,
    }


def _labels(component: str, tier: str) -> tuple[str, str]:
    if component == "llm" and tier in managed_tiers.LLM_TIER_LABELS:
        return managed_tiers.LLM_TIER_LABELS[tier]
    if component == managed_tiers.REALTIME_COMPONENT and tier in (
        managed_tiers.REALTIME_TIER_LABELS
    ):
        return managed_tiers.REALTIME_TIER_LABELS[tier]
    return (tier.title(), "")


def _provider_is_buildable(component: str, provider: str) -> bool:
    """Whether the service factory has a branch for this provider.

    Read from the registry rather than a list kept here, so a provider added to
    the platform is selectable the day it lands rather than the day somebody
    remembers this file.
    """
    from api.services.configuration.registry import REGISTRY, ServiceType

    service_type = {
        "llm": ServiceType.LLM,
        "stt": ServiceType.STT,
        "tts": ServiceType.TTS,
        managed_tiers.REALTIME_COMPONENT: ServiceType.REALTIME,
        managed_tiers.EMBEDDINGS_COMPONENT: ServiceType.EMBEDDINGS,
    }.get(component)
    if service_type is None:
        return False
    return any(key.value == provider for key in REGISTRY.get(service_type, {}))


async def _model_is_priced(
    session: AsyncSession, *, component: str, provider: str, model: str
) -> bool:
    """Whether a call on this tier would record a provider cost.

    Embeddings and speech-to-speech are not billed as their own component —
    embeddings run at ingest, and a realtime session meters as LLM usage — so
    those are checked against the component the rate card actually prices.
    """
    from datetime import UTC, datetime

    from api.enums import CostComponent
    from api.services.billing.rates import resolve_provider_rate
    from api.services.billing.usage import provider_from_processor

    if component == managed_tiers.EMBEDDINGS_COMPONENT:
        return True  # Not a billed component; nothing to price.

    billed = "llm" if component == managed_tiers.REALTIME_COMPONENT else component
    # The rate card is keyed by the name the pipeline records, which is derived
    # from the service class rather than the configuration vocabulary.
    rate = await resolve_provider_rate(
        session,
        provider=provider_from_processor(provider),
        component=CostComponent(billed),
        at=datetime.now(UTC),
        model=model,
    )
    return rate is not None


async def list_tiers(session: AsyncSession) -> list[TierView]:
    """Every tier, what serves it now, and whether that is safe."""
    overrides = {
        (row.component, row.tier): row
        for row in (await session.scalars(select(ManagedTierMappingModel))).all()
    }

    views: list[TierView] = []
    for component, tiers in _known_components().items():
        for tier in tiers:
            upstream = managed_tiers.resolve(component, tier)
            label, blurb = _labels(component, tier)
            views.append(
                TierView(
                    component=component,
                    tier=tier,
                    label=label,
                    blurb=blurb,
                    provider=upstream.provider,
                    model=upstream.model,
                    is_override=(component, tier) in overrides,
                    env_override=managed_tiers.env_override(component, tier),
                    is_priced=await _model_is_priced(
                        session,
                        component=component,
                        provider=upstream.provider,
                        model=upstream.model,
                    ),
                )
            )
    return views


async def set_tier(
    session: AsyncSession,
    *,
    component: str,
    tier: str,
    provider: str,
    model: str,
    actor_user_id: int | None,
) -> dict:
    """Point a tier at a vendor and model. Returns any warning worth showing."""
    component = (component or "").strip().lower()
    tier = (tier or "").strip().lower()
    provider = (provider or "").strip()
    model = (model or "").strip()

    known = _known_components()
    if component not in known:
        raise TierMappingError(
            f"{component!r} is not a component. One of: {sorted(known)}."
        )
    if tier not in known[component]:
        raise TierMappingError(
            f"{tier!r} is not a {component} tier. One of: {list(known[component])}."
        )
    if not provider or not model:
        raise TierMappingError("Name both the provider and the model.")

    if not _provider_is_buildable(component, provider):
        raise TierMappingError(
            f"Nothing can build {provider!r} for {component}. The call would "
            "fail while the pipeline was being set up."
        )

    from api.services.configuration.platform_credentials import resolve_api_key

    # Checked against the component the key is filed under, not the provider
    # alone — a vendor can be managed for speech and not for language models,
    # and a tier pointed at the half we hold no key for fails at dial time.
    if not await resolve_api_key(session, component=component, provider=provider):
        raise TierMappingError(
            f"No platform key is stored for {provider!r}. Add it under provider "
            "keys first — without one, managed calls on this tier cannot "
            "authenticate."
        )

    row = await session.scalar(
        select(ManagedTierMappingModel).where(
            ManagedTierMappingModel.component == component,
            ManagedTierMappingModel.tier == tier,
        )
    )
    if row is None:
        row = ManagedTierMappingModel(component=component, tier=tier)
        session.add(row)
    row.provider = provider
    row.model = model
    row.updated_by = actor_user_id
    await session.flush()

    priced = await _model_is_priced(
        session, component=component, provider=provider, model=model
    )
    if not priced:
        logger.warning(
            "Managed tier {}:{} now points at {}/{}, which has no rate row. "
            "Calls on it will report zero provider cost.",
            component,
            tier,
            provider,
            model,
        )

    return {
        "component": component,
        "tier": tier,
        "provider": provider,
        "model": model,
        # Not an error. A vendor's new model routinely lands before anybody
        # prices it, and blocking the change would make the price book a gate
        # on shipping — but margin is wrong until a rate is set, so it is said
        # out loud where the change was made.
        "warning": (
            None
            if priced
            else (
                f"No rate on file for {provider}/{model}. Calls on this tier "
                "will report zero provider cost, so margin will read high "
                "until a rate is added."
            )
        ),
    }


async def clear_tier(session: AsyncSession, *, component: str, tier: str) -> None:
    """Drop back to the compiled default for this tier."""
    await session.execute(
        delete(ManagedTierMappingModel).where(
            ManagedTierMappingModel.component == component.strip().lower(),
            ManagedTierMappingModel.tier == tier.strip().lower(),
        )
    )

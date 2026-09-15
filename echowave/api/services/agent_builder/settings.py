"""Which model the builder runs on, decided by which key is installed.

There is deliberately no "choose your builder model" setting. The choice is
made by installing a key at ``/superadmin/provider-keys``: whichever of
Anthropic, OpenAI or Google has an active platform LLM credential is the one
the builder uses, in preference order. One fact, one place, and no way for a
dropdown to name a provider whose key was removed last week.

``AGENT_BUILDER_PROVIDER`` pins one explicitly for the case where several keys
are installed and the preference order is not what is wanted.

The key itself never leaves this module's return value. It is fetched per turn
rather than cached, so rotating a key at the superadmin screen takes effect on
the next message instead of on the next deploy.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.enums import CostComponent
from api.services.agent_builder.client import SUPPORTED_PROVIDERS
from api.services.configuration import platform_credentials


class BuilderUnavailable(RuntimeError):
    """The builder cannot run — disabled, or no usable provider key."""


@dataclass(frozen=True)
class BuilderModel:
    """The vendor, model and key one turn will run against."""

    provider: str
    model: str
    api_key: str


async def resolve_model(session: AsyncSession) -> BuilderModel:
    """The model this turn should use.

    Raises :class:`BuilderUnavailable` with a message written for an operator
    rather than a user — every case here is something an administrator fixes on
    the provider keys screen, and saying so is more useful than a generic
    failure.
    """
    if not constants.AGENT_BUILDER_ENABLED:
        raise BuilderUnavailable(
            "The agent builder is switched off. Set AGENT_BUILDER_ENABLED=true "
            "to enable it."
        )

    if constants.AGENT_BUILDER_PROVIDER:
        candidates = [constants.AGENT_BUILDER_PROVIDER]
    else:
        candidates = [
            provider
            for provider in constants.AGENT_BUILDER_PROVIDER_PREFERENCE
            if provider in SUPPORTED_PROVIDERS
        ]

    unsupported = [c for c in candidates if c not in SUPPORTED_PROVIDERS]
    if unsupported:
        raise BuilderUnavailable(
            f"AGENT_BUILDER_PROVIDER is set to {unsupported[0]!r}, which the "
            f"builder cannot drive. Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    for provider in candidates:
        api_key = await platform_credentials.resolve_api_key(
            session, component=CostComponent.LLM, provider=provider
        )
        if api_key:
            model = constants.AGENT_BUILDER_MODELS.get(provider)
            if not model:
                logger.warning(
                    "No builder model configured for {}; skipping it.", provider
                )
                continue
            return BuilderModel(provider=provider, model=model, api_key=api_key)

    raise BuilderUnavailable(
        "No provider key is installed for the agent builder. Add an LLM key "
        f"for one of {', '.join(candidates)} in the provider keys screen."
    )


async def available_providers(session: AsyncSession) -> list[str]:
    """Which of the three the platform currently holds a key for.

    For the settings screen, so an operator can see what the builder *could*
    run on rather than only what it is running on.
    """
    found: list[str] = []
    for provider in SUPPORTED_PROVIDERS:
        key = await platform_credentials.resolve_api_key(
            session, component=CostComponent.LLM, provider=provider
        )
        if key:
            found.append(provider)
    return found


async def resolve_choice(session: AsyncSession, choice: str | None) -> BuilderModel:
    """The model for one Decibyl turn, honouring the picker when it can.

    ``choice`` is a chat preset (``deep``) or a named model
    (``model:openai/gpt-5``), or nothing. A preset resolves through its
    managed tier; a name is taken as given. Either runs only on a vendor
    the builder client can drive and holds a key for -- otherwise the turn
    falls back to :func:`resolve_model`, and says so in the log, rather
    than failing a message over a brain it could not use.
    """
    from api.services.configuration import chat_presets, managed_tiers

    chosen = (choice or "").strip().lower()
    if not chosen:
        return await resolve_model(session)

    pair = chat_presets.named_model(chosen)
    if pair is None and chosen in chat_presets.PRESETS_BY_SLUG:
        upstream = managed_tiers.resolve(
            "llm", chat_presets.PRESETS_BY_SLUG[chosen].llm_tier
        )
        pair = (upstream.provider, upstream.model)
    if pair is None:
        return await resolve_model(session)

    provider, model = pair
    if provider in SUPPORTED_PROVIDERS:
        api_key = await platform_credentials.resolve_api_key(
            session, component=CostComponent.LLM, provider=provider
        )
        if api_key:
            return BuilderModel(provider=provider, model=model, api_key=api_key)
    logger.info(
        "Decibyl cannot run {} on {} ({}); answering on the builder's model.",
        model,
        provider,
        "no key" if provider in SUPPORTED_PROVIDERS else "not a builder vendor",
    )
    return await resolve_model(session)

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
from api.services.agent_builder.client import SUPPORTED_PROVIDERS, is_exhausted
from api.services.configuration import platform_credentials


async def platform_key(
    session: AsyncSession, provider: str, model: str | None = None
) -> str | None:
    """What a platform turn on ``provider`` authenticates with.

    For Claude with ``CLAUDE_BACKEND`` on AWS and ready, the backend's
    marker (``services/aws_gateway/claude.py``): requests are signed with
    the instance role and no Anthropic key is needed. Otherwise the
    platform key stored for the vendor, exactly as before.
    """
    if provider == "anthropic":
        from api.services.aws_gateway import claude as aws_claude

        marker = aws_claude.platform_credential(model)
        if marker:
            return marker
    return await platform_credentials.resolve_api_key(
        session, component=CostComponent.LLM, provider=provider
    )


class BuilderUnavailable(RuntimeError):
    """The builder cannot run — disabled, or no usable provider key."""


class OwnKeyMissing(BuilderUnavailable):
    """The account runs on its own model keys and none can drive this turn.

    Raised instead of falling back to the platform key, which would charge
    the account for a turn it asked to have on its own key (BYOK-1). The
    message is written for the owner, not an operator.
    """


@dataclass(frozen=True)
class BuilderModel:
    """The vendor, model and key one turn will run against."""

    provider: str
    model: str
    api_key: str
    #: ``platform`` or ``byok`` (the account's own key, BYOK-1).
    key_source: str = "platform"


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

    # A vendor that said "out of credit" a moment ago is tried last, not
    # first: every other installed key gets the turn before it does.
    candidates = [c for c in candidates if not is_exhausted(c)] + [
        c for c in candidates if is_exhausted(c)
    ]
    for provider in candidates:
        model = constants.AGENT_BUILDER_MODELS.get(provider)
        api_key = await platform_key(session, provider, model)
        if api_key:
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
        key = await platform_key(
            session, provider, constants.AGENT_BUILDER_MODELS.get(provider)
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
    if provider in SUPPORTED_PROVIDERS and not is_exhausted(provider):
        api_key = await platform_key(session, provider, model)
        if api_key:
            return BuilderModel(provider=provider, model=model, api_key=api_key)
    logger.info(
        "Decibyl cannot run {} on {} ({}); answering on the builder's model.",
        model,
        provider,
        "no key" if provider in SUPPORTED_PROVIDERS else "not a builder vendor",
    )
    return await resolve_model(session)


# ---------------------------------------------------------------------------
# BYOK-1 (KAN-254): Decibyl, the builder and Decibyl routines on the
# account's own key.
# ---------------------------------------------------------------------------

OWN_KEY_MISSING_MESSAGE = (
    "Your workspace runs on its own model keys, and none of them can answer "
    "here: Decibyl needs an Anthropic, OpenAI or Google key. Add one in "
    "Settings → Model keys, or allow Decibyl's keys as a fallback there."
)


async def _own_key(
    session: AsyncSession, organization_id: int, provider: str
) -> str | None:
    from api.services.configuration import organization_credentials

    return await organization_credentials.resolve_api_key(
        session,
        organization_id=organization_id,
        component=CostComponent.LLM,
        provider=provider,
    )


async def _holds_llm_keys(session: AsyncSession, organization_id: int) -> bool:
    """Whether the account has brought any language-model key at all.

    The account with none is an ordinary managed customer and runs on the
    platform's keys, as before. The account with some has chosen its own."""
    from api.services.configuration import organization_credentials

    component = getattr(CostComponent.LLM, "value", CostComponent.LLM)
    for cred in await organization_credentials.list_credentials(
        session, organization_id=organization_id
    ):
        if not getattr(cred, "is_active", False):
            continue
        if str(getattr(cred, "component", "")) in (component, str(CostComponent.LLM)):
            return True
    return False


async def _fallback_to_platform_allowed(organization_id: int) -> bool:
    from api.services.organization_preferences import get_organization_preferences

    try:
        preferences = await get_organization_preferences(organization_id)
    except Exception:  # noqa: BLE001 - unknown preference is "not allowed"
        return False
    return bool(getattr(preferences, "byok_fallback_to_managed", False))


async def resolve_for_organization(
    session: AsyncSession, choice: str | None, *, organization_id: int | None
) -> BuilderModel:
    """The model for one turn of Decibyl or the builder, for this account.

    With ``byok_text`` off, or for an account that holds no model key of its
    own, exactly :func:`resolve_choice`: the platform's key. Otherwise the
    account's key, in this order: the vendor of the picked model, then the
    builder's preference order. An account whose keys cannot drive this turn
    gets :class:`OwnKeyMissing` unless it opted into Decibyl's keys as a
    fallback, which is the same rule calls follow (``byok_resolution``).
    """
    from api.services import features

    if organization_id is None or not features.is_on("byok_text", organization_id):
        return await resolve_choice(session, choice)
    if not constants.AGENT_BUILDER_ENABLED:
        # Same switch as the platform path: the account's key does not turn
        # on a feature the deployment has switched off.
        return await resolve_model(session)
    if not await _holds_llm_keys(session, organization_id):
        return await resolve_choice(session, choice)

    from api.services.configuration import chat_presets, managed_tiers

    chosen = (choice or "").strip().lower()
    wanted: tuple[str, str] | None = None
    if chosen:
        wanted = chat_presets.named_model(chosen)
        if wanted is None and chosen in chat_presets.PRESETS_BY_SLUG:
            upstream = managed_tiers.resolve(
                "llm", chat_presets.PRESETS_BY_SLUG[chosen].llm_tier
            )
            wanted = (upstream.provider, upstream.model)

    if wanted is not None and wanted[0] in SUPPORTED_PROVIDERS:
        key = await _own_key(session, organization_id, wanted[0])
        if key:
            return BuilderModel(
                provider=wanted[0], model=wanted[1], api_key=key, key_source="byok"
            )

    order = (
        [constants.AGENT_BUILDER_PROVIDER] if constants.AGENT_BUILDER_PROVIDER else []
    )
    order += [p for p in constants.AGENT_BUILDER_PROVIDER_PREFERENCE if p not in order]
    for provider in order:
        if provider not in SUPPORTED_PROVIDERS:
            continue
        model = constants.AGENT_BUILDER_MODELS.get(provider)
        if not model:
            continue
        key = await _own_key(session, organization_id, provider)
        if key:
            return BuilderModel(
                provider=provider, model=model, api_key=key, key_source="byok"
            )

    if await _fallback_to_platform_allowed(organization_id):
        logger.info(
            "Org {} has no usable own model key for Decibyl; on Decibyl's key "
            "by its own fallback setting.",
            organization_id,
        )
        return await resolve_choice(session, choice)
    raise OwnKeyMissing(OWN_KEY_MISSING_MESSAGE)

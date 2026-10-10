"""Which model runs a kind of work: Claude, unless Claude cannot.

``brain.py`` sorts a piece of work into quick, steps or deep. This module is
the second step: the model for that kind. The order never changes, so the
same message on the same day of keys and credit always lands on the same
model:

1. A rule that *names* a vendor (``prefer_vendor``) and that vendor is usable:
   that vendor's model for the kind. No rule ships that names one yet; the
   argument is the seam for it, and the reason is logged as ``rule``.
2. Claude's model for the kind (the managed tier Everyday / Smart / Deep
   resolves to), when the platform holds a usable Anthropic key and the vendor
   has not just said it is out of credit.
3. Otherwise OpenAI's model for the kind (``managed_tiers.AUTO_OPENAI_MODELS``),
   when the platform holds a usable OpenAI key: the fallback.
4. Otherwise Claude anyway, with the reason saying nothing else was usable. A
   reply that fails with a vendor's own error is better than one that fails in
   the router, and better than one that quietly goes somewhere unchecked.

"Usable" is a platform key that is stored, active and not known to be
rejected (``credential_validation.is_known_bad``: a key nobody has checked
counts as usable), and a vendor not marked out of credit
(``agent_builder.client.is_exhausted``). Auto runs on the platform's keys --
a workspace with its own key pinned a model and is not on Auto -- so the
workspace's keys are not asked about here.

Nothing is random and nothing is learned: the choice is a function of the
kind, the keys and the credit marks, and every choice carries the reason that
produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from api.enums import CostComponent
from api.services.configuration import chat_presets, managed_tiers

#: Reasons, as they are logged and counted. A closed list so the counters stay
#: countable.
CLAUDE_DEFAULT = "claude_default"
RULE_NAMED_VENDOR = "rule_named_vendor"
CLAUDE_UNAVAILABLE = "claude_unavailable"
NO_USABLE_VENDOR = "no_usable_vendor"
UNCHECKED = "availability_unchecked"


@dataclass(frozen=True)
class Pick:
    provider: str
    model: str
    #: What ``chat_presets.apply`` / ``resolve_for_organization`` is handed.
    slug: str
    #: One of the constants above.
    reason: str
    #: Why the vendor Auto would otherwise use was skipped, when it was.
    detail: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.reason == CLAUDE_UNAVAILABLE


def claude_for(kind: str) -> managed_tiers.ManagedUpstream:
    """The default model for this kind: the managed tier's, so a tier moved by
    ``MANAGED_LLM_*`` moves Auto with it."""
    return managed_tiers.resolve("llm", managed_tiers.AUTO_KIND_TIERS[kind])


def openai_for(kind: str) -> managed_tiers.ManagedUpstream:
    return managed_tiers.ManagedUpstream(
        managed_tiers.AUTO_OPENAI_PROVIDER, managed_tiers.AUTO_OPENAI_MODELS[kind]
    )


def openai_slug(kind: str) -> str:
    """``model:openai/<id>``: the catalogue's name for a direct choice."""
    return (
        f"{chat_presets.MODEL_PREFIX}{managed_tiers.AUTO_OPENAI_PROVIDER}/"
        + (managed_tiers.AUTO_OPENAI_MODELS[kind])
    )


async def unusable_because(
    session, upstream: managed_tiers.ManagedUpstream
) -> str | None:
    """Why the platform cannot run this upstream now, or None when it can."""
    from api.services.agent_builder import client
    from api.services.configuration import managed_resolution

    if client.is_exhausted(upstream.provider):
        return "out_of_credit"
    key = await managed_resolution._platform_key(
        session,
        component=CostComponent.LLM,
        provider=upstream.provider,
        model=upstream.model,
    )
    if not key:
        return "no_key"
    if await managed_resolution._credential_is_known_bad(
        session, component=CostComponent.LLM, provider=upstream.provider
    ):
        return "key_rejected"
    return None


def _claude_pick(kind: str, preset: str, reason: str, detail: str | None) -> Pick:
    upstream = claude_for(kind)
    return Pick(upstream.provider, upstream.model, preset, reason, detail)


async def pick(kind: str, *, preset: str, prefer_vendor: str | None = None) -> Pick:
    """The model for this kind of work. Never raises."""
    try:
        from api.db import db_client

        async with db_client.async_session() as session:
            return await _pick(session, kind, preset, prefer_vendor)
    except Exception as exc:  # noqa: BLE001 -- routing must never cost a reply
        logger.warning(
            "Auto could not check which vendors are usable ({}); using Claude",
            type(exc).__name__,
        )
        return _claude_pick(kind, preset, UNCHECKED, type(exc).__name__)


async def _pick(session, kind: str, preset: str, prefer_vendor: str | None) -> Pick:
    alternative = openai_for(kind)
    wants_openai = (prefer_vendor or "").strip().lower() == alternative.provider

    if wants_openai and await unusable_because(session, alternative) is None:
        return Pick(
            alternative.provider,
            alternative.model,
            openai_slug(kind),
            RULE_NAMED_VENDOR,
        )

    claude_why = await unusable_because(session, claude_for(kind))
    if claude_why is None:
        return _claude_pick(kind, preset, CLAUDE_DEFAULT, None)

    openai_why = await unusable_because(session, alternative)
    if openai_why is None:
        return Pick(
            alternative.provider,
            alternative.model,
            openai_slug(kind),
            CLAUDE_UNAVAILABLE,
            claude_why,
        )
    logger.warning(
        "Auto: neither Claude ({}) nor OpenAI ({}) is usable for {} work; "
        "staying on Claude",
        claude_why,
        openai_why,
        kind,
    )
    return _claude_pick(
        kind, preset, NO_USABLE_VENDOR, f"claude={claude_why}, openai={openai_why}"
    )

"""The catalogue of priced features, and which ones a call used.

Every competitor bills these separately — Retell prices knowledge-base
retrieval and post-call QA per minute on top of its platform fee — and Decibyl
has shipped both since launch without charging for either. This module is the
price list.

Three properties are worth knowing before adding an entry.

**An add-on is Decibyl revenue, never a pass-through.** Its line carries
``provider_cost_paise = 0`` and is never marked up, exactly like the platform
fee. That is what makes it appear as margin on the unit-economics screen
without any change to how revenue is summed.

**The catalogue is data, and the charge is a call-time measurement.** A rate
here is a list price in micro-dollars per billable minute; whether an account
paid it on a given call depends on ``usage_info["addons"]``, which the runtime
writes when a feature actually runs. An add-on that is configured but never
fires on a call is not billed for that call — the same rule the provider lines
follow, and the reason billing cannot drift from what the customer received.

**A feature with no runtime signal is deliberately absent.** Only features that
record their own use can be billed honestly, so the catalogue is smaller than
the list of things we could charge for. Adding an entry means adding the
``record_addon_used`` call that proves it ran, not just the price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api import constants

#: Where the runtime records which priced features ran on a call. A list of
#: catalogue keys under ``usage_info``, alongside ``key_sources`` and for the
#: same reason: the cost engine must not have to re-derive from configuration
#: what the pipeline already knows from execution.
USAGE_INFO_KEY = "addons"

#: Knowledge-base retrieval fired at least once on the call.
KNOWLEDGE_BASE = "knowledge_base"
#: Post-call QA analysis ran for the call. Unlike the other entries this has a
#: direct marginal cost to us: an LLM inference per call, whose tokens
#: ``run_integrations`` already folds into ``usage_info["llm"]`` under a
#: ``QAAnalysis`` processor. No rate resolves for that processor, so the cost
#: engine reports it as ``uncosted`` — measured, but paid for by us.
CALL_QA = "call_qa"


@dataclass(frozen=True)
class Addon:
    """A priced feature.

    ``micros_usd_per_minute`` is a list price in micro-dollars, converted to
    rupees at the call's own FX rate — the same treatment the platform fee
    gets, so the two are quoted in the same currency and move together.
    """

    key: str
    label: str
    micros_usd_per_minute: int
    note: str

    @property
    def is_billable(self) -> bool:
        """A rate of zero disables the charge but keeps the plumbing.

        Switching an add-on off is then an environment change rather than a
        deploy, and the usage is still recorded, so what it *would* have earned
        stays measurable.
        """
        return self.micros_usd_per_minute > 0


def catalogue() -> tuple[Addon, ...]:
    """The price list, read from configuration at call time.

    A function reading ``constants`` through the module rather than a
    from-import, so a rate changed at runtime is the rate the next call uses.
    Binding the values at import time would make the "switch it off with an
    environment variable" property above quietly untrue.
    """
    return (
        Addon(
            key=KNOWLEDGE_BASE,
            label="Knowledge base",
            micros_usd_per_minute=constants.ADDON_KNOWLEDGE_BASE_MICROS_USD,
            note="Document retrieval during the call.",
        ),
        Addon(
            key=CALL_QA,
            label="Post-call QA",
            micros_usd_per_minute=constants.ADDON_CALL_QA_MICROS_USD,
            note="Per-node scoring and call analysis after the call ends.",
        ),
    )


def by_key(key: str) -> Addon | None:
    """Look one up, or None if it is not in the catalogue.

    An unknown key is not an error. Usage recorded by a newer runtime than the
    costing code, or by a feature whose entry has since been removed, must not
    fail a receipt — it is simply not billed.
    """
    for addon in catalogue():
        if addon.key == key:
            return addon
    return None


def addon_keys_from_usage_info(usage_info: dict[str, Any] | None) -> tuple[str, ...]:
    """Which catalogue entries this run actually used.

    Deduplicated and returned in catalogue order rather than the order the
    runtime happened to record them, so a receipt lists add-ons the same way
    every time and two runs with the same add-ons produce identical line
    ordering.

    Unknown and malformed entries are dropped. ``usage_info`` is free-form JSON
    written by the pipeline; a bad value there must not stop a call being
    costed, because an uncosted call is a call the account used for free.
    """
    if not isinstance(usage_info, dict):
        return ()
    recorded = usage_info.get(USAGE_INFO_KEY)
    if not isinstance(recorded, (list, tuple, set)):
        return ()
    seen = {value for value in recorded if isinstance(value, str)}
    return tuple(addon.key for addon in catalogue() if addon.key in seen)


def record_addon_used(usage_info: dict[str, Any], key: str) -> None:
    """Note that ``key`` ran on this call, idempotently.

    Called from the runtime the moment a priced feature does its work, not from
    the configuration that enabled it: a knowledge base attached to a node that
    the conversation never reached has cost the customer nothing and must not
    appear on their invoice.

    Never raises. This is bookkeeping on the side of a live call, and failing a
    call to record a five-thousandth of a dollar would be a bad trade.
    """
    try:
        existing = usage_info.get(USAGE_INFO_KEY)
        if not isinstance(existing, list):
            existing = []
            usage_info[USAGE_INFO_KEY] = existing
        if key not in existing:
            existing.append(key)
    except Exception:  # noqa: BLE001 - see docstring
        pass

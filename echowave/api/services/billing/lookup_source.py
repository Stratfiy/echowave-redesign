"""Who pays for a contact lookup, and how (OP-3).

Decided 21 Sept 2026 with the Prospecting design: a workspace that has
connected its **own** contact-data app pays the tool-call fee alone, the
way every connected app is charged, because the vendor bills them
directly. A workspace with no such app looks up on the **platform's** key
and pays the fee plus the vendor's price passed through at cost, per
*verified* address -- an unverified guess is not a result and is not
charged for. A workspace with neither is told which app to connect, on a
card, never asked for a key.

This module is the rule and the charge, not a vendor: no enrichment API is
called here, and no vendor is chosen. Whoever wires one calls
:func:`source_for` first and :func:`charge` after, and the same two calls
hold whichever vendor it turns out to be. A platform provider with no rate
on file is charged the fee only and *says so* in the result, so a missing
rate is seen on the first lookup rather than found in a quarter's margin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.enums import CostComponent

#: Contact-data apps, as Composio names their toolkits, lower-case. The
#: workspace's *own* connection to any of these makes it the source. A
#: slug missing here is an app whose connection goes unrecognised and the
#: lookup falls to the platform key, so the miss is visible on the receipt
#: as a platform line; add the slug, not a guess.
CONTACT_DATA_TOOLKITS: frozenset[str] = frozenset(
    {
        "apollo",
        "hunter",
        "hunter_io",
        "lusha",
        "clearbit",
        "rocketreach",
        "peopledatalabs",
        "people_data_labs",
        "zoominfo",
        "snov",
        "snov_io",
        "dropcontact",
        "findymail",
        "kaspr",
        "prospeo",
        # A gateway over many of the above (treg.to), keyed once.
        "treg",
    }
)

#: What a platform lookup is metered as, in ``data_lookup_costs``.
VERIFIED_EMAIL = "verified_email"

OWN = "own"
PLATFORM = "platform"
NONE = "none"

CONNECT_LINE = (
    "No contact-data source is set up. Connect a contact-data app (Apollo, "
    "Hunter, Lusha, RocketReach…) to look addresses up on your own account, "
    "or ask an operator to add a platform key under provider keys, "
    "component data."
)


@dataclass(frozen=True)
class LookupSource:
    kind: str  # OWN, PLATFORM or NONE
    provider: str | None = None  # the toolkit slug, or the platform provider

    @property
    def usable(self) -> bool:
        return self.kind in (OWN, PLATFORM)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.kind}
        if self.provider:
            out["provider"] = self.provider
        if self.kind == OWN:
            out["charged_as"] = "tool call on your own account"
        elif self.kind == PLATFORM:
            out["charged_as"] = "tool call plus the vendor's price per verified address"
        else:
            out["reason"] = CONNECT_LINE
        return out


def own_toolkit(tools: list[Any]) -> str | None:
    """The first connected contact-data app among these tool rows."""
    from api.services.workflow import connected_tools

    for tool in tools:
        slug = connected_tools.toolkit_of(tool)
        if slug and slug in CONTACT_DATA_TOOLKITS:
            return slug
    return None


async def _platform_provider() -> str | None:
    """A contact-data provider the platform holds a key for, or None."""
    from api.db import db_client
    from api.services.configuration import platform_credentials

    async with db_client.async_session() as session:
        managed = await platform_credentials.managed_providers(session)
    for provider in managed.get(CostComponent.DATA.value, []):
        if provider.strip().lower() in CONTACT_DATA_TOOLKITS:
            return provider.strip().lower()
    return None


async def source_for(organization_id: int) -> LookupSource:
    """Own app first, the platform key second, neither last. Never raises:
    a source that cannot be read is no source, which is told, not guessed."""
    try:
        from api.services.workflow import connected_tools

        tools = await connected_tools.list_for_organization(organization_id)
        slug = own_toolkit(tools)
        if slug:
            return LookupSource(OWN, slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read org {}'s connected apps: {}", organization_id, exc
        )
    try:
        provider = await _platform_provider()
        if provider:
            return LookupSource(PLATFORM, provider)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the platform's data keys: {}", exc)
    return LookupSource(NONE)


async def charge(
    *,
    organization_id: int,
    source: LookupSource,
    verified: int,
    ref_id: str,
    workflow_id: int | None = None,
    note: str = "contact lookup",
) -> dict[str, Any]:
    """Charge one lookup by its source. Returns what was charged, for the
    tool result, so the model and the receipt say the same thing.

    Own app: the fee, at the connector's rate (premium or not). Platform:
    the fee plus ``verified`` pass-through lines; zero verified, no
    pass-through. Neither: nothing, and nothing should have been looked up.
    """
    from api.services.billing import data_costs
    from api.services.billing import events as billing_events

    if not source.usable:
        return {"charged": False, **source.as_dict()}
    event = (
        billing_events.tool_call_event(source.provider)
        if source.kind == OWN
        else billing_events.TOOL_CALL
    )
    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=event,
        ref_id=ref_id,
        note=f"{note} via {source.provider}",
        workflow_id=workflow_id,
    )
    out: dict[str, Any] = {"charged": True, "fee_event": event, **source.as_dict()}
    if source.kind == PLATFORM and verified > 0:
        paise = await data_costs.debit_lookup_in_own_session(
            organization_id=organization_id,
            provider=source.provider or "",
            kind=VERIFIED_EMAIL,
            requests=verified,
            ref_id=ref_id,
            workflow_id=workflow_id,
        )
        out["verified"] = verified
        out["pass_through_paise"] = paise
        if paise == 0:
            out["note"] = (
                f"No rate on file for {source.provider} {VERIFIED_EMAIL}; the "
                "vendor's price was not passed through on this lookup."
            )
    return out


__all__ = [
    "CONNECT_LINE",
    "CONTACT_DATA_TOOLKITS",
    "NONE",
    "OWN",
    "PLATFORM",
    "VERIFIED_EMAIL",
    "LookupSource",
    "charge",
    "own_toolkit",
    "source_for",
]

"""The models Decibyl sells, and whether each one can actually be sold.

One table, one answer to "what do we offer". Before this the offering was the
intersection of three things that could disagree — what the registry could
build, what a tier pointed at, and what had a rate row — and the customer's
model picker showed every vendor this codebase had ever integrated, including
the ones we hold no key for.

A model is **sellable** only when all three of these hold, and this module is
where that is decided rather than assumed:

* it is in the catalogue and enabled — an operator put it on sale;
* we hold a platform key for its provider — otherwise the call resolves no
  credential and runs on nothing;
* it has a live rate row — otherwise the call bills the platform fee alone,
  records the provider usage as uncosted, and reports margin we did not earn.

The third is the one that used to be invisible. A model offered and unpriced
does not fail: it works, and quietly loses money on every call. So the admin
listing returns ``is_priced`` per row and the customer listing simply omits
anything unpriced, which is the only honest thing to show a price picker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PlatformModelModel, ProviderRateModel
from api.enums import CostComponent

#: Slots a model can fill. ``realtime`` and ``embeddings`` are here because they
#: need a key and a catalogue entry like anything else, even though only the
#: first three are things a customer picks a voice or a brain for.
COMPONENTS = ("stt", "llm", "tts", "realtime", "embeddings")


class CatalogueError(ValueError):
    """A catalogue entry that could not be written."""


@dataclass(frozen=True)
class CatalogueEntry:
    component: str
    provider: str
    model: str
    label: str
    enabled: bool
    sort_order: int
    #: A live rate row exists for exactly this model, or for the provider as a
    #: whole. Both bill; the second bills at the vendor's default rather than
    #: this model's, which is worth showing but is not a blocker.
    is_priced: bool = False
    #: The rate matched the provider-wide row rather than this model. On an
    #: expensive model beside a cheap default that is a real mispricing.
    priced_by_fallback: bool = False
    #: We hold a platform key for this provider.
    has_key: bool = False

    @property
    def is_sellable(self) -> bool:
        return self.enabled and self.is_priced and self.has_key


def _label(entry: PlatformModelModel) -> str:
    return (entry.label or "").strip() or entry.model


async def _priced_keys(session: AsyncSession) -> tuple[set, set]:
    """``(component, provider, model)`` and ``(component, provider)`` in force.

    Two sets rather than a query per row: a catalogue of eighty models would
    otherwise be eighty round trips to draw one screen.
    """
    rows = (
        await session.execute(
            select(
                ProviderRateModel.component,
                ProviderRateModel.provider,
                ProviderRateModel.model,
            ).where(ProviderRateModel.effective_to.is_(None))
        )
    ).all()
    exact = {(c, p, m) for c, p, m in rows if m}
    provider_wide = {(c, p) for c, p, m in rows if not m}
    return exact, provider_wide


def _rate_card_slot(component: str, provider: str) -> tuple[str, str]:
    """The ``(component, provider)`` the rate card files this catalogue row under.

    Two slots do not price under the name they are catalogued under, and both
    read as "we never priced this" until translated:

    * **Realtime.** A speech-to-speech session is metered as LLM usage, and the
      rate is keyed by the name derived from the running pipecat service class
      -- ``decibylopenairealtime``, not ``openai_realtime``. ``estimator``
      already owns that seam, and its docstring records what the same gap cost
      on the estimate path: a quote of Rs2.76 against an invoice of Rs25.79.
      This is the catalogue's copy of that bug -- every realtime model read as
      unpriced, so ``sellable`` returned nothing and the picker's realtime slot
      was empty however many models an operator had ticked.
    * **Embeddings.** The catalogue says ``embeddings``; ``CostComponent`` says
      ``embedding``. One letter, same outcome.

    Everything else is already in rate-card form and passes through unchanged.
    """
    if component == "realtime":
        from api.services.billing.estimator import rate_card_provider

        return CostComponent.LLM.value, rate_card_provider(provider)
    if component == "embeddings":
        return CostComponent.EMBEDDING.value, provider
    return component, provider


async def _providers_with_keys(session: AsyncSession) -> set[tuple[str, str]]:
    """``(component, provider)`` we hold an active platform key for.

    Asked through ``managed_providers``, which is already the answer to "which
    providers can we serve" and is what the customer-facing picker uses. Two
    definitions of holding a key would drift, and the drift would show up as a
    model offered on a screen that the pipeline then cannot authenticate.
    """
    from api.services.configuration import platform_credentials as creds

    try:
        by_component = await creds.managed_providers(session)
    except Exception:  # noqa: BLE001 — a vault we cannot read is "no keys"
        return set()
    return {
        (component, provider)
        for component, providers in by_component.items()
        for provider in providers
    }


async def list_catalogue(
    session: AsyncSession,
    *,
    component: str | None = None,
    enabled_only: bool = False,
) -> list[CatalogueEntry]:
    """Every catalogue row, with what it would take to sell it."""
    query = select(PlatformModelModel)
    if component:
        query = query.where(PlatformModelModel.component == component.strip().lower())
    if enabled_only:
        query = query.where(PlatformModelModel.enabled.is_(True))
    rows = (
        await session.execute(
            query.order_by(
                PlatformModelModel.component,
                PlatformModelModel.sort_order,
                PlatformModelModel.provider,
                PlatformModelModel.model,
            )
        )
    ).scalars()

    exact, provider_wide = await _priced_keys(session)
    keyed = await _providers_with_keys(session)

    out: list[CatalogueEntry] = []
    for row in rows:
        rate_component, rate_provider = _rate_card_slot(row.component, row.provider)
        has_exact = (rate_component, rate_provider, row.model) in exact
        has_wide = (rate_component, rate_provider) in provider_wide
        out.append(
            CatalogueEntry(
                component=row.component,
                provider=row.provider,
                model=row.model,
                label=_label(row),
                enabled=bool(row.enabled),
                sort_order=int(row.sort_order or 0),
                is_priced=has_exact or has_wide,
                priced_by_fallback=(not has_exact) and has_wide,
                # Realtime and embeddings authenticate on the LLM credential —
                # the vendor issues one key for all of them — so a realtime
                # model counts as keyed when the LLM slot is.
                has_key=(row.component, row.provider) in keyed
                or (
                    row.component in ("realtime", "embeddings")
                    and ("llm", row.provider.replace("_realtime", "")) in keyed
                ),
            )
        )
    return out


async def sellable(session: AsyncSession, *, component: str) -> list[CatalogueEntry]:
    """What a customer may actually choose for this slot, today.

    Everything unsellable is omitted rather than shown greyed out. A model we
    cannot price is not a choice with a caveat, it is a choice that would bill
    the customer wrongly, and the picker's whole job is to put a price against
    every option.
    """
    return [
        entry
        for entry in await list_catalogue(
            session, component=component, enabled_only=True
        )
        if entry.is_sellable
    ]


async def offers(session: AsyncSession, *, component: str, provider: str) -> set[str]:
    """Model ids we already offer for this slot on this provider.

    Used to work out what a customer's own key adds: they may bring anything we
    do not already provide, and offering them a key-shaped way to buy something
    we sell managed would be two prices for one thing.
    """
    rows = (
        await session.execute(
            select(PlatformModelModel.model).where(
                PlatformModelModel.component == component.strip().lower(),
                PlatformModelModel.provider == provider.strip().lower(),
                PlatformModelModel.enabled.is_(True),
            )
        )
    ).scalars()
    return set(rows)


async def set_offered(
    session: AsyncSession,
    *,
    component: str,
    provider: str,
    models: list[str] | tuple[str, ...],
    labels: dict[str, str] | None = None,
) -> list[CatalogueEntry]:
    """Make the catalogue for this slot exactly ``models``.

    Replaces rather than merges, because the screen this serves is a set of
    tick boxes and an unticked box means "we do not offer this". Merging would
    make removal impossible from the only UI that writes here.

    Rows for models still ticked keep their id, so nothing downstream that
    referenced them by ``(component, provider, model)`` — which is everything —
    notices a save.
    """
    component = (component or "").strip().lower()
    provider = (provider or "").strip().lower()
    if component not in COMPONENTS:
        raise CatalogueError(f"{component!r} is not a slot a model can fill.")
    if not provider:
        raise CatalogueError("A catalogue entry needs a provider.")

    wanted = [m.strip() for m in models if (m or "").strip()]
    if len(set(wanted)) != len(wanted):
        raise CatalogueError("The same model was listed twice.")

    existing = {
        row.model: row
        for row in (
            await session.execute(
                select(PlatformModelModel).where(
                    PlatformModelModel.component == component,
                    PlatformModelModel.provider == provider,
                )
            )
        ).scalars()
    }

    labels = labels or {}
    now = datetime.now(UTC)
    for index, model in enumerate(wanted):
        row = existing.pop(model, None)
        if row is None:
            row = PlatformModelModel(
                component=component, provider=provider, model=model
            )
            session.add(row)
        row.label = (labels.get(model) or "").strip()
        row.enabled = True
        row.sort_order = index
        row.updated_at = now

    # Anything left in ``existing`` was unticked.
    for row in existing.values():
        await session.delete(row)

    await session.flush()
    return await list_catalogue(session, component=component)

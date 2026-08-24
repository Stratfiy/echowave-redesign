"""Everything about one vendor, on one screen.

The superadmin rate card was a flat list of rows, and a flat list is the wrong
shape for the question an operator actually asks. Nobody wonders "what is row
forty-one"; they wonder **"is OpenAI set up, and what does it cost us?"** —
which spans a stored key, the components that vendor serves, the models under
each, and which of those have a price.

So this assembles the vendor's-eye view, and three facts about each model that
a flat list could not show:

* **Whether a key is stored.** A priced model with no platform key is a managed
  call that fails at dial time.
* **Whether the model has a rate of its own, or falls through to the provider
  rate.** That distinction is the reason an operator edits a price and watches
  nothing happen — the resolver takes the most specific row, and until this
  screen said so the shadowing was invisible.
* **Which models the vendor offers that we have never priced.** Absent from a
  flat list by construction: a row that does not exist cannot be listed.

Read from the registry rather than a catalogue kept here, so a provider or
model added to the platform appears the day it lands rather than the day
somebody remembers this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ProviderRateModel
from api.enums import CostComponent

#: The billing components a platform key can be held for, in the order an
#: operator reads them: what it hears, what it thinks, what it says.
_COMPONENT_ORDER = ("stt", "llm", "tts", "embeddings")


@dataclass(frozen=True)
class ModelEntry:
    model: str
    #: Millipaise per rate unit, or None when this model has no row of its own.
    rate_mpaise: int | None
    #: Set instead of ``rate_mpaise`` when the row is quoted in dollars.
    rate_micros_usd: int | None
    unit: str | None
    #: True when the price shown comes from the provider-wide row rather than
    #: one quoted for this model. The flat-rate case, said out loud.
    from_provider_rate: bool


@dataclass(frozen=True)
class ComponentEntry:
    component: str
    #: The provider-wide rate, applied to any model without its own row.
    flat_rate_mpaise: int | None
    #: Set instead of ``flat_rate_mpaise`` when quoted in dollars. The screen
    #: reads this to open the editor in the currency the vendor was priced in,
    #: rather than in whichever one the form happens to default to.
    flat_rate_micros_usd: int | None
    flat_unit: str | None
    has_platform_key: bool
    models: list[ModelEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderEntry:
    provider: str
    components: list[ComponentEntry] = field(default_factory=list)

    @property
    def is_priced(self) -> bool:
        return any(
            c.flat_rate_mpaise is not None
            or c.flat_rate_micros_usd is not None
            or any(m.rate_mpaise or m.rate_micros_usd for m in c.models)
            for c in self.components
        )


def _registry_models(component: str, provider: str) -> tuple[list[str], bool]:
    """Models this vendor offers for this component, and whether it takes others."""
    from api.services.configuration.registry import REGISTRY, ServiceType

    service_type = {
        "llm": ServiceType.LLM,
        "stt": ServiceType.STT,
        "tts": ServiceType.TTS,
        "embeddings": ServiceType.EMBEDDINGS,
    }.get(component)
    if service_type is None:
        return [], True

    for key, config_class in REGISTRY.get(service_type, {}).items():
        if key.value != provider:
            continue
        model_field = config_class.model_fields.get("model")
        extra = (model_field.json_schema_extra or {}) if model_field else {}
        if not isinstance(extra, dict):
            return [], True
        models = list(extra.get("examples") or [])
        return models, bool(extra.get("allow_custom_input")) or not models
    return [], True


async def build(session: AsyncSession) -> list[ProviderEntry]:
    """Every vendor we price or hold a key for, grouped."""
    from api.services.configuration import platform_credentials as creds
    from api.services.configuration.registry import REGISTRY, ServiceType

    rates = list(
        (
            await session.scalars(
                select(ProviderRateModel).where(
                    ProviderRateModel.effective_to.is_(None)
                )
            )
        ).all()
    )
    stored = await creds.list_credentials(session)
    keyed = {(c.component, c.provider) for c in stored}

    # Every provider the platform can build, plus any we hold a rate or a key
    # for even if the registry no longer offers it — a retired vendor with live
    # rows has to stay visible or its prices become unreachable.
    known: set[str] = set()
    by_component: dict[str, set[str]] = {}
    for component in _COMPONENT_ORDER:
        service_type = {
            "llm": ServiceType.LLM,
            "stt": ServiceType.STT,
            "tts": ServiceType.TTS,
            "embeddings": ServiceType.EMBEDDINGS,
        }[component]
        names = {key.value for key in REGISTRY.get(service_type, {})}
        names |= {r.provider for r in rates if r.component == component}
        names |= {p for c, p in keyed if c == component}
        by_component[component] = names
        known |= names

    out: list[ProviderEntry] = []
    for provider in sorted(known):
        components: list[ComponentEntry] = []
        for component in _COMPONENT_ORDER:
            if provider not in by_component[component]:
                continue

            here = [
                r for r in rates if r.provider == provider and r.component == component
            ]
            flat = next((r for r in here if not r.model), None)
            named = {r.model: r for r in here if r.model}

            offered, _ = _registry_models(component, provider)
            # Registry models first, then any priced model the registry does
            # not list — a vendor's model we priced before they published it,
            # or one added by hand, must not disappear from the screen that
            # governs its price.
            model_names = list(dict.fromkeys([*offered, *sorted(named)]))

            models = [
                ModelEntry(
                    model=name,
                    rate_mpaise=(
                        named[name].rate_mpaise
                        if name in named
                        else (flat.rate_mpaise if flat else None)
                    ),
                    rate_micros_usd=(
                        named[name].rate_micros_usd
                        if name in named
                        else (flat.rate_micros_usd if flat else None)
                    ),
                    unit=(
                        named[name].unit
                        if name in named
                        else (flat.unit if flat else None)
                    ),
                    from_provider_rate=name not in named and flat is not None,
                )
                for name in model_names
            ]

            components.append(
                ComponentEntry(
                    component=component,
                    flat_rate_mpaise=flat.rate_mpaise if flat else None,
                    flat_rate_micros_usd=flat.rate_micros_usd if flat else None,
                    flat_unit=flat.unit if flat else None,
                    has_platform_key=(component, provider) in keyed,
                    models=models,
                )
            )

        if components:
            out.append(ProviderEntry(provider=provider, components=components))
    return out


async def set_rates(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    unit: str,
    actor_user_id: int | None,
    flat_rate_mpaise: int | None = None,
    flat_rate_micros_usd: int | None = None,
    model_rates: dict[str, int] | None = None,
    model_rates_micros_usd: dict[str, int] | None = None,
) -> dict:
    """Price a vendor flat, per model, or both.

    Both, because vendors differ: some quote one price for everything they
    serve and some price every model separately, and an operator should not
    have to model one as the other. A flat rate writes the provider-wide row; a
    model rate writes a named one that outranks it.

    Each row goes through ``rate_card.set_provider_rate``, so every price is
    still effective-dated and audited — this only saves an operator from making
    the same call fifteen times.
    """
    from api.services.billing import rate_card

    written: list[str] = []
    if flat_rate_mpaise is not None or flat_rate_micros_usd is not None:
        await rate_card.set_provider_rate(
            session,
            actor_user_id=actor_user_id,
            provider=provider,
            component=component,
            unit=unit,
            rate_mpaise=flat_rate_mpaise,
            rate_micros_usd=flat_rate_micros_usd,
            model="",
        )
        written.append("(all models)")

    # Dollar-quoted model rates, kept in their own map rather than sharing one
    # with the rupee rates. A single map would have to carry the currency per
    # entry or guess it from the provider, and guessing is how a $0.05 rate gets
    # written as five paise.
    for model, micros in sorted((model_rates_micros_usd or {}).items()):
        await rate_card.set_provider_rate(
            session,
            actor_user_id=actor_user_id,
            provider=provider,
            component=component,
            unit=unit,
            rate_micros_usd=micros,
            model=model,
        )
        written.append(model)

    for model, mpaise in sorted((model_rates or {}).items()):
        await rate_card.set_provider_rate(
            session,
            actor_user_id=actor_user_id,
            provider=provider,
            component=component,
            unit=unit,
            rate_mpaise=mpaise,
            model=model,
        )
        written.append(model)

    return {
        "provider": provider,
        "component": (
            component.value if isinstance(component, CostComponent) else component
        ),
        "written": written,
        "at": datetime.now(UTC).isoformat(),
    }

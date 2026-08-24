"""Effective-dated rate resolution.

The platform rate for a call is resolved in a fixed order:

1. **Account override** — an explicit, effective-dated rate for that account.
   Enterprise deals live here.
2. **Volume tier** — optional; applies once the account's billable minutes in
   the current billing period reach a tier's threshold. Highest matching
   threshold wins.
3. **Global default** — $0.02/min.

The list price is in **dollars** and settled in **rupees**, so resolving a rate
is really two lookups: what dollar figure applied, and what the rupee was worth
at that moment. Both are effective-dated, and both are returned, because a
receipt that says only "₹1.92" cannot be checked by the customer who was quoted
two cents.

Every lookup takes an ``at`` timestamp and asks the history tables what was in
effect *then*, never what is in effect now. That is what makes recomputing an
old invoice reproduce the original number. Callers should still snapshot the
resolved rate onto the call row (``platform_rate_mpaise_applied`` and friends)
so a historical receipt survives even a corrupted history table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    OrganizationRateHistoryModel,
    PlatformVolumeTierModel,
    ProviderRateModel,
    UsdInrRateHistoryModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PULSE_SECONDS,
    DEFAULT_USD_INR_PAISE,
    usd_to_mpaise,
)


@dataclass(frozen=True)
class ResolvedFxRate:
    """The USD→INR rate applied to a call, and whether it was a real one."""

    paise_per_usd: int
    source: str | None
    #: True when no history row covered ``at`` and the hard-coded fallback was
    #: used. Surfaced rather than swallowed: billing at a stale rate quietly
    #: erodes margin as the rupee weakens, and the only symptom is a number
    #: that looks fine.
    is_fallback: bool = False


@dataclass(frozen=True)
class ResolvedPlatformRate:
    """A platform rate plus how it was arrived at, for receipts and debugging.

    ``rate_mpaise`` is the figure actually billed. The USD price and FX rate
    are carried alongside so a receipt can show the conversion rather than
    asserting a rupee number.
    """

    rate_mpaise: int
    # "account_override" | "volume_tier" | "global_default"
    source: str
    pulse_seconds: int = DEFAULT_PULSE_SECONDS
    # Set when the rate was quoted in dollars, which is the normal case. None
    # for an override written in rupees, where there is no dollar price to show.
    rate_micros_usd: int | None = None
    usd_inr_paise: int | None = None
    tier_name: str | None = None


@dataclass(frozen=True)
class ResolvedProviderRate:
    provider: str
    component: str
    unit: RateUnit
    rate_mpaise: int
    # The model the matched row was quoted for; "" when it is the
    # provider-wide fallback rather than a model-specific rate.
    model: str = ""
    #: Set only when the row was quoted in dollars, alongside the FX used to
    #: reach ``rate_mpaise``. Both None on a rupee-native row, which is how a
    #: reader tells "this vendor invoices us in rupees" from "this vendor
    #: invoices us in dollars and today that came to this many paise".
    rate_micros_usd: int | None = None
    usd_inr_paise: int | None = None


def _effective_at(column_from, column_to, at: datetime):
    """Rows whose effective window contains ``at``.

    ``effective_to`` is exclusive so that a rate change at an exact instant has
    no gap and no overlap.
    """
    return and_(
        column_from <= at,
        or_(column_to.is_(None), column_to > at),
    )


async def resolve_usd_inr(session: AsyncSession, *, at: datetime) -> ResolvedFxRate:
    """The USD→INR rate in force at ``at``.

    Falls back to a constant when no row covers the moment, because failing to
    cost a call outright is worse than costing it at a slightly wrong rate. The
    fallback is flagged so it can be reported rather than assumed correct.
    """
    row = await session.scalar(
        select(UsdInrRateHistoryModel)
        .where(
            _effective_at(
                UsdInrRateHistoryModel.effective_from,
                UsdInrRateHistoryModel.effective_to,
                at,
            )
        )
        .order_by(UsdInrRateHistoryModel.effective_from.desc())
        .limit(1)
    )
    if row is not None:
        return ResolvedFxRate(paise_per_usd=row.paise_per_usd, source=row.source)

    logger.warning(
        "No USD/INR rate on file for {}; billing at the ₹{:.2f} fallback",
        at.isoformat(),
        DEFAULT_USD_INR_PAISE / 100,
    )
    return ResolvedFxRate(
        paise_per_usd=DEFAULT_USD_INR_PAISE, source=None, is_fallback=True
    )


def _from_row(row, *, source: str, fx: ResolvedFxRate, tier_name: str | None = None):
    """Build a resolved rate from a history row, in whichever currency it uses."""
    pulse = row.pulse_seconds or DEFAULT_PULSE_SECONDS

    if row.platform_rate_micros_usd is not None:
        return ResolvedPlatformRate(
            rate_mpaise=usd_to_mpaise(
                micros_usd=row.platform_rate_micros_usd,
                usd_inr_paise=fx.paise_per_usd,
            ),
            source=source,
            pulse_seconds=pulse,
            rate_micros_usd=row.platform_rate_micros_usd,
            usd_inr_paise=fx.paise_per_usd,
            tier_name=tier_name,
        )

    # Rupee-native contract: no dollar price to report, and deliberately no FX
    # applied — that is the whole reason the row is written this way.
    return ResolvedPlatformRate(
        rate_mpaise=row.platform_rate_mpaise,
        source=source,
        pulse_seconds=pulse,
        tier_name=tier_name,
    )


async def resolve_platform_rate(
    session: AsyncSession,
    *,
    organization_id: int | None,
    at: datetime,
    period_minutes: int = 0,
) -> ResolvedPlatformRate:
    """Resolve the platform rate for ``organization_id`` as of ``at``.

    ``period_minutes`` is the account's billable minutes so far in the current
    billing period, used only for tier matching.

    ``organization_id=None`` asks for the **list price** — the rate an account
    with no negotiated override pays. Operator screens need that: pricing a
    bundle against whichever account happened to be at hand would quote one
    customer's contract as though it were everybody's.
    """
    fx = await resolve_usd_inr(session, at=at)

    override = (
        await session.scalar(
            select(OrganizationRateHistoryModel)
            .where(
                OrganizationRateHistoryModel.organization_id == organization_id,
                _effective_at(
                    OrganizationRateHistoryModel.effective_from,
                    OrganizationRateHistoryModel.effective_to,
                    at,
                ),
            )
            .order_by(OrganizationRateHistoryModel.effective_from.desc())
            .limit(1)
        )
        if organization_id is not None
        else None
    )
    if override is not None:
        return _from_row(override, source="account_override", fx=fx)

    tier = await session.scalar(
        select(PlatformVolumeTierModel)
        .where(
            PlatformVolumeTierModel.min_period_minutes <= period_minutes,
            _effective_at(
                PlatformVolumeTierModel.effective_from,
                PlatformVolumeTierModel.effective_to,
                at,
            ),
        )
        .order_by(PlatformVolumeTierModel.min_period_minutes.desc())
        .limit(1)
    )
    if tier is not None:
        return _from_row(tier, source="volume_tier", fx=fx, tier_name=tier.name)

    return ResolvedPlatformRate(
        rate_mpaise=usd_to_mpaise(
            micros_usd=DEFAULT_PLATFORM_RATE_MICROS_USD,
            usd_inr_paise=fx.paise_per_usd,
        ),
        source="global_default",
        pulse_seconds=DEFAULT_PULSE_SECONDS,
        rate_micros_usd=DEFAULT_PLATFORM_RATE_MICROS_USD,
        usd_inr_paise=fx.paise_per_usd,
    )


async def resolve_provider_rate(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    at: datetime,
    model: str = "",
) -> ResolvedProviderRate | None:
    """Resolve a provider's unit rate as of ``at``, or None if none applies.

    Most specific wins: a rate quoted for this exact model beats the
    provider-wide fallback (``model = ""``). Models from one provider differ by
    more than an order of magnitude — gpt-4o against gpt-4o-mini — so pricing
    every OpenAI call at one rate would be wrong for anyone off the default.

    None means "we have no rate on file", which is different from a rate of
    zero. The cost engine treats it as an un-costable component and says so,
    rather than silently charging nothing.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    normalized_model = (model or "").strip().lower()

    row = await session.scalar(
        select(ProviderRateModel)
        .where(
            ProviderRateModel.provider == provider,
            ProviderRateModel.component == component_value,
            ProviderRateModel.model.in_(
                [normalized_model, ""] if normalized_model else [""]
            ),
            _effective_at(
                ProviderRateModel.effective_from,
                ProviderRateModel.effective_to,
                at,
            ),
        )
        # An exact model match sorts ahead of the "" fallback; among equals the
        # most recently effective row wins.
        .order_by(
            (ProviderRateModel.model == "").asc(),
            ProviderRateModel.effective_from.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None

    # A dollar-quoted row is converted here rather than at write time, so the
    # cost follows the rupee the way the vendor's invoice does. The FX lookup
    # happens only when such a row is actually matched — a deployment buying
    # entirely in rupees never pays for it.
    if row.rate_micros_usd is not None:
        fx = await resolve_usd_inr(session, at=at)
        return ResolvedProviderRate(
            provider=row.provider,
            component=row.component,
            unit=RateUnit(row.unit),
            rate_mpaise=usd_to_mpaise(
                micros_usd=row.rate_micros_usd, usd_inr_paise=fx.paise_per_usd
            ),
            model=row.model or "",
            rate_micros_usd=row.rate_micros_usd,
            usd_inr_paise=fx.paise_per_usd,
        )

    return ResolvedProviderRate(
        provider=row.provider,
        component=row.component,
        unit=RateUnit(row.unit),
        rate_mpaise=row.rate_mpaise,
        model=row.model or "",
    )

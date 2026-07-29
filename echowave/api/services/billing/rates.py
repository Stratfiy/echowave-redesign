"""Effective-dated rate resolution.

The platform rate for a call is resolved in a fixed order:

1. **Account override** — an explicit, effective-dated rate for that account.
   Enterprise deals live here.
2. **Volume tier** — optional; applies once the account's billable minutes in
   the current billing period reach a tier's threshold. Highest matching
   threshold wins.
3. **Global default** — ₹2.00/min.

Every lookup takes an ``at`` timestamp and asks the history tables what was in
effect *then*, never what is in effect now. That is what makes recomputing an
old invoice reproduce the original number. Callers should still snapshot the
resolved rate onto the call row (``platform_rate_mpaise_applied``) so a
historical receipt survives even a corrupted history table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    OrganizationRateHistoryModel,
    PlatformVolumeTierModel,
    ProviderRateModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE


@dataclass(frozen=True)
class ResolvedPlatformRate:
    """A platform rate plus where it came from, for display and debugging."""

    rate_mpaise: int
    # "account_override" | "volume_tier" | "global_default"
    source: str
    tier_name: str | None = None


@dataclass(frozen=True)
class ResolvedProviderRate:
    provider: str
    component: str
    unit: RateUnit
    rate_mpaise: int


def _effective_at(column_from, column_to, at: datetime):
    """Rows whose effective window contains ``at``.

    ``effective_to`` is exclusive so that a rate change at an exact instant has
    no gap and no overlap.
    """
    return and_(
        column_from <= at,
        or_(column_to.is_(None), column_to > at),
    )


async def resolve_platform_rate(
    session: AsyncSession,
    *,
    organization_id: int,
    at: datetime,
    period_minutes: int = 0,
) -> ResolvedPlatformRate:
    """Resolve the platform rate for ``organization_id`` as of ``at``.

    ``period_minutes`` is the account's billable minutes so far in the current
    billing period, used only for tier matching.
    """
    override = await session.scalar(
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
    if override is not None:
        return ResolvedPlatformRate(
            rate_mpaise=override.platform_rate_mpaise,
            source="account_override",
        )

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
        return ResolvedPlatformRate(
            rate_mpaise=tier.platform_rate_mpaise,
            source="volume_tier",
            tier_name=tier.name,
        )

    return ResolvedPlatformRate(
        rate_mpaise=DEFAULT_PLATFORM_RATE_MPAISE,
        source="global_default",
    )


async def resolve_provider_rate(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    at: datetime,
) -> ResolvedProviderRate | None:
    """Resolve a provider's unit rate as of ``at``, or None if none applies.

    None means "we have no rate on file", which is different from a rate of
    zero. The cost engine treats it as an un-costable component and says so,
    rather than silently charging nothing.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    row = await session.scalar(
        select(ProviderRateModel)
        .where(
            ProviderRateModel.provider == provider,
            ProviderRateModel.component == component_value,
            _effective_at(
                ProviderRateModel.effective_from,
                ProviderRateModel.effective_to,
                at,
            ),
        )
        .order_by(ProviderRateModel.effective_from.desc())
        .limit(1)
    )
    if row is None:
        return None
    return ResolvedProviderRate(
        provider=row.provider,
        component=row.component,
        unit=RateUnit(row.unit),
        rate_mpaise=row.rate_mpaise,
    )

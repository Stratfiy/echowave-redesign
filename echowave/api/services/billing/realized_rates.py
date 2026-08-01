"""What a provider actually charged, derived from calls we already made.

A rate card holds list prices, and list prices are the one number nobody pays.
Volume commitments, startup credits and negotiated contracts all move the real
figure, usually downward, and none of that reaches a vendor's public pricing
page. The seeded book is a starting point that says so in its own docstring.

Every priced call already writes ``units`` and ``cost_paise`` per component to
``call_cost_items`` so a receipt can be re-derived. Summing those two columns
over a window and dividing gives **the rate we are really paying**, measured
rather than quoted:

    realized = SUM(cost_paise) / SUM(units)

That is strictly better evidence than a published price, and it is the number
to reconcile the rate card against. When the two disagree materially, one of
them is wrong and it is usually not the measurement.

The obvious trap is comparing the realized figure to the configured one and
"correcting" the rate card to match. That would be circular: ``cost_paise`` was
*computed from* ``unit_rate_mpaise``, so where a single rate has been in force
for the whole window the two agree by construction and prove nothing. The
comparison earns its keep in exactly the case it is built for — when the rate
changed mid-window, or when different models under one provider carry different
rates — because then the blend reveals what the card's single headline number
hides. :func:`divergence` therefore reports, and never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallCostItemModel, WorkflowRunModel

#: How far back to look. Long enough to survive a quiet week, short enough that
#: a rate changed last month does not dominate what we report as current.
DEFAULT_WINDOW_DAYS = 30

#: Below this many units a blended rate is noise — one atypical call can move
#: it by an order of magnitude — so it is reported as unmeasured rather than
#: as a small number that looks authoritative.
MIN_UNITS_FOR_SIGNIFICANCE = 1_000

#: Relative gap at which configured and realized stop being roughly the same
#: number. Ten percent is wide enough to absorb rounding and pulse effects, and
#: narrow enough to catch the 1.56x Sarvam error that shipped in the seeded book.
DIVERGENCE_THRESHOLD = 0.10


@dataclass(frozen=True)
class RealizedRate:
    provider: str
    component: str
    units: int
    cost_paise: int
    calls: int

    @property
    def mpaise_per_unit(self) -> float:
        """Millipaise per unit, to match ``provider_rates.rate_mpaise``."""
        return (self.cost_paise * 1000) / self.units if self.units else 0.0

    @property
    def is_significant(self) -> bool:
        return self.units >= MIN_UNITS_FOR_SIGNIFICANCE


async def measure(
    session: AsyncSession,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    organization_id: int | None = None,
) -> list[RealizedRate]:
    """Blended cost per unit for every provider and component with usage.

    Joined to ``workflow_runs`` for the timestamp because cost items carry no
    time of their own — they belong to the run, and a run is what happened on a
    date. ``organization_id`` is optional and platform-wide is the default: the
    question this answers is what *we* pay a vendor, which is not an
    account-scoped fact.
    """
    since = datetime.now(UTC) - timedelta(days=window_days)

    query = (
        select(
            CallCostItemModel.provider,
            CallCostItemModel.component,
            func.sum(CallCostItemModel.units).label("units"),
            func.sum(CallCostItemModel.cost_paise).label("cost_paise"),
            func.count(func.distinct(CallCostItemModel.workflow_run_id)).label("calls"),
        )
        .join(
            WorkflowRunModel,
            WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
        )
        .where(
            WorkflowRunModel.created_at >= since,
            # The platform fee has no provider and is not a vendor rate.
            CallCostItemModel.provider.isnot(None),
            CallCostItemModel.units > 0,
        )
        .group_by(CallCostItemModel.provider, CallCostItemModel.component)
    )
    if organization_id is not None:
        query = query.where(WorkflowRunModel.organization_id == organization_id)

    rows = (await session.execute(query)).all()
    return [
        RealizedRate(
            provider=r.provider,
            component=r.component,
            units=int(r.units or 0),
            cost_paise=int(r.cost_paise or 0),
            calls=int(r.calls or 0),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class Divergence:
    provider: str
    component: str
    configured_mpaise: float
    realized_mpaise: float
    units: int

    @property
    def ratio(self) -> float:
        """Realized over configured. Above 1 means we are paying more than the
        card says, which is the direction that costs money quietly."""
        return (
            self.realized_mpaise / self.configured_mpaise
            if self.configured_mpaise
            else 0.0
        )

    @property
    def note(self) -> str:
        if self.ratio > 1:
            return (
                f"Paying {self.ratio:.2f}x the configured rate. The card "
                "understates cost, so margin is being reported too high."
            )
        return (
            f"Paying {self.ratio:.2f}x the configured rate — likely a negotiated "
            "or discounted price the card has not caught up with."
        )


def divergence(
    realized: list[RealizedRate], configured: dict[tuple[str, str], float]
) -> list[Divergence]:
    """Where measured and configured rates disagree enough to look into.

    ``configured`` is keyed by ``(provider, component)`` in millipaise per unit
    — the shape ``rate_card`` already holds. Providers with no configured rate
    are skipped rather than reported as infinitely divergent: an unpriced
    provider is a different problem, and the rate card reports it separately.
    """
    out = []
    for r in realized:
        if not r.is_significant:
            continue
        configured_rate = configured.get((r.provider, r.component))
        if not configured_rate:
            continue
        gap = abs(r.mpaise_per_unit - configured_rate) / configured_rate
        if gap >= DIVERGENCE_THRESHOLD:
            out.append(
                Divergence(
                    provider=r.provider,
                    component=r.component,
                    configured_mpaise=configured_rate,
                    realized_mpaise=r.mpaise_per_unit,
                    units=r.units,
                )
            )
    return sorted(out, key=lambda d: abs(d.ratio - 1), reverse=True)

"""What our own calls were costed at, per provider, component and model.

**This does not measure what a vendor charged us, and this file used to claim
it did.** Every line in ``call_cost_items`` has its ``provider_cost_paise``
computed by the cost engine as ``units × rate``, where ``rate`` came from the
rate card — exact model first, then the provider-wide fallback. Summing that
column and dividing by units therefore returns a units-weighted average of the
card's own numbers. It carries no information about a vendor's invoice, and no
amount of usage will make it reveal one. Only a vendor's own bill can do that.

What it does measure is real and worth having: **which rate our calls were
actually costed at**, blended across whatever ran in the window.

    realized = SUM(provider_cost_paise) / SUM(units)

Grouped by model, because the cost engine prices by model. Leaving the model
out is what made this report lie. It compared a blend across every OpenAI model
— gpt-4.1 at 36,480 millipaise per 1k tokens mixed with gpt-4.1-mini at 7,296 —
against the provider-wide fallback of 2,736, which governs neither of them, and
reported the arithmetic of that mix as "paying 5.32x the configured rate, the
card understates cost, so margin is being reported too high". Nothing was being
paid over the odds and margin was correct. An operator acting on that finding
would have raised the fallback fivefold, and then overcharged every model that
legitimately falls back to it.

So a realized row is now compared only against the rate that actually priced
it, resolved the way the cost engine resolves it: the model's own row, else the
provider-wide one. What survives that is a genuine finding — **the card today
is not what these calls were costed at** — which happens when a rate moved
mid-window, or when a model row was added after calls had already run. Margin
measured over the period will then not match margin forecast from the card, and
that is worth an operator's attention.

:func:`divergence` reports, and never writes.
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
    #: The model these lines were priced against, or None for a component that
    #: has no model (telephony) and for rows written before the cost engine
    #: recorded one. Carried because the cost engine prices *by model*: a blend
    #: across models is not comparable to any single card rate.
    model: str | None = None

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
            CallCostItemModel.model,
            func.sum(CallCostItemModel.units).label("units"),
            # The **vendor** figure, not the customer-facing one. This report
            # exists to answer "is the rate card still what the vendor charges",
            # so it has to compare like with like: `cost_paise` is the line
            # after markup, so summing it made every marked-up component report
            # a realized rate of exactly the markup multiple — 1.40x on the
            # default 14000 bps — and read as "the card understates cost".
            # Telephony carries no markup and so reported 1.00x, which made the
            # false signal look selective and therefore credible. An operator
            # acting on it would raise the card to 1.4x vendor cost and then
            # charge the markup on top of that.
            func.sum(CallCostItemModel.provider_cost_paise).label("cost_paise"),
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
        .group_by(
            CallCostItemModel.provider,
            CallCostItemModel.component,
            CallCostItemModel.model,
        )
    )
    if organization_id is not None:
        query = query.where(WorkflowRunModel.organization_id == organization_id)

    rows = (await session.execute(query)).all()
    return [
        RealizedRate(
            provider=r.provider,
            component=r.component,
            model=r.model or None,
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
    model: str | None = None

    @property
    def subject(self) -> str:
        """What these calls ran on, for a message somebody has to act on."""
        return f"{self.provider} {self.model}" if self.model else self.provider

    @property
    def ratio(self) -> float:
        """Realized over the rate on the card today.

        Above 1 means these calls were costed higher than the current card, so
        margin measured over the window is worse than the card would forecast.
        It does **not** mean a vendor charged more than the card says: the
        realized figure is derived from the card, and cannot be evidence about
        a vendor. See the module docstring.
        """
        return (
            self.realized_mpaise / self.configured_mpaise
            if self.configured_mpaise
            else 0.0
        )

    @property
    def note(self) -> str:
        """Deliberately about the card and the period, never about the vendor.

        The wording this replaced asserted "the card understates cost, so
        margin is being reported too high", which is a claim about what a
        vendor charges that this data cannot support — and, on the finding that
        prompted the rewrite, was simply false.
        """
        direction = "above" if self.ratio > 1 else "below"
        return (
            f"Costed at {self.ratio:.2f}x the rate now on the card, {direction} "
            f"today's figure for {self.subject}. The rate changed during the "
            "window, so margin measured over this period will not match margin "
            "forecast from the card."
        )


#: How many recorded units one card unit holds. Usage is written in the
#: pipeline's own units — seconds of speech, characters, tokens — while the
#: card quotes a minute, a thousand characters, a thousand tokens. Comparing
#: the two without this made every vendor read as 0.001x or 0.017x the card:
#: not a discount, a unit.
_UNITS_PER_CARD_UNIT = {
    "minute": 60.0,
    "1k_chars": 1000.0,
    "1k_tokens": 1000.0,
}


def per_unit(card_rate_mpaise: float, unit: str | None) -> float:
    """A card rate in millipaise per *recorded* unit, comparable to measured."""
    return card_rate_mpaise / _UNITS_PER_CARD_UNIT.get(unit or "", 1.0)


def rate_for(
    configured: dict[tuple[str, str, str | None], float],
    *,
    provider: str,
    component: str,
    model: str | None,
) -> float | None:
    """The card rate that priced this usage, resolved as the cost engine does.

    Exact model first, then the provider-wide fallback — the same two-step
    lookup as ``cost_engine``, and it has to stay the same two steps. Comparing
    usage against a rate that did not price it is precisely the bug this
    module was rewritten to remove.
    """
    exact = configured.get((provider, component, model))
    if exact:
        return exact
    return configured.get((provider, component, None))


def divergence(
    realized: list[RealizedRate],
    configured: dict[tuple[str, str, str | None], float],
) -> list[Divergence]:
    """Where the card today disagrees with what these calls were costed at.

    ``configured`` is keyed by ``(provider, component, model)`` in millipaise
    per unit, with ``model`` None for the provider-wide row. Usage with no
    configured rate at all is skipped rather than reported as infinitely
    divergent: an unpriced provider is a different problem, and the rate card
    reports it separately.

    Where a rate has been in force for the whole window, realized and
    configured agree to the rounding — cost was computed from that rate — and
    nothing is reported. That is not a weakness, it is the correct answer, and
    it is why this exists per model rather than per provider.
    """
    out = []
    for r in realized:
        if not r.is_significant:
            continue
        configured_rate = rate_for(
            configured,
            provider=r.provider,
            component=r.component,
            model=r.model,
        )
        if not configured_rate:
            continue
        gap = abs(r.mpaise_per_unit - configured_rate) / configured_rate
        if gap >= DIVERGENCE_THRESHOLD:
            out.append(
                Divergence(
                    provider=r.provider,
                    component=r.component,
                    model=r.model,
                    configured_mpaise=configured_rate,
                    realized_mpaise=r.mpaise_per_unit,
                    units=r.units,
                )
            )
    return sorted(out, key=lambda d: abs(d.ratio - 1), reverse=True)

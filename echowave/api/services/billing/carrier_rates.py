"""Which carriers we may actually sell minutes on.

Four of the carriers this platform can dial through have never had their India
outbound rate read off a published price list. Two carried stand-ins at the
Twilio India mobile rate; two carried their **US** rates against traffic that is
Indian, which is a different and dearer market.

While carriage was cost-only that was survivable and erring high was free — we
under-reported our own margin until somebody corrected it. Carriage is now
marked up into the sell price like every other component, and that removes the
safe direction: a stand-in that is too high overcharges the customer, one that
is too low sells the minute at a loss, and nothing about the resulting invoice
says which happened.

So the rule this module enforces is narrow and absolute: **a carrier whose rate
is still a stand-in cannot be put on the managed path.** Not "warns", not
"estimates approximately" — refused, at the one route that decides it, because
the alternative is billing somebody from a number nobody checked.

It costs nothing today. A customer on their own Telnyx or Vonage account is
unaffected: those minutes are on their own carrier invoice and we bill no
carriage at all (``services/telephony/carriage.py``). The rule bites only when
Decibyl fronts the carrier, which is exactly when the number has to be right.

**Provisional is a property of the live rate, not of this file.** An operator
who puts a real figure in at ``/superadmin/billing/rate-card`` supersedes the
seeded row and writes their own note, the marker goes with it, and the carrier
becomes sellable with no code change. That is the intended way out.

**A second, separate gate lives here too: which carriers we have actually
chosen to sell managed minutes on at all.** This is not a statement about rate
quality — Twilio's rate is real, published, and not provisional — it is a
rollout decision, and the two are kept apart deliberately. A carrier could
clear provisional pricing today and still not be enabled; conflating the two
would make ``MANAGED_CARRIER_ALLOWLIST`` look like a data-quality flag, and
the first person who "fixed" a rate would accidentally turn selling on that
carrier back on.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ProviderRateModel
from api.enums import CostComponent
from api.services.billing.default_rates import (
    PROVISIONAL_MARKER,
    TELEPHONY_RATES,
)

#: Carriers Decibyl currently sells managed minutes on. Everything else is
#: refused on the managed path regardless of whether its rate is verified —
#: this is a rollout decision made 21 Aug 2026, not a statement that the other
#: carriers' numbers are wrong. Widen it when a carrier is actually ready to
#: be sold on, not when its rate merely becomes accurate.
MANAGED_CARRIER_ALLOWLIST: frozenset[str] = frozenset({"plivo"})


def is_enabled_for_managed_billing(provider: str) -> bool:
    """Whether this carrier is one Decibyl currently sells managed minutes on.

    Independent of :func:`is_provisionally_priced` — a carrier can fail either
    check on its own, and the managed route refuses on both.
    """
    return (provider or "").strip().lower() in MANAGED_CARRIER_ALLOWLIST


def seeded_provisional_carriers() -> frozenset[str]:
    """Carriers this file ships a stand-in for.

    The static answer, for tests and for anything that has no session. The
    question that decides whether money moves is
    :func:`provisionally_priced`, which reads what is actually in force.
    """
    return frozenset(
        rate.provider
        for rate in TELEPHONY_RATES
        if rate.provisional and rate.component is CostComponent.TELEPHONY
    )


async def provisionally_priced(session: AsyncSession) -> dict[str, str]:
    """``{carrier: note}`` for every live carrier rate that is still a guess.

    Matched on the marker inside the seeded note rather than on a column of its
    own. That is deliberate: superseding a rate is how this codebase records a
    price change, so the operator's replacement row simply does not carry the
    marker and the carrier drops out of this set. A boolean column would have
    to be cleared by hand, and a flag somebody has to remember to clear is a
    flag that is eventually wrong in the dangerous direction.
    """
    rows = (
        await session.execute(
            select(ProviderRateModel.provider, ProviderRateModel.note).where(
                ProviderRateModel.component == CostComponent.TELEPHONY.value,
                ProviderRateModel.effective_to.is_(None),
                ProviderRateModel.note.ilike(f"%{PROVISIONAL_MARKER}%"),
            )
        )
    ).all()
    return {provider: (note or "") for provider, note in rows}


async def is_provisionally_priced(session: AsyncSession, *, provider: str) -> bool:
    """Whether this carrier's live rate is a stand-in.

    A carrier with **no** rate at all is not provisional here — it is unpriced,
    which is a different failure with its own guard
    (``tests/test_telephony_is_priced.py``) and its own readiness check. This
    answers one question only: is the number we hold a guess.
    """
    name = (provider or "").strip().lower()
    if not name:
        return False
    return name in await provisionally_priced(session)

"""Load, and refresh, the starter price book (``default_rates``).

The book is a file; the rate that bills is a row. This module is the only
path from one to the other, shared by the operator script
(``scripts/seed_provider_rates``) and the superadmin screen, so both do the
same thing and say the same thing.

Three modes, in order of how much they overwrite:

* **seed** — write the rows that are missing. A rate already on file is left
  alone, whoever wrote it.
* **refresh seeded** — also replace rows that are still this book's own
  defaults, told by the note prefix ``default_rates.SEED_NOTE_PREFIX``. A row a
  person wrote (through the screen, with their own note or none) is never
  touched: prices somebody chose outrank prices a file guessed, always. This is
  how a production card that was seeded from an old book takes the survey
  (KAN-58) without losing the contracted Smallest or Cartesia figures.
* **force** — replace everything. Rarely what anyone wants.

Nothing here commits; the caller does, after reading the plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ProviderRateModel
from api.services.billing.default_rates import (
    DEFAULT_RATES,
    SEED_NOTE,
    DefaultRate,
    is_seeded_note,
    usd_to_mpaise,
)
from api.services.billing.rate_card import set_provider_rate
from api.services.billing.rates import resolve_usd_inr

ACTION_WRITE = "write"
ACTION_REPLACE = "replace"
ACTION_SKIP_CHOSEN = "skip — set by an operator"
ACTION_SKIP_SEEDED = "skip — already seeded"


@dataclass(frozen=True)
class SeedLine:
    rate: DefaultRate
    rate_mpaise: int
    action: str

    @property
    def writes(self) -> bool:
        return self.action in (ACTION_WRITE, ACTION_REPLACE)

    def as_dict(self) -> dict:
        return {
            "provider": self.rate.provider,
            "model": self.rate.model or None,
            "component": self.rate.component.value,
            "unit": self.rate.unit.value,
            "usd_per_unit": self.rate.usd_per_unit,
            "rate_mpaise": self.rate_mpaise,
            "provisional": self.rate.provisional,
            "source_url": self.rate.source,
            "source_checked_on": self.rate.source_checked_on,
            "action": self.action,
        }


@dataclass(frozen=True)
class SeedPlan:
    usd_inr: float
    fx_source: str
    lines: list[SeedLine]

    @property
    def to_write(self) -> list[SeedLine]:
        return [line for line in self.lines if line.writes]

    def as_dict(self) -> dict:
        return {
            "usd_inr": self.usd_inr,
            "fx_source": self.fx_source,
            "lines": [line.as_dict() for line in self.lines],
            "would_write": len(self.to_write),
        }


async def _open_rows(
    session: AsyncSession,
) -> dict[tuple[str, str, str], ProviderRateModel]:
    """Every rate currently in force, keyed as the resolver keys them.

    Only open rows count. A retired rate is history, not a decision anyone is
    still standing behind, so seeding over it is fine.
    """
    rows = (
        await session.scalars(
            select(ProviderRateModel).where(ProviderRateModel.effective_to.is_(None))
        )
    ).all()
    return {(r.provider, r.model, r.component): r for r in rows}


async def plan(
    session: AsyncSession,
    *,
    force: bool = False,
    refresh_seeded: bool = False,
    at: datetime | None = None,
) -> SeedPlan:
    """What seeding would do, row by row, without doing it."""
    at = at or datetime.now(UTC)
    fx = await resolve_usd_inr(session, at=at)
    usd_inr = fx.paise_per_usd / 100
    existing = await _open_rows(session)

    lines: list[SeedLine] = []
    for rate in DEFAULT_RATES:
        key = (rate.provider, rate.model, rate.component.value)
        mpaise = usd_to_mpaise(rate.usd_per_unit, usd_inr=usd_inr)
        current = existing.get(key)
        if current is None:
            action = ACTION_WRITE
        elif force:
            action = ACTION_REPLACE
        elif refresh_seeded and is_seeded_note(current.note):
            action = ACTION_REPLACE
        elif is_seeded_note(current.note):
            action = ACTION_SKIP_SEEDED
        else:
            action = ACTION_SKIP_CHOSEN
        lines.append(SeedLine(rate=rate, rate_mpaise=mpaise, action=action))
    return SeedPlan(usd_inr=usd_inr, fx_source=fx.source, lines=lines)


async def apply(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    force: bool = False,
    refresh_seeded: bool = False,
    at: datetime | None = None,
) -> SeedPlan:
    """Write the plan. Each row goes through ``set_provider_rate``, so it is
    effective-dated, audited, and closes the row it replaces."""
    at = at or datetime.now(UTC)
    seed_plan = await plan(session, force=force, refresh_seeded=refresh_seeded, at=at)
    for line in seed_plan.to_write:
        rate = line.rate
        await set_provider_rate(
            session,
            actor_user_id=actor_user_id,
            provider=rate.provider,
            model=rate.model,
            component=rate.component,
            unit=rate.unit,
            rate_mpaise=line.rate_mpaise,
            effective_from=at,
            note=f"{SEED_NOTE} ({rate.basis})",
            source_url=rate.source,
            source_checked_on=rate.source_checked_on,
        )
    return seed_plan

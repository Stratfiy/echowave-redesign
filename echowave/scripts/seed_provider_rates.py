"""Load the starter provider price book into an empty rate card.

Without provider rates the cost engine has only the platform fee to work with:
the model picker shows the same number whatever you choose, and every call is
recorded as *uncosted*, not free — so margin reads as 100% and is wrong. This
puts a defensible number against each provider so the machinery works, and
leaves a note on every row saying it was defaulted rather than chosen.

    python -m scripts.seed_provider_rates                 # show what it would do
    python -m scripts.seed_provider_rates --confirm       # write the missing rates
    python -m scripts.seed_provider_rates --confirm --force   # also overwrite existing

**Nothing is written without `--confirm`**, and a rate an operator has already
set is never replaced unless `--force` is passed as well. Prices you chose
outrank prices this file guessed, always — the whole point of a seed is to stop
a fresh install looking broken, not to have an opinion about your contracts.

Requires DATABASE_URL, like every repo-owned script:

    set -a && source api/.env && set +a && python -m scripts.seed_provider_rates

In Docker, where there is no checkout to run from:

    docker compose exec api python -m scripts.seed_provider_rates --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from api.db import db_client
from api.db.models import ProviderRateModel
from api.services.billing.default_rates import (
    AS_OF,
    DEFAULT_RATES,
    SEED_NOTE,
    usd_to_mpaise,
)
from api.services.billing.rate_card import set_provider_rate
from api.services.billing.rates import resolve_usd_inr


async def _existing_keys(session) -> set[tuple[str, str, str]]:
    """(provider, model, component) for every rate currently in force.

    Only open rows count. A retired rate is history, not a decision anyone is
    still standing behind, so seeding over it is fine.
    """
    rows = (
        await session.scalars(
            select(ProviderRateModel).where(ProviderRateModel.effective_to.is_(None))
        )
    ).all()
    return {(r.provider, r.model, r.component) for r in rows}


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed default provider rates into an empty rate card."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually write. Without it, prints what would be written and exits.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also replace rates that are already set. Rarely what you want.",
    )
    args = parser.parse_args()

    now = datetime.now(UTC)

    async with db_client.async_session() as session:
        fx = await resolve_usd_inr(session, at=now)
        usd_inr = fx.paise_per_usd / 100
        existing = await _existing_keys(session)

        print(f"USD→INR: ₹{usd_inr:.2f} ({fx.source})")
        print(f"Price book: {len(DEFAULT_RATES)} rates, list prices as of {AS_OF}\n")
        # Paise, not rupees: these rates are fractions of a rupee per unit and
        # printing ₹0.0004 for every row would be unreadable.
        print(
            f"{'component':<10} {'provider':<12} {'model':<16} {'unit':<12} "
            f"{'USD/unit':>10} {'paise/unit':>11}  action"
        )
        print("-" * 88)

        to_write = []
        for rate in DEFAULT_RATES:
            key = (rate.provider, rate.model, rate.component.value)
            mpaise = usd_to_mpaise(rate.usd_per_unit, usd_inr=usd_inr)
            already = key in existing

            if already and not args.force:
                action = "skip — already set"
            else:
                action = "replace" if already else "write"
                to_write.append((rate, mpaise))

            print(
                f"{rate.component.value:<10} {rate.provider:<12} "
                f"{(rate.model or '(any)'):<16} {rate.unit.value:<12} "
                f"{rate.usd_per_unit:>10.6f} {mpaise / 1000:>11.4f}  {action}"
            )

        if not args.confirm:
            print(
                f"\n{len(to_write)} rate(s) would be written. "
                "Nothing changed — re-run with --confirm."
            )
            return 0

        if not to_write:
            print("\nEvery rate is already set. Nothing to do.")
            return 0

        for rate, mpaise in to_write:
            await set_provider_rate(
                session,
                actor_user_id=None,
                provider=rate.provider,
                model=rate.model,
                component=rate.component,
                unit=rate.unit,
                rate_mpaise=mpaise,
                effective_from=now,
                note=f"{SEED_NOTE} ({rate.basis})",
            )
        await session.commit()

        print(f"\nWrote {len(to_write)} rate(s).")
        print(
            "Check them at /superadmin/billing/rate-card — every one is a list "
            "price, and yours will differ."
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

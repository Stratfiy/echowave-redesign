"""Load the starter provider price book into an empty rate card.

Without provider rates the cost engine has only the platform fee to work with:
the model picker shows the same number whatever you choose, and every call is
recorded as *uncosted*, not free — so margin reads as 100% and is wrong. This
puts a defensible number against each provider so the machinery works, and
leaves a note on every row saying it was defaulted rather than chosen.

    python -m scripts.seed_provider_rates                 # show what it would do
    python -m scripts.seed_provider_rates --confirm       # write the missing rates
    python -m scripts.seed_provider_rates --confirm --refresh-seeded
                                # also bring rows still on the seeded default
                                # up to this book (an operator's rows stay)
    python -m scripts.seed_provider_rates --confirm --force   # also overwrite existing

**Nothing is written without `--confirm`**, and a rate an operator has already
set is never replaced unless `--force` is passed as well. Prices you chose
outrank prices this file guessed, always — the whole point of a seed is to stop
a fresh install looking broken, not to have an opinion about your contracts.
`--refresh-seeded` is the middle path for a card seeded from an older book:
rows whose note still says "Seeded default" take the current figures, rows
somebody typed do not. The same thing is a button on the rate-card screen.

The logic lives in ``api/services/billing/seed_rates``; this is its shell.

Requires DATABASE_URL, like every repo-owned script:

    set -a && source api/.env && set +a && python -m scripts.seed_provider_rates

In Docker, where there is no checkout to run from:

    docker compose exec api python -m scripts.seed_provider_rates --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from api.db import db_client
from api.services.billing import seed_rates
from api.services.billing.default_rates import AS_OF, DEFAULT_RATES


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
        "--refresh-seeded",
        action="store_true",
        help=(
            "Also replace rows that are still this book's own defaults (told by "
            "their note). A rate an operator set is never touched."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also replace rates that are already set. Rarely what you want.",
    )
    args = parser.parse_args()

    async with db_client.async_session() as session:
        plan = await seed_rates.plan(
            session, force=args.force, refresh_seeded=args.refresh_seeded
        )

        print(f"USD→INR: ₹{plan.usd_inr:.2f} ({plan.fx_source})")
        print(f"Price book: {len(DEFAULT_RATES)} rates, list prices as of {AS_OF}\n")
        # Paise, not rupees: these rates are fractions of a rupee per unit and
        # printing ₹0.0004 for every row would be unreadable.
        print(
            f"{'component':<10} {'provider':<12} {'model':<16} {'unit':<12} "
            f"{'USD/unit':>10} {'paise/unit':>11}  {'checked':<10} action"
        )
        print("-" * 100)
        for line in plan.lines:
            rate = line.rate
            print(
                f"{rate.component.value:<10} {rate.provider:<12} "
                f"{(rate.model or '(any)'):<16} {rate.unit.value:<12} "
                f"{rate.usd_per_unit:>10.6f} {line.rate_mpaise / 1000:>11.4f}  "
                f"{rate.source_checked_on:<10} {line.action}"
            )

        if not args.confirm:
            print(
                f"\n{len(plan.to_write)} rate(s) would be written. "
                "Nothing changed — re-run with --confirm."
            )
            return 0

        if not plan.to_write:
            print("\nEvery rate is already set. Nothing to do.")
            return 0

        written = await seed_rates.apply(
            session,
            actor_user_id=None,
            force=args.force,
            refresh_seeded=args.refresh_seeded,
        )
        await session.commit()

        print(f"\nWrote {len(written.to_write)} rate(s).")
        print(
            "Check them at /superadmin/billing/rate-card — every one is a list "
            "price, and yours will differ."
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

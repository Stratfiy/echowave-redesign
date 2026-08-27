"""Create the sellable plan tiers, and pin each to its Razorpay plan.

``subscription_plans.ensure_seeded`` creates exactly one plan — Starter — and
that is deliberate: it is what a fresh deployment needs to have something to
offer. The rest of the ladder is a commercial decision per deployment, so it
lives here rather than in code every install runs.

    python -m scripts.seed_subscription_plans                  # show what it would do
    python -m scripts.seed_subscription_plans --confirm        # write the missing plans
    python -m scripts.seed_subscription_plans --confirm --force  # also update existing

**Nothing is written without ``--confirm``**, and a plan an operator has already
edited is never replaced unless ``--force`` is passed as well. A price somebody
chose outranks a price this file shipped with.

**The Razorpay ids are per account and must be supplied**, not defaulted. A plan
id from another deployment's dashboard is not a harmless wrong value: it is a
standing instruction to collect somebody else's amount, every month. Pass them
with the ``--<tier>-id`` / ``--<tier>-export-id`` flags; a tier given neither is
still created, and cannot be subscribed to until an operator sets one — a
visible refusal rather than an invisible mis-collection.

The domestic id must point at a plan created at the **gross** and the export id
at one created at the **net**, because a pinned plan's amount is what the bank
is told to take and nothing here can change it. ``mandates._ensure_plan``
re-checks that on every subscribe and refuses a mismatch, so a swapped pair
fails loudly at checkout rather than quietly under-collecting the tax.

Requires DATABASE_URL, like every repo-owned script:

    set -a && source api/.env && set +a && python -m scripts.seed_subscription_plans

In Docker, where there is no checkout to run from:

    docker compose exec api python -m scripts.seed_subscription_plans --confirm
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from api.db import db_client
from api.services.billing import subscription_plans
from api.services.billing.subscription_plans import PlanError

MB = 1024 * 1024


@dataclass(frozen=True)
class TierSeed:
    """One row of the ladder, priced net of GST like everything in the ledger.

    ``price_paise`` is a commercial figure rather than the sum of its parts: a
    plan may discount its contents, and every tier here does. What it may never
    do is grant more balance than it collects, because balance is spendable
    immediately and at our cost — ``subscription_plans.save`` refuses that.
    """

    code: str
    label: str
    blurb: str
    price_paise: int
    balance_paise: int
    included_numbers: int
    knowledge_base_bytes: int
    knowledge_base_max_file_bytes: int
    sort_order: int


#: The ladder as sold. Balances and entitlements rise faster than price, so each
#: step is a better deal per rupee than the one below it — which is the whole
#: reason a customer moves up rather than topping up.
TIERS: tuple[TierSeed, ...] = (
    TierSeed(
        code=subscription_plans.STARTER,
        label="Starter",
        blurb="A phone number and a month of calling, on one monthly payment.",
        price_paise=299_900,
        balance_paise=250_000,
        included_numbers=1,
        knowledge_base_bytes=25 * MB,
        knowledge_base_max_file_bytes=5 * MB,
        sort_order=0,
    ),
    TierSeed(
        code="growth",
        label="Growth",
        blurb="Two numbers and enough calling to run a real campaign.",
        price_paise=799_900,
        balance_paise=720_000,
        included_numbers=2,
        knowledge_base_bytes=100 * MB,
        knowledge_base_max_file_bytes=10 * MB,
        sort_order=10,
    ),
    TierSeed(
        code="scale",
        label="Scale",
        blurb="Four numbers, a large knowledge base, and volume calling.",
        price_paise=1_999_900,
        balance_paise=1_850_000,
        included_numbers=4,
        knowledge_base_bytes=500 * MB,
        knowledge_base_max_file_bytes=25 * MB,
        sort_order=20,
    ),
)


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


def _mb(value: int) -> int:
    return value // MB


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the sellable plan tiers and pin their Razorpay plans."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually write. Without it, prints what would be written and exits.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also update plans that already exist. Overwrites operator edits.",
    )
    for tier in TIERS:
        parser.add_argument(
            f"--{tier.code}-id",
            dest=f"{tier.code}_id",
            help=f"Razorpay plan id for {tier.label}, created at the GROSS.",
        )
        parser.add_argument(
            f"--{tier.code}-export-id",
            dest=f"{tier.code}_export_id",
            help=f"Razorpay plan id for {tier.label} exports, created at the NET.",
        )
    args = parser.parse_args()

    print(
        f"{'code':<10} {'price':>10} {'balance':>10} {'nums':>5} {'KB':>8} "
        f"{'file':>6}  razorpay"
    )
    print("-" * 78)

    written = 0
    async with db_client.async_session() as session:
        for tier in TIERS:
            existing = await subscription_plans.get_plan(session, code=tier.code)
            razorpay_id = getattr(args, f"{tier.code}_id", None)
            export_id = getattr(args, f"{tier.code}_export_id", None)

            # An id already on the row outranks an unsupplied flag, so re-running
            # without the flags does not blank plans that were pinned earlier.
            if existing is not None:
                razorpay_id = razorpay_id or existing.razorpay_plan_id
                export_id = export_id or existing.razorpay_plan_id_export

            pinned = razorpay_id or "— none, cannot be subscribed to"
            if not existing:
                state = "new"
            elif args.force:
                state = "exists, replacing"
            elif getattr(args, f"{tier.code}_id", None) or getattr(
                args, f"{tier.code}_export_id", None
            ):
                state = "exists, pinning ids only"
            else:
                state = "exists, unchanged"

            print(
                f"{tier.code:<10} {_rupees(tier.price_paise):>10} "
                f"{_rupees(tier.balance_paise):>10} {tier.included_numbers:>5} "
                f"{_mb(tier.knowledge_base_bytes):>6}MB "
                f"{_mb(tier.knowledge_base_max_file_bytes):>4}MB  {pinned}  [{state}]"
            )

            if not args.confirm:
                continue

            # An existing plan gets its provider ids pinned but keeps its
            # prices. A null id is "not configured yet", not a choice somebody
            # made, so filling one in is not overwriting an operator -- whereas
            # rewriting the whole row through save() would silently reset a
            # price they had edited.
            if existing and not args.force:
                supplied = (
                    getattr(args, f"{tier.code}_id", None),
                    getattr(args, f"{tier.code}_export_id", None),
                )
                if not any(supplied):
                    continue
                try:
                    await subscription_plans.set_provider_plan_ids(
                        session,
                        code=tier.code,
                        razorpay_plan_id=supplied[0],
                        razorpay_plan_id_export=supplied[1],
                    )
                except PlanError as exc:
                    print(f"  ! {tier.code} refused: {exc}")
                    continue
                written += 1
                continue

            try:
                await subscription_plans.save(
                    session,
                    code=tier.code,
                    label=tier.label,
                    blurb=tier.blurb,
                    price_paise=tier.price_paise,
                    balance_paise=tier.balance_paise,
                    included_numbers=tier.included_numbers,
                    knowledge_base_bytes=tier.knowledge_base_bytes,
                    knowledge_base_max_file_bytes=tier.knowledge_base_max_file_bytes,
                    razorpay_plan_id=razorpay_id,
                    razorpay_plan_id_export=export_id,
                    sort_order=tier.sort_order,
                )
            except PlanError as exc:
                # Refused rather than written: every one of these guards exists
                # because the value it rejects loses money every cycle and looks
                # plausible on the way in.
                print(f"  ! {tier.code} refused: {exc}")
                continue
            written += 1

        if args.confirm and written:
            await session.commit()

    print()
    if not args.confirm:
        print("Nothing written. Re-run with --confirm.")
        return 0

    print(f"Wrote {written} plan(s).")

    # Read back rather than inferred from the flags. A tier pinned on an earlier
    # run is pinned whether or not this run passed the flag again, and warning
    # off the arguments said the opposite -- alarming, and wrong.
    async with db_client.async_session() as session:
        unpinned = [
            tier.code
            for tier in TIERS
            if (plan := await subscription_plans.get_plan(session, code=tier.code))
            is not None
            and not plan.razorpay_plan_id
        ]
    if unpinned:
        print(
            "No Razorpay plan pinned for: "
            + ", ".join(unpinned)
            + ". Those tiers cannot be subscribed to until one is set."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

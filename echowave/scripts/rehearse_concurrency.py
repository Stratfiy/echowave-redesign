"""Prove the spend ceiling holds on this deployment, before a campaign does.

``api/tests/test_billing_reservations.py`` proves the reservation logic. It
cannot prove the three things that are properties of a *deployment* rather than
of the code, and each one fails the same way: an account spends money it does
not have, at the exact moment nobody is watching, which is the middle of a
campaign.

    1. **Enforcement is switched on.** ``BALANCE_ENFORCEMENT_ENABLED=false`` is
       a valid setting and makes ``reserve`` a no-op that returns None for
       every call. Nothing else in the product looks different.
    2. **The database serializes the lock.** ``SELECT ... FOR UPDATE`` is the
       whole mechanism, and it is a property of the server and the isolation
       level in front of it — a connection pooler in transaction mode, a read
       replica that a session accidentally routes to, an isolation level
       someone changed. None of that exists in the test environment.
    3. **The sweeper reaches the stranded holds.** A worker that dies mid-call
       leaves funds held against a call that will never be costed. On a
       deployment where the sweeper is not scheduled, that is a slow leak that
       ends with a paying customer unable to place a call.

This rehearses all three against the database this deployment actually uses,
with real concurrent transactions, on a throwaway organization it creates and
then deletes. **It places no calls and spends no money at a provider.** The
credit it grants is to an account nobody can sign in to, and every row it
writes is removed before it exits.

One effect reaches beyond the throwaway account: check three runs the real
sweeper, which releases stale holds across the whole deployment. That is what
the scheduled job does anyway, and it returns funds to accounts rather than
taking them, but it is not a dry run.

    # On the deployed box, which is the only place the answers mean anything:
    docker compose exec api python -m scripts.rehearse_concurrency

    # Rehearse a specific campaign's shape: 40 simultaneous starts.
    docker compose exec api python -m scripts.rehearse_concurrency --calls 40

Exit status is 0 when everything passed, 1 otherwise, so it can sit in a
deploy gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.constants import (
    BALANCE_ENFORCEMENT_ENABLED,
    MIN_BALANCE_PAISE,
    RESERVATION_MAX_AGE_MINUTES,
)
from api.db import db_client
from api.db.models import (
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind
from api.services.billing import reservations

RUPEE = 100  # paise

PASS = "  ok  "
FAIL = " FAIL "
WARN = " warn "

_results: list[tuple[str, str, str]] = []


def record(mark: str, title: str, detail: str = "") -> None:
    _results.append((mark, title, detail))
    print(f"[{mark}] {title}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


class Rehearsal:
    """A throwaway organization with a known cost per minute.

    The estimate is pinned through data — a rollup saying this account's calls
    have cost ₹2 a minute — rather than by patching the estimator, because the
    point is to exercise the estimator this deployment will actually use.
    """

    #: ₹2 a minute over the reservation window. With RESERVATION_MINUTES at its
    #: default of 5 that is a ₹10 hold per call, so the credit granted below
    #: covers a known, small number of simultaneous starts.
    PAISE_PER_MINUTE = 2 * RUPEE

    def __init__(self, maker, *, calls: int):
        self.maker = maker
        self.calls = calls
        self.tag = f"rehearsal-{uuid.uuid4()}"
        self.organization_id: int | None = None
        self.run_ids: list[int] = []
        self.hold_paise = 0
        self.credit_paise = 0

    async def build(self, affordable: int) -> None:
        """Create the account, funded for exactly ``affordable`` calls."""
        async with self.maker() as session:
            org = OrganizationModel(provider_id=self.tag, quota_decibyl_tokens=0)
            session.add(org)
            await session.flush()

            # 100 calls, 6000 billable seconds, ₹200 — ₹2 a minute.
            session.add(
                DailyOrganizationRollupModel(
                    organization_id=org.id,
                    day=datetime.now(UTC).date(),
                    calls=100,
                    billable_seconds=6_000,
                    charged_paise=200 * RUPEE,
                )
            )
            await session.flush()

            self.hold_paise = await reservations.estimate_paise(
                session, organization_id=org.id
            )
            # Funded for exactly ``affordable`` simultaneous starts, and the
            # arithmetic has to account for the floor.
            #
            # Calling stops *below* MIN_BALANCE_PAISE, not below zero, so an
            # account holding one hold's worth of credit cannot spend it. A
            # start is allowed while the balance before it is at or above the
            # floor, so the last one we want permitted needs the floor still
            # standing underneath it: floor + hold x (affordable - 1). Funding
            # hold x affordable — the obvious figure, and what this used to do —
            # buys one fewer call than it looks like, which is precisely the
            # off-by-one this rehearsal exists to catch in production rather
            # than reproduce in itself.
            self.credit_paise = MIN_BALANCE_PAISE + self.hold_paise * (
                affordable - 1
            )
            session.add(
                CreditLedgerModel(
                    organization_id=org.id,
                    delta_paise=self.credit_paise,
                    kind=CreditLedgerKind.TOPUP.value,
                    balance_after_paise=self.credit_paise,
                    note=self.tag,
                )
            )

            user = UserModel(provider_id=f"user-{self.tag}")
            session.add(user)
            await session.flush()
            workflow = WorkflowModel(
                name=self.tag,
                user_id=user.id,
                organization_id=org.id,
                workflow_definition={},
                template_context_variables={},
                call_disposition_codes={},
            )
            session.add(workflow)
            await session.flush()

            runs = [
                WorkflowRunModel(
                    name=f"{self.tag}-{index}", workflow_id=workflow.id, mode="twilio"
                )
                for index in range(self.calls)
            ]
            session.add_all(runs)
            await session.flush()

            self.organization_id = org.id
            self.run_ids = [run.id for run in runs]
            await session.commit()

    async def start_all(self) -> int:
        """Fire every start at once. Returns how many were allowed.

        Each start gets its own session and commits on its own, because the
        row lock only means anything across real concurrent transactions — a
        rehearsal run inside one transaction proves nothing about the lock.
        """

        async def start(run_id: int) -> bool:
            async with self.maker() as session:
                held = await reservations.reserve(
                    session,
                    organization_id=self.organization_id,
                    workflow_run_id=run_id,
                )
                await session.commit()
                return held is not None

        allowed = await asyncio.gather(*(start(run_id) for run_id in self.run_ids))
        return sum(allowed)

    async def balance(self) -> int:
        async with self.maker() as session:
            return await reservations.current_balance_paise(
                session, organization_id=self.organization_id
            )

    async def held_rows(self) -> int:
        async with self.maker() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(CreditLedgerModel)
                    .where(
                        CreditLedgerModel.organization_id == self.organization_id,
                        CreditLedgerModel.kind == CreditLedgerKind.RESERVATION.value,
                    )
                )
            )

    async def age_the_holds(self) -> None:
        """Backdate every hold past the sweeper's cut-off.

        Waiting three hours for RESERVATION_MAX_AGE_MINUTES to pass is not a
        rehearsal anybody runs, so the clock is moved instead of the operator.
        """
        async with self.maker() as session:
            cutoff = (
                datetime.now(UTC).timestamp() - (RESERVATION_MAX_AGE_MINUTES + 60) * 60
            )
            await session.execute(
                CreditLedgerModel.__table__.update()
                .where(
                    CreditLedgerModel.organization_id == self.organization_id,
                    CreditLedgerModel.kind == CreditLedgerKind.RESERVATION.value,
                )
                .values(created_at=datetime.fromtimestamp(cutoff, tz=UTC))
            )
            await session.commit()

    async def sweep(self) -> tuple[int, int]:
        async with self.maker() as session:
            swept = await reservations.sweep_stale(session)
            await session.commit()
            return swept

    async def clean_up(self) -> None:
        """Remove every row this rehearsal wrote.

        Runs even when a check failed — a rehearsal that leaves a funded
        organization behind on a production database is worse than the doubt
        it was resolving.
        """
        if self.organization_id is None:
            return

        async with self.maker() as session:
            await session.execute(
                delete(WorkflowRunModel).where(
                    WorkflowRunModel.workflow_id.in_(
                        select(WorkflowModel.id).where(
                            WorkflowModel.organization_id == self.organization_id
                        )
                    )
                )
            )
            await session.execute(
                delete(WorkflowModel).where(
                    WorkflowModel.organization_id == self.organization_id
                )
            )
            await session.execute(
                delete(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == self.organization_id
                )
            )
            await session.execute(
                delete(DailyOrganizationRollupModel).where(
                    DailyOrganizationRollupModel.organization_id == self.organization_id
                )
            )
            await session.execute(
                delete(UserModel).where(UserModel.provider_id == f"user-{self.tag}")
            )
            await session.execute(
                delete(OrganizationModel).where(
                    OrganizationModel.id == self.organization_id
                )
            )
            await session.commit()


async def run(calls: int, affordable: int) -> None:
    if not BALANCE_ENFORCEMENT_ENABLED:
        record(
            FAIL,
            "Balance enforcement is switched off on this deployment",
            "BALANCE_ENFORCEMENT_ENABLED is false, so reserve() is a no-op and\n"
            "an account with no credit can place unlimited concurrent calls.\n"
            "Nothing else below can pass meaningfully. Set it to true and\n"
            "restart the API and workers.",
        )
        return

    record(PASS, "Balance enforcement is switched on")

    maker = async_sessionmaker(bind=db_client.engine, expire_on_commit=False)
    rehearsal = Rehearsal(maker, calls=calls)

    try:
        await rehearsal.build(affordable)
        record(
            PASS,
            f"Throwaway account funded for exactly {affordable} call(s)",
            f"estimate {rehearsal.hold_paise / 100:.2f} rupees per call, "
            f"credit {rehearsal.credit_paise / 100:.2f} rupees, "
            f"{calls} starts to fire",
        )

        allowed = await rehearsal.start_all()
        if allowed == affordable:
            record(
                PASS,
                f"{allowed} of {calls} simultaneous starts were allowed",
                "The per-organization lock serialized them: the account could "
                "afford\nexactly this many and got exactly this many.",
            )
        else:
            record(
                FAIL,
                f"{allowed} of {calls} simultaneous starts were allowed, "
                f"expected {affordable}",
                "More than the balance covers means the row lock is not "
                "serializing\nstarts on this database — check for a connection "
                "pooler in\ntransaction mode, a read replica, or a changed "
                "isolation level.\nFewer means calls are being refused that the "
                "account can pay for.",
            )

        balance = await rehearsal.balance()
        if balance >= 0:
            record(
                PASS,
                f"Balance after the burst is {balance / 100:.2f} rupees",
                "Never negative: every allowed start is already subtracted.",
            )
        else:
            record(
                FAIL,
                f"Balance after the burst is {balance / 100:.2f} rupees",
                "An account spent credit it did not have.",
            )

        stranded = await rehearsal.held_rows()
        await rehearsal.age_the_holds()
        # This is the real sweeper, so it releases every stale hold on the
        # deployment, not only this rehearsal's. That is the behaviour the
        # scheduled job performs anyway and it returns funds rather than taking
        # them — but it is a real effect on real accounts, so the counts below
        # are reported as "at least ours" rather than as ours alone.
        rows, paise = await rehearsal.sweep()
        remaining = await rehearsal.held_rows()

        if remaining == 0 and rows >= stranded:
            record(
                PASS,
                f"The sweeper released this account's {stranded} stranded hold(s)",
                "A worker dying mid-call returns its held funds to the account.\n"
                f"It swept {rows} row(s) worth {paise / 100:.2f} rupees in "
                "total — the\nsweeper is deployment-wide, so any other stale "
                "holds went too.",
            )
        else:
            record(
                FAIL,
                f"{remaining} hold(s) survived the sweep",
                "Funds held against calls that will never be costed do not come "
                "back.\nThis leaks spending power until a paying customer "
                "cannot place a call.",
            )

        recovered = await rehearsal.balance()
        if recovered == rehearsal.credit_paise:
            record(
                PASS,
                f"Full balance recovered: {recovered / 100:.2f} rupees",
                "The ledger nets to exactly what was paid in.",
            )
        else:
            record(
                WARN,
                f"Balance after the sweep is {recovered / 100:.2f} rupees, "
                f"paid in {rehearsal.credit_paise / 100:.2f}",
                "Holds are released rather than charged, so these should match.",
            )
    finally:
        await rehearsal.clean_up()
        record(PASS, "Rehearsal account and every row it wrote were removed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse the concurrent spend ceiling against this deployment."
    )
    parser.add_argument(
        "--calls",
        type=int,
        default=12,
        help="How many starts to fire at once (default: 12).",
    )
    parser.add_argument(
        "--affordable",
        type=int,
        default=3,
        help="How many of them the account is funded for (default: 3).",
    )
    args = parser.parse_args()

    if args.affordable >= args.calls:
        print("--affordable must be fewer than --calls, or nothing is refused.")
        return 1

    asyncio.run(run(args.calls, args.affordable))

    print()
    failures = [row for row in _results if row[0] == FAIL]
    if failures:
        print(f"{len(failures)} check(s) failed.")
        return 1
    print("Every check passed. The spend ceiling holds on this deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

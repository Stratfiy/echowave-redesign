"""Holding funds while a call is in flight.

The test that matters most is the concurrent one. A balance check that passes
every sequential test still lets an account with ₹10 start fifty simultaneous
calls, because each of them reads the balance before any of them writes — and
concurrency is what a voice platform sells, so that is the normal case rather
than the edge.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

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
from api.services.billing.costing import current_balance_paise

RUPEE = 100


@pytest.fixture(autouse=True)
def enforcement_on(monkeypatch):
    """Enforcement is on by default in production; make that explicit here."""
    monkeypatch.setattr(reservations, "BALANCE_ENFORCEMENT_ENABLED", True)
    monkeypatch.setattr(reservations, "RESERVATION_MINUTES", 5)
    monkeypatch.setattr(reservations, "RESERVATION_MAX_AGE_MINUTES", 180)


async def _org(async_session, slug: str, *, balance_paise: int = 0):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()

    if balance_paise:
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=balance_paise,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=balance_paise,
            )
        )
        await async_session.flush()
    return org


async def _run(async_session, org, *, name: str = "run"):
    user = UserModel(provider_id=f"user-{org.provider_id}-{name}")
    async_session.add(user)
    await async_session.flush()

    workflow = WorkflowModel(
        name=f"wf-{name}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()

    run = WorkflowRunModel(name=name, workflow_id=workflow.id, mode="twilio")
    async_session.add(run)
    await async_session.flush()
    return run


async def _reservation_rows(async_session, org) -> int:
    return int(
        await async_session.scalar(
            select(func.count())
            .select_from(CreditLedgerModel)
            .where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.RESERVATION.value,
            )
        )
        or 0
    )


@pytest.mark.asyncio
class TestRefusingAnUnfundedAccount:
    async def test_a_zero_balance_is_refused(self, async_session):
        org = await _org(async_session, "zero")
        run = await _run(async_session, org)

        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert held is None
        assert await _reservation_rows(async_session, org) == 0

    async def test_a_negative_balance_is_refused(self, async_session):
        """An account already overdrawn — the last call overran its hold — does
        not get to keep going."""
        org = await _org(async_session, "negative", balance_paise=-5_000)
        run = await _run(async_session, org)

        assert (
            await reservations.reserve(
                async_session, organization_id=org.id, workflow_run_id=run.id
            )
            is None
        )

    async def test_has_credit_agrees_with_reserve(self, async_session):
        funded = await _org(async_session, "hascredit", balance_paise=100 * RUPEE)
        empty = await _org(async_session, "nocredit")

        assert await reservations.has_credit(async_session, organization_id=funded.id)
        assert not await reservations.has_credit(
            async_session, organization_id=empty.id
        )


@pytest.mark.asyncio
class TestHoldingFunds:
    async def test_a_hold_lowers_the_balance_immediately(self, async_session):
        """The property the whole design rests on: a reservation is an ordinary
        ledger row, so no balance query can forget to count it."""
        org = await _org(async_session, "holds", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)

        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert held is not None
        assert held.amount_paise > 0
        balance = await current_balance_paise(async_session, organization_id=org.id)
        assert balance == 1_000 * RUPEE - held.amount_paise

    async def test_a_retried_start_does_not_stack_a_second_hold(self, async_session):
        """Runtime entrypoints retry. Two holds for one call would take spending
        power away twice and only one would ever be released."""
        org = await _org(async_session, "retry", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)

        first = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        second = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert first is not None and second is not None
        assert first.ledger_id == second.ledger_id
        assert await _reservation_rows(async_session, org) == 1
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 1_000 * RUPEE - first.amount_paise
        )

    async def test_a_thin_balance_holds_what_is_left_rather_than_refusing(
        self, async_session
    ):
        """₹5 left buys one more call, not a refusal.

        Refusing anything short of a full estimate would strand credit the
        customer has already paid for.
        """
        org = await _org(async_session, "thin", balance_paise=5 * RUPEE)
        run = await _run(async_session, org)

        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert held is not None
        assert held.amount_paise == 5 * RUPEE
        assert await current_balance_paise(async_session, organization_id=org.id) == 0

    async def test_enforcement_off_holds_nothing(self, async_session, monkeypatch):
        monkeypatch.setattr(reservations, "BALANCE_ENFORCEMENT_ENABLED", False)
        org = await _org(async_session, "disabled")
        run = await _run(async_session, org)

        assert (
            await reservations.reserve(
                async_session, organization_id=org.id, workflow_run_id=run.id
            )
            is None
        )
        assert await _reservation_rows(async_session, org) == 0
        # And an unfunded account is not refused, which is the point of the flag.
        assert await reservations.has_credit(async_session, organization_id=org.id)


@pytest.mark.asyncio
class TestConcurrentStarts:
    """The reason this module exists.

    A bare balance check passes every sequential test above and still fails
    here, because each caller reads the balance before any of them writes.
    """

    async def test_concurrent_starts_cannot_all_spend_the_same_balance(
        self, test_engine, setup_test_database
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(bind=test_engine, expire_on_commit=False)

        # Committed rather than in a test savepoint: the row lock only means
        # anything across real concurrent transactions.
        async with maker() as setup:
            org = OrganizationModel(
                provider_id=f"org-concurrent-{datetime.now(UTC).timestamp()}",
                quota_decibyl_tokens=0,
            )
            setup.add(org)
            await setup.flush()
            setup.add(
                CreditLedgerModel(
                    organization_id=org.id,
                    # Enough for exactly two holds, given the history below.
                    delta_paise=20 * RUPEE,
                    kind=CreditLedgerKind.TOPUP.value,
                    balance_after_paise=20 * RUPEE,
                )
            )
            # Pin the estimate through data rather than by patching the module:
            # eight concurrent tasks saving and restoring a module global race
            # with each other, and one of them ends up restoring another's
            # patched function permanently. ₹2 a minute over five minutes is a
            # ₹10 hold, so ₹20 of credit covers exactly two calls.
            setup.add(
                DailyOrganizationRollupModel(
                    organization_id=org.id,
                    day=datetime.now(UTC).date(),
                    calls=100,
                    billable_seconds=6_000,
                    charged_paise=200 * RUPEE,
                )
            )
            user = UserModel(provider_id=f"user-concurrent-{org.id}")
            setup.add(user)
            await setup.flush()
            workflow = WorkflowModel(
                name="wf-concurrent",
                user_id=user.id,
                organization_id=org.id,
                workflow_definition={},
                template_context_variables={},
                call_disposition_codes={},
            )
            setup.add(workflow)
            await setup.flush()
            runs = [
                WorkflowRunModel(
                    name=f"run-{i}", workflow_id=workflow.id, mode="twilio"
                )
                for i in range(8)
            ]
            setup.add_all(runs)
            await setup.flush()
            org_id = org.id
            run_ids = [r.id for r in runs]
            await setup.commit()

        async def start(run_id: int) -> bool:
            async with maker() as session:
                held = await reservations.reserve(
                    session, organization_id=org_id, workflow_run_id=run_id
                )
                await session.commit()
                return held is not None

        try:
            results = await asyncio.gather(*(start(rid) for rid in run_ids))

            async with maker() as check:
                balance = await current_balance_paise(check, organization_id=org_id)
                held_rows = int(
                    await check.scalar(
                        select(func.count())
                        .select_from(CreditLedgerModel)
                        .where(
                            CreditLedgerModel.organization_id == org_id,
                            CreditLedgerModel.kind
                            == CreditLedgerKind.RESERVATION.value,
                        )
                    )
                    or 0
                )

            # Eight simultaneous starts, ₹20 of credit, ₹10 held per call.
            # Without the lock every one of them succeeds.
            assert sum(results) == 2, (
                f"{sum(results)} of 8 concurrent starts were allowed on a "
                "balance that covers 2"
            )
            assert held_rows == 2
            assert balance == 0
        finally:
            async with maker() as cleanup:
                await cleanup.execute(
                    CreditLedgerModel.__table__.delete().where(
                        CreditLedgerModel.organization_id == org_id
                    )
                )
                await cleanup.execute(
                    DailyOrganizationRollupModel.__table__.delete().where(
                        DailyOrganizationRollupModel.organization_id == org_id
                    )
                )
                await cleanup.execute(
                    WorkflowRunModel.__table__.delete().where(
                        WorkflowRunModel.id.in_(run_ids)
                    )
                )
                await cleanup.commit()


@pytest.mark.asyncio
class TestReleasing:
    async def test_releasing_returns_the_funds(self, async_session):
        org = await _org(async_session, "release", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        released = await reservations.release(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert released == held.amount_paise
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 1_000 * RUPEE
        )

    async def test_releasing_leaves_no_trace_on_the_statement(self, async_session):
        """A hold and its reversal on a customer's statement reads as being
        charged twice for something that never charged them once."""
        org = await _org(async_session, "notrace", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        await reservations.release(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        assert await _reservation_rows(async_session, org) == 0

    async def test_releasing_a_run_with_no_hold_is_harmless(self, async_session):
        """The normal case for anything costed before enforcement existed."""
        org = await _org(async_session, "nohold", balance_paise=100 * RUPEE)
        run = await _run(async_session, org)

        assert (
            await reservations.release(
                async_session, organization_id=org.id, workflow_run_id=run.id
            )
            == 0
        )
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 100 * RUPEE
        )


@pytest.mark.asyncio
class TestSweepingStrandedHolds:
    async def test_a_hold_on_a_costed_run_is_swept(self, async_session):
        """A crash between releasing and debiting leaves this behind."""
        org = await _org(async_session, "sweepcosted", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        run.costed_at = datetime.now(UTC)
        await async_session.flush()

        rows, paise = await reservations.sweep_stale(async_session)

        assert rows == 1
        assert paise > 0
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 1_000 * RUPEE
        )

    async def test_a_hold_older_than_the_cutoff_is_swept(self, async_session):
        """The worker died and the run will never complete."""
        org = await _org(async_session, "sweepold", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        row = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.id == held.ledger_id)
        )
        row.created_at = datetime.now(UTC) - timedelta(minutes=999)
        await async_session.flush()

        rows, _ = await reservations.sweep_stale(async_session)

        assert rows == 1
        assert await _reservation_rows(async_session, org) == 0

    async def test_a_live_call_keeps_its_hold(self, async_session):
        """Sweeping a live call's hold would put the account back over its
        balance while it is still spending."""
        org = await _org(async_session, "sweeplive", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

        rows, _ = await reservations.sweep_stale(async_session)

        assert rows == 0
        assert await _reservation_rows(async_session, org) == 1
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 1_000 * RUPEE - held.amount_paise
        )


@pytest.mark.asyncio
class TestEstimating:
    async def test_it_measures_the_account_rather_than_guessing(self, async_session):
        """An account whose calls cost ₹4 a minute holds against ₹4, not
        against someone else's average."""
        org = await _org(async_session, "measured", balance_paise=100_000 * RUPEE)
        async_session.add(
            DailyOrganizationRollupModel(
                organization_id=org.id,
                day=datetime.now(UTC).date(),
                calls=100,
                billable_seconds=6_000,  # 100 minutes
                charged_paise=400 * RUPEE,  # Rs 4.00 a minute
            )
        )
        await async_session.flush()

        estimate = await reservations.estimate_paise(
            async_session, organization_id=org.id
        )

        assert estimate == 4 * RUPEE * reservations.RESERVATION_MINUTES

    async def test_it_falls_back_when_there_is_no_history(self, async_session):
        """A first call has nothing to measure. The fallback is priced off the
        account's own platform rate, not a flat constant, so an enterprise on a
        negotiated rate is not measured against someone else's."""
        org = await _org(async_session, "firstcall", balance_paise=100_000 * RUPEE)

        estimate = await reservations.estimate_paise(
            async_session, organization_id=org.id
        )

        assert estimate > 0

    async def test_a_stale_rollup_is_not_measured(self, async_session):
        """History older than the window belongs to a different stack."""
        org = await _org(async_session, "stalehistory", balance_paise=100_000 * RUPEE)
        async_session.add(
            DailyOrganizationRollupModel(
                organization_id=org.id,
                day=(
                    datetime.now(UTC)
                    - timedelta(days=reservations.COST_HISTORY_DAYS + 5)
                ).date(),
                calls=100,
                billable_seconds=6_000,
                charged_paise=400 * RUPEE,
            )
        )
        await async_session.flush()

        estimate = await reservations.estimate_paise(
            async_session, organization_id=org.id
        )

        assert estimate != 4 * RUPEE * reservations.RESERVATION_MINUTES


@pytest.mark.asyncio
class TestCostingReleasesTheHold:
    """The pairing that makes the ledger exact.

    An account is billed for what it used, never for what we guessed when the
    call started — so the hold has to disappear as the charge lands, and in the
    same transaction.
    """

    async def test_costing_replaces_the_hold_with_the_real_charge(self, async_session):
        from api.services.billing.costing import cost_workflow_run

        org = await _org(async_session, "costrelease", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        assert held is not None

        run.billable_seconds = 60
        await async_session.flush()
        cost = await cost_workflow_run(async_session, run.id)
        assert cost is not None

        # No hold left, and exactly one usage debit.
        assert await _reservation_rows(async_session, org) == 0
        usage_rows = int(
            await async_session.scalar(
                select(func.count())
                .select_from(CreditLedgerModel)
                .where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
                )
            )
            or 0
        )
        assert usage_rows == 1

        # The balance reflects the charge, not the guess.
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 1_000 * RUPEE - cost.total_charged_paise
        )

    async def test_a_costed_call_is_never_charged_the_estimate(self, async_session):
        """A hold larger than the real cost must not leave the difference held.

        Five minutes is reserved; a one-minute call costs a fifth of it. If the
        release were missing the customer would silently lose the rest until the
        sweeper ran.
        """
        from api.services.billing.costing import cost_workflow_run

        org = await _org(async_session, "overheld", balance_paise=1_000 * RUPEE)
        run = await _run(async_session, org)
        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        run.billable_seconds = 60
        await async_session.flush()

        cost = await cost_workflow_run(async_session, run.id)

        assert cost.total_charged_paise < held.amount_paise
        balance = await current_balance_paise(async_session, organization_id=org.id)
        assert balance > 1_000 * RUPEE - held.amount_paise


@pytest.mark.asyncio
class TestReAuthorizingALiveCall:
    """A call already holding funds must not be refused for lack of balance.

    Found by running the gate against a real database rather than by a unit
    test: every runtime entrypoint can re-authorize the same run — a websocket
    reconnect, an ARI retry — and by then the hold it took has usually driven
    the balance to exactly the point where a bare check refuses it.
    """

    async def test_a_run_holding_funds_passes_even_at_zero_balance(self, async_session):
        org = await _org(async_session, "reauth", balance_paise=10 * RUPEE)
        run = await _run(async_session, org)

        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        assert held is not None
        assert await current_balance_paise(async_session, organization_id=org.id) == 0

        # Without the run id this reads as an unfunded account.
        assert not await reservations.has_credit(async_session, organization_id=org.id)
        # With it, the call it already paid for is allowed to carry on.
        assert await reservations.has_credit(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )

    async def test_a_different_run_is_still_refused(self, async_session):
        """The exemption is for *this* call, not a licence to start more."""
        org = await _org(async_session, "reauthother", balance_paise=10 * RUPEE)
        live = await _run(async_session, org, name="live")
        other = await _run(async_session, org, name="other")

        await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=live.id
        )

        assert not await reservations.has_credit(
            async_session, organization_id=org.id, workflow_run_id=other.id
        )

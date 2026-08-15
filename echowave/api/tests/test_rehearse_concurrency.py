"""The rehearsal script, rehearsed.

``scripts/rehearse_concurrency.py`` exists to be believed on a deploy morning:
somebody runs it, reads "every check passed", and starts a campaign. A
rehearsal that reports success no matter what is worse than no rehearsal,
because it converts an open question into a wrong answer.

So this pins the two things that make it worth running — that a real burst of
concurrent starts is held to the balance, and that it says so *only when that
is true* — and the thing that makes it safe to run on a production box: it
removes everything it wrote.

It exercises the script's own machinery against the test database with real
concurrent transactions, which is the same thing the script does. The parts
not covered here are the ones that are properties of a deployment rather than
of code — a connection pooler in transaction mode, a changed isolation level —
and those are exactly what the script is for.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind


def _load_script():
    """Import the script by path — `scripts` is not an installed package."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_concurrency.py"
    spec = importlib.util.spec_from_file_location("rehearse_concurrency", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


@pytest.fixture
def maker(test_engine):
    """Sessions that really commit.

    The whole point is concurrent transactions contending for a row lock, so
    the transactional test session would prove nothing here.
    """
    return async_sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture
async def rehearsal(maker):
    """A built rehearsal, cleaned up however the test ends."""
    built = script.Rehearsal(maker, calls=8)
    try:
        yield built
    finally:
        await built.clean_up()


@pytest.mark.asyncio
class TestTheBurst:
    async def test_only_what_the_balance_covers_is_allowed(
        self, setup_test_database, rehearsal
    ):
        """The claim the script makes. Eight starts at once, credit for three."""
        await rehearsal.build(affordable=3)

        allowed = await rehearsal.start_all()

        assert allowed == 3, (
            f"{allowed} of 8 simultaneous starts were allowed on an account "
            "funded for three"
        )

    async def test_the_balance_never_goes_negative(
        self, setup_test_database, rehearsal
    ):
        await rehearsal.build(affordable=2)
        await rehearsal.start_all()

        assert await rehearsal.balance() >= 0

    async def test_an_account_with_credit_for_one_call_gets_one(
        self, setup_test_database, rehearsal
    ):
        """The boundary. Off by one here either strands a customer's credit or
        lets a burst through."""
        await rehearsal.build(affordable=1)

        assert await rehearsal.start_all() == 1


@pytest.mark.asyncio
class TestTheSweep:
    async def test_stranded_holds_come_back(self, setup_test_database, rehearsal):
        """A worker dying mid-call must not keep the money held forever."""
        await rehearsal.build(affordable=3)
        await rehearsal.start_all()
        assert await rehearsal.held_rows() == 3

        await rehearsal.age_the_holds()
        await rehearsal.sweep()

        assert await rehearsal.held_rows() == 0
        assert await rehearsal.balance() == rehearsal.credit_paise

    async def test_a_fresh_hold_is_not_swept(self, setup_test_database, rehearsal):
        """The dangerous direction. Sweeping a live call's hold puts the account
        back over its balance while it is still spending."""
        await rehearsal.build(affordable=3)
        await rehearsal.start_all()

        await rehearsal.sweep()  # without ageing them first

        assert await rehearsal.held_rows() == 3


@pytest.mark.asyncio
class TestItCleansUpAfterItself:
    async def test_nothing_survives_the_rehearsal(self, setup_test_database, maker):
        """It runs against production databases. A rehearsal that leaves a
        funded organization behind is worse than the doubt it resolved."""
        run = script.Rehearsal(maker, calls=6)
        await run.build(affordable=2)
        await run.start_all()
        organization_id = run.organization_id

        await run.clean_up()

        async with maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OrganizationModel)
                    .where(OrganizationModel.id == organization_id)
                )
            ) == 0
            for model in (CreditLedgerModel, WorkflowModel):
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.organization_id == organization_id)
                    )
                ) == 0
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WorkflowRunModel)
                    .where(WorkflowRunModel.id.in_(run.run_ids))
                )
            ) == 0

    async def test_cleanup_runs_even_when_the_rehearsal_failed(
        self, setup_test_database, maker
    ):
        """Cleanup is in a finally block for this reason, and a rehearsal that
        only tidies up on the happy path tidies up on the wrong days."""
        run = script.Rehearsal(maker, calls=4)
        await run.build(affordable=1)
        organization_id = run.organization_id

        with pytest.raises(RuntimeError):
            try:
                raise RuntimeError("a check failed midway")
            finally:
                await run.clean_up()

        async with maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OrganizationModel)
                    .where(OrganizationModel.id == organization_id)
                )
            ) == 0


@pytest.mark.asyncio
class TestTheRefusalItCannotHide:
    async def test_enforcement_switched_off_is_reported_as_a_failure(
        self, monkeypatch, capsys
    ):
        """BALANCE_ENFORCEMENT_ENABLED=false makes reserve() a no-op, so every
        other check would pass vacuously. The script has to say so rather than
        print a page of ticks."""
        monkeypatch.setattr(script, "BALANCE_ENFORCEMENT_ENABLED", False)
        monkeypatch.setattr(script, "_results", [])

        await script.run(calls=8, affordable=2)

        marks = [mark for mark, _, _ in script._results]
        assert script.FAIL in marks
        assert "enforcement is switched off" in capsys.readouterr().out.lower()

    async def test_it_writes_nothing_when_enforcement_is_off(
        self, monkeypatch, setup_test_database, maker
    ):
        """It refuses before creating an account, so a misconfigured box does
        not accumulate rehearsal organizations."""
        monkeypatch.setattr(script, "BALANCE_ENFORCEMENT_ENABLED", False)
        monkeypatch.setattr(script, "_results", [])

        async with maker() as session:
            before = await session.scalar(
                select(func.count()).select_from(OrganizationModel)
            )

        await script.run(calls=8, affordable=2)

        async with maker() as session:
            after = await session.scalar(
                select(func.count()).select_from(OrganizationModel)
            )
        assert after == before


class TestTheArguments:
    def test_asking_for_more_credit_than_calls_is_refused(self, monkeypatch):
        """--affordable 8 --calls 8 refuses nothing, so it would report a pass
        on a deployment with no enforcement at all."""
        monkeypatch.setattr(
            "sys.argv", ["rehearse_concurrency", "--calls", "4", "--affordable", "4"]
        )

        assert script.main() == 1


class TestTheLedgerKindItUses:
    def test_holds_are_reservations(self):
        """If the script counted the wrong ledger kind it would report zero
        holds and pass while nothing was being held at all."""
        assert CreditLedgerKind.RESERVATION.value == "reservation"

"""Twenty debits at once leave a ledger whose running balance is true (KAN-44).

This is the ticket's acceptance test, and it has to be run the hard way.
The ordinary ``async_session`` fixture binds every session in a test to one
connection inside a savepoint, so twenty "concurrent" writes through it
would be serialised by the connection itself and could never race -- the
test would pass with or without the lock. So this one opens twenty
independent connections off the session-scoped engine, commits each, and
cleans up after itself.

What it proves: every row's ``balance_after_paise`` equals the sum of the
deltas up to and including it, in write order. Without the organisation
lock, several of the twenty read the same balance and record the same
running total; with it, the column is a true statement.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.models import CreditLedgerModel, OrganizationModel
from api.services.billing import events

WRITERS = 20


@pytest.mark.asyncio
async def test_twenty_concurrent_debits_keep_the_running_balance_true(test_engine):
    make = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    # The organisation has to be committed, not held in a savepoint, or the
    # other connections cannot see it and every debit fails its foreign key.
    async with make() as setup:
        org = OrganizationModel(provider_id="org-ledger-race", quota_decibyl_tokens=0)
        setup.add(org)
        await setup.commit()
        org_id = org.id

    try:

        async def debit(i: int) -> None:
            async with make() as session, session.begin():
                await events.charge(
                    session,
                    organization_id=org_id,
                    event=events.TOOL_CALL,
                    ref_id=f"race-{i}",
                )

        await asyncio.gather(*(debit(i) for i in range(WRITERS)))

        async with make() as reader:
            rows = (
                await reader.scalars(
                    select(CreditLedgerModel)
                    .where(CreditLedgerModel.organization_id == org_id)
                    .order_by(CreditLedgerModel.id)
                )
            ).all()

        assert len(rows) == WRITERS
        price = events.paise_for(events.TOOL_CALL)
        running = 0
        for row in rows:
            assert row.delta_paise == -price
            running += row.delta_paise
            assert row.balance_after_paise == running, (
                f"row {row.id} recorded {row.balance_after_paise} but the "
                f"ledger up to it sums to {running}: two debits read the "
                "same balance"
            )
        assert running == -price * WRITERS
        # The one thing a race produces and a lock forbids: two rows
        # claiming the same running balance.
        assert len({r.balance_after_paise for r in rows}) == WRITERS
    finally:
        async with make() as cleanup:
            await cleanup.execute(
                delete(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org_id
                )
            )
            await cleanup.execute(
                delete(OrganizationModel).where(OrganizationModel.id == org_id)
            )
            await cleanup.commit()

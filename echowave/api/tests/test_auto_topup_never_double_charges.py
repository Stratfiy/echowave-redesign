"""The guard that holds when the decision engine cannot.

`auto_topup.decide` refuses a second attempt while one is in flight, and that is
correct — but it runs inside a worker, and a worker can be running twice. Two
sweeps that both read "nothing in flight" a millisecond apart both decide to
charge, and the customer is debited twice for one low balance.

The partial unique index is what makes that impossible rather than unlikely. It
is the same reasoning as `uq_payment_mandates_subscription`: a rule enforced
only in Python is a rule that holds until the day there are two processes.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from api.db.models import (
    AutoTopupAttemptModel,
    AutoTopupSettingModel,
    OrganizationModel,
)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


def _attempt(org_id: int, status: str) -> AutoTopupAttemptModel:
    return AutoTopupAttemptModel(
        organization_id=org_id, status=status, amount_paise=100_000
    )


@pytest.mark.asyncio
class TestOnlyOneAttemptCanBeInFlight:
    async def test_a_second_scheduled_attempt_is_refused_by_the_database(
        self, db_session, async_session
    ):
        """The double-charge race, made impossible."""
        org = await _org(async_session, "race")
        async_session.add(_attempt(org.id, "scheduled"))
        await async_session.flush()

        async with async_session.begin_nested():
            async_session.add(_attempt(org.id, "scheduled"))
            with pytest.raises(IntegrityError):
                await async_session.flush()

    async def test_charging_also_counts_as_in_flight(self, db_session, async_session):
        """The window between deciding to charge and the provider answering is
        the most dangerous one: the money may already be moving."""
        org = await _org(async_session, "charging")
        async_session.add(_attempt(org.id, "scheduled"))
        await async_session.flush()

        async with async_session.begin_nested():
            async_session.add(_attempt(org.id, "charging"))
            with pytest.raises(IntegrityError):
                await async_session.flush()

    async def test_a_finished_attempt_does_not_block_the_next_one(
        self, db_session, async_session
    ):
        """Otherwise one top-up a month would be one top-up ever."""
        org = await _org(async_session, "finished")
        async_session.add(_attempt(org.id, "succeeded"))
        await async_session.flush()

        async_session.add(_attempt(org.id, "scheduled"))
        await async_session.flush()  # must not raise

    async def test_a_failed_attempt_does_not_block_the_next_one(
        self, db_session, async_session
    ):
        org = await _org(async_session, "failed")
        async_session.add(_attempt(org.id, "failed"))
        await async_session.flush()

        async_session.add(_attempt(org.id, "scheduled"))
        await async_session.flush()

    async def test_two_different_accounts_do_not_block_each_other(
        self, db_session, async_session
    ):
        """The index is per organization. Scoping it wrong would mean one
        customer's pending top-up stopped everyone else's."""
        first = await _org(async_session, "a")
        second = await _org(async_session, "b")
        async_session.add(_attempt(first.id, "scheduled"))
        async_session.add(_attempt(second.id, "scheduled"))
        await async_session.flush()


@pytest.mark.asyncio
class TestTheSettingsRow:
    async def test_an_account_has_at_most_one_policy(self, db_session, async_session):
        """Two rows would mean two answers to "is this on", and the sweep would
        act on whichever it happened to read first."""
        org = await _org(async_session, "settings")
        async_session.add(AutoTopupSettingModel(organization_id=org.id))
        await async_session.flush()

        async with async_session.begin_nested():
            async_session.add(AutoTopupSettingModel(organization_id=org.id))
            with pytest.raises(IntegrityError):
                await async_session.flush()

    async def test_a_new_row_is_off_and_buys_nothing(self, db_session, async_session):
        """Defaults decide what happens to an account nobody configured, so the
        defaults are the safe ones: off, and zero."""
        org = await _org(async_session, "defaults")
        row = AutoTopupSettingModel(organization_id=org.id)
        async_session.add(row)
        await async_session.flush()
        await async_session.refresh(row)

        assert row.enabled is False
        assert row.amount_paise == 0
        assert row.max_per_month == 4

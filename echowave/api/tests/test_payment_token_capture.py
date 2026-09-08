"""Keeping — and not keeping — permission to charge a customer later.

Two failures live here and they point in opposite directions.

Missing the token is the harmless one: it arrives exactly once, on the payment
that registered it, so a miss means the customer must authorise again and
experiences the feature as broken.

Storing the wrong one is the serious one. An ordinary card payment also carries
a token id — for the customer's own saved-card convenience — and treating that
as standing permission to debit would be taking money on the strength of a
checkbox somebody ticked to avoid retyping a card number. Only a token the
provider marks recurring is permission.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from api.db.models import OrganizationModel, PaymentTokenModel
from api.services.billing import payments


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


def _entity(**kw) -> dict:
    base = {
        "token_id": "token_abc",
        "recurring": True,
        "method": "card",
        "customer_id": "cust_1",
        "card": {"last4": "4321"},
        "max_amount_paise": None,
    }
    base.update(kw)
    base.pop("max_amount_paise", None)
    return base


@pytest.mark.asyncio
class TestOnlyRecurringPermissionIsKept:
    async def test_a_recurring_token_is_stored(self, db_session, async_session):
        org = await _org(async_session, "recurring")
        row = await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        assert row is not None
        assert row.token_id == "token_abc"
        assert row.customer_id == "cust_1"
        assert row.status == "active"

    async def test_a_convenience_saved_card_is_not_permission_to_debit(
        self, db_session, async_session
    ):
        """The one that would take money nobody authorised."""
        org = await _org(async_session, "convenience")
        row = await payments.remember_token(
            async_session,
            organization_id=org.id,
            entity=_entity(recurring=False),
        )
        assert row is None
        stored = await async_session.scalar(select(PaymentTokenModel))
        assert stored is None

    async def test_a_payment_with_no_token_stores_nothing(
        self, db_session, async_session
    ):
        org = await _org(async_session, "none")
        entity = _entity()
        entity.pop("token_id")
        assert (
            await payments.remember_token(
                async_session, organization_id=org.id, entity=entity
            )
            is None
        )

    async def test_the_registered_maximum_is_kept_when_given(
        self, db_session, async_session
    ):
        """It is the bank's ceiling, not ours. Stored so a charge above it is
        refused here with a reason rather than discovered as a decline."""
        org = await _org(async_session, "max")
        row = await payments.remember_token(
            async_session,
            organization_id=org.id,
            entity=_entity(max_amount=500_000),
        )
        assert row.max_amount_paise == 500_000

    async def test_an_absent_maximum_is_not_read_as_unlimited(
        self, db_session, async_session
    ):
        """Null means the provider did not say. Defaulting it to a number would
        invent an authorisation the customer never gave."""
        org = await _org(async_session, "nomax")
        row = await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        assert row.max_amount_paise is None


@pytest.mark.asyncio
class TestRedeliveryDoesNotDuplicate:
    async def test_the_same_token_twice_updates_one_row(
        self, db_session, async_session
    ):
        """Razorpay delivers at least once. A second row would mean half the
        code reads the stale one."""
        org = await _org(async_session, "redeliver")
        await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        await payments.remember_token(
            async_session,
            organization_id=org.id,
            entity=_entity(max_amount=200_000),
        )
        rows = (await async_session.execute(select(PaymentTokenModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].max_amount_paise == 200_000

    async def test_a_redelivery_reactivates_a_revoked_token(
        self, db_session, async_session
    ):
        """Re-authorising after a revocation is the customer choosing to switch
        it back on, and it arrives as the same shape of event."""
        org = await _org(async_session, "reauth")
        await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        await payments.revoke_token(async_session, token_id="token_abc")
        row = await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        assert row.status == "active"


@pytest.mark.asyncio
class TestWhichInstrumentWeWouldPresent:
    async def test_a_revoked_token_is_never_offered(
        self, db_session, async_session
    ):
        """Presenting a cancelled mandate is a decline that costs money and
        merchant standing, every time."""
        org = await _org(async_session, "revoked")
        await payments.remember_token(
            async_session, organization_id=org.id, entity=_entity()
        )
        await payments.revoke_token(async_session, token_id="token_abc")
        assert await payments.active_token(async_session, organization_id=org.id) is None

    async def test_an_account_with_no_token_has_none(
        self, db_session, async_session
    ):
        org = await _org(async_session, "empty")
        assert await payments.active_token(async_session, organization_id=org.id) is None

    async def test_one_accounts_token_is_never_offered_to_another(
        self, db_session, async_session
    ):
        """Tenant isolation, on the object that can take money."""
        first = await _org(async_session, "t1")
        second = await _org(async_session, "t2")
        await payments.remember_token(
            async_session, organization_id=first.id, entity=_entity()
        )
        assert (
            await payments.active_token(async_session, organization_id=second.id)
            is None
        )


@pytest.mark.asyncio
class TestStoringATokenNeverBreaksAPayment:
    async def test_a_database_failure_here_does_not_abort_the_credit(
        self, db_session, async_session
    ):
        """The disaster this exists to prevent.

        `remember_token` runs inside the transaction that has just credited the
        customer's ledger for a payment the bank has already taken. A failed
        flush aborts the *whole* transaction, so catching the exception is not
        enough — the credit would roll back with it. A savepoint is what keeps
        the failure local, and this proves the surrounding work survives.
        """
        org = await _org(async_session, "poison")

        # Longer than the column allows, so the insert is rejected by Postgres
        # rather than by anything we could check in Python first.
        result = await payments.remember_token(
            async_session,
            organization_id=org.id,
            entity=_entity(token_id="x" * 200),
        )
        assert result is None

        # The session is still usable, which is the whole point: the credit that
        # was written before this call is still there to be committed.
        still_there = await async_session.scalar(
            select(OrganizationModel).where(OrganizationModel.id == org.id)
        )
        assert still_there is not None

    async def test_a_malformed_entity_stores_nothing_and_does_not_raise(
        self, db_session, async_session
    ):
        org = await _org(async_session, "malformed")
        result = await payments.remember_token(
            async_session,
            organization_id=org.id,
            entity={"token_id": "t", "recurring": True, "card": "not-a-dict"},
        )
        assert result is None

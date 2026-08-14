"""Refunding a payment, and the three ways it could quietly cost money.

Nothing reversed a payment before this. A chargeback or a goodwill refund left
credit granted with no way to claw it back, so a refund was two manual acts in
two systems and nobody could tell afterwards whether both had happened.

**Never more than was paid.** Checked against the payment's own gross, not a
number in the request. A refund of ten times the payment is one misplaced
decimal and the provider will process it.

**Never credit that has been spent.** The balance has to survive the reversal.
Refunding a customer who already made the calls hands back the money *and*
keeps the service, and leaves a negative balance that every reservation check
downstream reads as an account in good standing with a strange number.

**Money first, ledger second.** Reversing before the provider confirms takes
credit off an account whose refund then failed. The other order leaves, at
worst, a provider refund with no ledger row — which reconciliation catches and
a human can finish.
"""

from datetime import UTC, datetime

import pytest

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentModel,
    RefundModel,
)
from api.enums import CreditLedgerKind
from api.services.billing import refunds


async def _paid(async_session, slug, *, net=100_000, gross=118_000, spent=0):
    """An organization with one settled payment, and optionally some spend."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()

    payment = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{slug}",
        payment_id=f"pay_{slug}",
        amount_paise=net,
        gross_paise=gross,
        status="paid",
        paid_at=datetime.now(UTC),
    )
    async_session.add(payment)
    await async_session.flush()

    async_session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=net,
            kind=CreditLedgerKind.TOPUP.value,
            ref_type="payment",
            ref_id=payment.payment_id,
            balance_after_paise=net,
        )
    )
    if spent:
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-spent,
                kind=CreditLedgerKind.USAGE.value,
                balance_after_paise=net - spent,
            )
        )
    await async_session.flush()
    return org, payment


@pytest.fixture
def unconfigured_provider(monkeypatch):
    """No Razorpay keys, so the provider call is skipped.

    The reversal is the half these tests are about, and a deployment with no
    Razorpay is a real configuration — self-hosted, or settling some other way
    — rather than a stub invented for the test.
    """
    monkeypatch.setattr(refunds, "RAZORPAY_KEY_ID", None)
    monkeypatch.setattr(refunds, "RAZORPAY_KEY_SECRET", None)


@pytest.mark.asyncio
class TestTheAmount:
    async def test_a_full_refund_reverses_the_net_credit(
        self, db_session, async_session, unconfigured_provider
    ):
        """Gross out to the customer, net out of the ledger. The ledger never
        held the tax, so removing it would take credit that was never given."""
        org, payment = await _paid(async_session, "full")

        result = await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=None,
            reason="Cancelled",
            actor_user_id=None,
        )

        assert result.amount_paise == 118_000
        assert result.reversed_credit_paise == 100_000

    async def test_a_partial_refund_reverses_proportionally(
        self, db_session, async_session, unconfigured_provider
    ):
        """A partial refund of a taxed payment is a partial refund of both
        halves, which is also how the credit note has to read."""
        _, payment = await _paid(async_session, "partial")

        result = await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=59_000,
            reason=None,
            actor_user_id=None,
        )

        assert result.amount_paise == 59_000
        assert result.reversed_credit_paise == 50_000

    async def test_refunding_more_than_was_paid_is_refused(
        self, db_session, async_session, unconfigured_provider
    ):
        """One misplaced decimal, and the provider will process it."""
        _, payment = await _paid(async_session, "over")

        with pytest.raises(refunds.RefundError):
            await refunds.refund_payment(
                async_session,
                payment_id=payment.id,
                amount_paise=1_180_000,
                reason=None,
                actor_user_id=None,
            )

    async def test_two_partials_cannot_exceed_the_payment(
        self, db_session, async_session, unconfigured_provider
    ):
        """ "How much is left" is a sum over refunds, not a flag on the payment
        — which is the whole reason refunds are their own rows."""
        _, payment = await _paid(async_session, "twice")

        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=100_000,
            reason=None,
            actor_user_id=None,
        )

        with pytest.raises(refunds.RefundError):
            await refunds.refund_payment(
                async_session,
                payment_id=payment.id,
                amount_paise=100_000,
                reason=None,
                actor_user_id=None,
            )

    async def test_a_fully_refunded_payment_cannot_be_refunded_again(
        self, db_session, async_session, unconfigured_provider
    ):
        _, payment = await _paid(async_session, "already")
        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=None,
            reason=None,
            actor_user_id=None,
        )

        with pytest.raises(refunds.RefundError):
            await refunds.refund_payment(
                async_session,
                payment_id=payment.id,
                amount_paise=None,
                reason=None,
                actor_user_id=None,
            )

    async def test_a_payment_that_never_settled_cannot_be_refunded(
        self, db_session, async_session, unconfigured_provider
    ):
        org = OrganizationModel(provider_id="org-unpaid", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        payment = PaymentModel(
            organization_id=org.id,
            provider="razorpay",
            order_id="order_unpaid",
            amount_paise=100_000,
            gross_paise=118_000,
            status="created",
        )
        async_session.add(payment)
        await async_session.flush()

        with pytest.raises(refunds.RefundError):
            await refunds.refund_payment(
                async_session,
                payment_id=payment.id,
                amount_paise=None,
                reason=None,
                actor_user_id=None,
            )


@pytest.mark.asyncio
class TestSpentCredit:
    async def test_refunding_spent_credit_is_refused(
        self, db_session, async_session, unconfigured_provider
    ):
        """The guard that matters most. The customer made the calls; refunding
        hands back the money and keeps the service, and the balance goes
        negative where every reservation check misreads it."""
        _, payment = await _paid(async_session, "spent", spent=90_000)

        with pytest.raises(refunds.RefundError) as exc:
            await refunds.refund_payment(
                async_session,
                payment_id=payment.id,
                amount_paise=None,
                reason=None,
                actor_user_id=None,
            )
        assert "already been spent" in str(exc.value)

    async def test_a_partial_refund_within_the_balance_is_allowed(
        self, db_session, async_session, unconfigured_provider
    ):
        """The honest resolution to most disputes, and the reason all-or-
        nothing was never good enough."""
        _, payment = await _paid(async_session, "some-spent", spent=60_000)

        result = await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=23_600,
            reason=None,
            actor_user_id=None,
        )

        assert result.reversed_credit_paise == 20_000

    async def test_the_balance_never_goes_negative(
        self, db_session, async_session, unconfigured_provider
    ):
        from api.services.billing.costing import current_balance_paise

        org, payment = await _paid(async_session, "nonneg", spent=50_000)
        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=59_000,
            reason=None,
            actor_user_id=None,
        )

        assert await current_balance_paise(async_session, organization_id=org.id) >= 0


@pytest.mark.asyncio
class TestWhatIsRecorded:
    async def test_the_ledger_gets_a_reversing_row(
        self, db_session, async_session, unconfigured_provider
    ):
        import sqlalchemy as sa

        org, payment = await _paid(async_session, "ledger")
        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=None,
            reason=None,
            actor_user_id=None,
        )

        row = (
            await async_session.execute(
                sa.select(CreditLedgerModel).where(
                    CreditLedgerModel.ref_type == "refund"
                )
            )
        ).scalar_one()
        assert row.delta_paise == -100_000
        assert row.organization_id == org.id

    async def test_both_amounts_are_stored(
        self, db_session, async_session, unconfigured_provider
    ):
        """Gross and net. Keeping both is what lets a credit note be produced
        later without re-deriving the split from a rate that may since have
        changed."""
        import sqlalchemy as sa

        _, payment = await _paid(async_session, "both")
        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=None,
            reason="Duplicate charge",
            actor_user_id=None,
        )

        record = (await async_session.execute(sa.select(RefundModel))).scalar_one()
        assert record.amount_paise == 118_000
        assert record.reversed_credit_paise == 100_000
        assert record.reason == "Duplicate charge"
        assert record.status == "processed"

    async def test_a_held_referral_award_is_cancelled_with_the_refund(
        self, db_session, async_session, unconfigured_provider
    ):
        """The case the referral hold exists for, joined up. Refund the
        payment and the reward it earned goes with it — silently, because
        nothing had been credited yet."""
        from api.services.billing import referrals

        referrer = OrganizationModel(provider_id="org-ref-r", quota_decibyl_tokens=0)
        async_session.add(referrer)
        await async_session.flush()
        org, payment = await _paid(async_session, "with-referral")
        await referrals.attribute(
            async_session,
            organization_id=org.id,
            code=await referrals.ensure_code(
                async_session, organization_id=referrer.id
            ),
        )
        await referrals.award_for_payment(
            async_session,
            organization_id=org.id,
            payment_id=payment.payment_id,
            net_paise=100_000,
        )

        await refunds.refund_payment(
            async_session,
            payment_id=payment.id,
            amount_paise=None,
            reason=None,
            actor_user_id=None,
        )

        summary = await referrals.summary(async_session, organization_id=referrer.id)
        assert summary["pending_paise"] == 0
        assert summary["cancelled_paise"] == 20_000

"""Plan balance is for the month it was granted for. A top-up is for ever.

``CreditLedgerKind.PLAN`` was an ordinary credit row with no expiry, and that
was never a decision — ``PRICING-FIX-PLAN.md`` flagged expiry-versus-rollover
as a call to take explicitly and it was taken by default, in favour of rollover
for ever. An account paying ₹2,999 a month and never calling accrued ₹2,500 a
month indefinitely: an unbounded liability, and a bundle strictly worse for us
than a top-up of the same size while being indistinguishable to the customer.

Expiry is what makes a bundle a bundle, and it has exactly two dangers. It must
never touch a top-up — the customer bought that outright — and it must never
take more than is there, because an expiry that drives a balance negative
suspends calls over a date rather than over a spend.

Both paths are tested: the ordinary one, where the next collection retires the
cycle before it, and the lapsed one, where an account stops paying and a daily
sweep does it instead. They race on a late renewal by design, so the same grant
reached twice must produce one debit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db.models import CreditLedgerModel, OrganizationModel, PaymentMandateModel
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import plans


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _entry(
    session,
    org,
    *,
    delta: int,
    kind: CreditLedgerKind,
    ref_type: str | None = None,
    ref_id: str | None = None,
    created_at: datetime | None = None,
) -> CreditLedgerModel:
    from api.services.billing.payments import current_balance_paise

    balance = await current_balance_paise(session, organization_id=org.id)
    row = CreditLedgerModel(
        organization_id=org.id,
        delta_paise=delta,
        kind=kind.value,
        ref_type=ref_type,
        ref_id=ref_id,
        balance_after_paise=balance + delta,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def _grant(session, org, *, paise=250_000, ref="pay_1", created_at=None):
    return await _entry(
        session,
        org,
        delta=paise,
        kind=CreditLedgerKind.PLAN,
        ref_type=plans.REF_TYPE,
        ref_id=ref,
        created_at=created_at,
    )


async def _balance(session, org) -> int:
    from api.services.billing.payments import current_balance_paise

    return await current_balance_paise(session, organization_id=org.id)


class TestWhatExpires:
    async def test_an_untouched_cycle_expires_in_full(self, db_session, async_session):
        org = await _org(async_session, "untouched")
        grant = await _grant(async_session, org)

        expired = await plans.expire_grant(async_session, grant=grant)

        assert expired == 250_000
        assert await _balance(async_session, org) == 0

    async def test_only_the_unspent_part_expires(self, db_session, async_session):
        org = await _org(async_session, "partly")
        grant = await _grant(async_session, org)
        await _entry(
            async_session, org, delta=-90_000, kind=CreditLedgerKind.USAGE
        )

        expired = await plans.expire_grant(async_session, grant=grant)

        assert expired == 160_000
        assert await _balance(async_session, org) == 0

    async def test_a_fully_spent_cycle_expires_nothing(self, db_session, async_session):
        org = await _org(async_session, "spent")
        grant = await _grant(async_session, org)
        await _entry(
            async_session, org, delta=-250_000, kind=CreditLedgerKind.USAGE
        )

        assert await plans.expire_grant(async_session, grant=grant) == 0

    async def test_a_top_up_is_never_expired(self, db_session, async_session):
        """The line that must not move. A top-up was bought outright; taking it
        back because a plan cycle ended is taking money for nothing."""
        org = await _org(async_session, "topper")
        grant = await _grant(async_session, org)
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)

        expired = await plans.expire_grant(async_session, grant=grant)

        assert expired == 250_000
        assert await _balance(async_session, org) == 100_000

    async def test_spending_draws_the_plan_balance_down_first(
        self, db_session, async_session
    ):
        """The ordering that makes the subtraction above the right answer.

        Plan money is the money that expires, so it is spent first. Any other
        ordering quietly converts a top-up into something with an expiry date.
        """
        org = await _org(async_session, "order")
        grant = await _grant(async_session, org)
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        await _entry(
            async_session, org, delta=-300_000, kind=CreditLedgerKind.USAGE
        )

        # ₹3,000 spent against ₹2,500 of plan and ₹1,000 of top-up: the plan is
        # gone and ₹500 of the top-up went with it.
        expired = await plans.expire_grant(async_session, grant=grant)

        assert expired == 0
        assert await _balance(async_session, org) == 50_000

    async def test_a_held_call_counts_against_the_remainder(
        self, db_session, async_session
    ):
        """A reservation is money already committed to a call in flight.

        Expiring it would take the funds out from under a call that is
        currently running.
        """
        org = await _org(async_session, "inflight")
        grant = await _grant(async_session, org)
        await _entry(
            async_session, org, delta=-40_000, kind=CreditLedgerKind.RESERVATION
        )

        assert await plans.expire_grant(async_session, grant=grant) == 210_000

    async def test_a_released_hold_gives_the_remainder_back(
        self, db_session, async_session
    ):
        org = await _org(async_session, "released")
        grant = await _grant(async_session, org)
        await _entry(
            async_session, org, delta=-40_000, kind=CreditLedgerKind.RESERVATION
        )
        await _entry(
            async_session, org, delta=40_000, kind=CreditLedgerKind.RESERVATION
        )

        assert await plans.expire_grant(async_session, grant=grant) == 250_000

    async def test_it_never_drives_a_balance_negative(self, db_session, async_session):
        """An expiry that suspends calls is an expiry that reads as a fault."""
        org = await _org(async_session, "negative")
        grant = await _grant(async_session, org)
        # An operator clawback after the grant: the balance is gone but the
        # kinds this counts as consumption did not move.
        await _entry(
            async_session, org, delta=-250_000, kind=CreditLedgerKind.ADJUSTMENT
        )

        expired = await plans.expire_grant(async_session, grant=grant)

        assert expired == 0
        assert await _balance(async_session, org) == 0


class TestItHappensOnce:
    async def test_the_same_grant_reached_twice_debits_once(
        self, db_session, async_session
    ):
        """The collection path and the daily sweep race on a late renewal."""
        org = await _org(async_session, "twice")
        grant = await _grant(async_session, org)

        first = await plans.expire_grant(async_session, grant=grant)
        second = await plans.expire_grant(async_session, grant=grant)

        assert first == 250_000
        assert second == 0
        assert await _balance(async_session, org) == 0

    async def test_the_expiry_says_what_it_is(self, db_session, async_session):
        """An unexplained debit the size of last month's grant is the single
        most alarming line a billing screen can show."""
        org = await _org(async_session, "labelled")
        grant = await _grant(async_session, org)
        await plans.expire_grant(async_session, grant=grant)

        from sqlalchemy import select

        row = await async_session.scalar(
            select(CreditLedgerModel).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.PLAN_EXPIRY.value,
            )
        )
        assert row is not None
        assert row.ref_id == str(grant.id)
        assert "expired" in (row.note or "")


class TestTheNextCycleRetiresTheLastOne:
    async def test_a_collection_expires_the_previous_grant(
        self, db_session, async_session
    ):
        org = await _org(async_session, "renewing")
        mandate = PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose="starter_plan",
            subscription_id="sub_renew",
            status=MandateStatus.ACTIVE.value,
            price_paise=299_900,
        )
        async_session.add(mandate)
        await async_session.flush()

        await _grant(async_session, org, ref="pay_first")

        result = await plans.grant_plan_cycle(
            async_session,
            mandate=mandate,
            event={"payload": {"payment": {"entity": {"id": "pay_second"}}}},
            balance_paise=250_000,
        )

        assert result["status"] not in ("no_payment_id", "invalid_amount")
        # Last month's ₹2,500 gone, this month's ₹2,500 in. Not ₹5,000.
        assert await _balance(async_session, org) == 250_000

    async def test_what_the_customer_actually_spent_survives_the_boundary(
        self, db_session, async_session
    ):
        """A top-up made mid-cycle is still there after the renewal."""
        org = await _org(async_session, "mixed")
        mandate = PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose="starter_plan",
            subscription_id="sub_mixed",
            status=MandateStatus.ACTIVE.value,
            price_paise=299_900,
        )
        async_session.add(mandate)
        await async_session.flush()

        await _grant(async_session, org, ref="pay_first")
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)

        await plans.grant_plan_cycle(
            async_session,
            mandate=mandate,
            event={"payload": {"payment": {"entity": {"id": "pay_second"}}}},
            balance_paise=250_000,
        )

        assert await _balance(async_session, org) == 350_000


class TestTheLapsedSweep:
    async def test_an_account_that_stopped_paying_loses_its_last_cycle(
        self, db_session, async_session
    ):
        org = await _org(async_session, "lapsed")
        await _grant(
            async_session,
            org,
            created_at=datetime.now(UTC)
            - timedelta(days=plans.CYCLE_GRACE_DAYS + 1),
        )

        counters = await plans.sweep_lapsed_plan_balance(async_session)

        assert counters["expired"] == 1
        assert await _balance(async_session, org) == 0

    async def test_a_cycle_still_inside_its_month_is_left_alone(
        self, db_session, async_session
    ):
        org = await _org(async_session, "current")
        await _grant(async_session, org, created_at=datetime.now(UTC))

        counters = await plans.sweep_lapsed_plan_balance(async_session)

        assert counters["expired"] == 0
        assert await _balance(async_session, org) == 250_000

    async def test_a_collection_a_few_days_late_does_not_lose_its_balance(
        self, db_session, async_session
    ):
        """Grace, and why it is longer than a month.

        A bank retry or a weekend can put a collection a day or two past the
        month. Expiring balance the customer is about to renew would be the
        worst possible timing for the most alarming line on a billing screen.
        """
        org = await _org(async_session, "late")
        await _grant(
            async_session,
            org,
            created_at=datetime.now(UTC) - timedelta(days=32),
        )

        counters = await plans.sweep_lapsed_plan_balance(async_session)

        assert counters["expired"] == 0
        assert await _balance(async_session, org) == 250_000

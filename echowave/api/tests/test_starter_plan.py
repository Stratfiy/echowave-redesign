"""The starter plan: one collection, two things bought.

    Rs2,500 of call balance  +  Rs499 for the number  =  Rs2,999 net

Every test here exists because one specific way of getting it wrong moves real
money in a way that reconciles perfectly against itself:

* **a redelivered webhook must not grant a second balance.** Razorpay delivers
  at least once. Two ledger rows of Rs2,500 both look correct in isolation, and
  the account paid for one.
* **the rent inside the plan must not also be debited from the balance.** The
  customer would pay the rent inside the collection and again out of the credit
  that same collection just gave them.
* **the bank must be told the gross.** A plan collected at the net figure
  collects no GST at all, month after month, by standing instruction.
* **a rental charge is not a plan charge.** Routing on amount rather than
  purpose would make two same-priced plans indistinguishable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.constants import (
    NUMBER_RENTAL_PRICE_PAISE,
    STARTER_PLAN_BALANCE_PAISE,
    STARTER_PLAN_PRICE_PAISE,
)
from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
)
from api.enums import (
    CreditLedgerKind,
    MandateStatus,
    RecurringChargeStatus,
    RecurringChargeType,
)
from api.services.billing import mandates as mandate_service
from api.services.billing import payments
from api.services.billing.plans import grant_plan_cycle


async def _org(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _plan_mandate(session, org, *, sub="sub_plan_1"):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=mandate_service.PURPOSE_STARTER_PLAN,
        subscription_id=sub,
        plan_id="plan_starter",
        status=MandateStatus.ACTIVE.value,
        price_paise=STARTER_PLAN_PRICE_PAISE,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _charge(session, org, *, mandate, resource_id=1):
    charge = RecurringChargeModel(
        organization_id=org.id,
        charge_type=RecurringChargeType.NUMBER_RENTAL.value,
        resource_id=resource_id,
        status=RecurringChargeStatus.ACTIVE.value,
        cost_paise=25000,
        price_paise=NUMBER_RENTAL_PRICE_PAISE,
        mandate_id=mandate.id,
        started_at=datetime.now(UTC) - timedelta(days=1),
        next_charge_at=datetime.now(UTC),
    )
    session.add(charge)
    await session.flush()
    return charge


def _collection(subscription_id: str, *, payment_id: str = "pay_plan_1") -> dict:
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {"id": subscription_id, "status": "active"}},
            "payment": {"entity": {"id": payment_id, "amount": 353882}},
        },
    }


WEBHOOK_SECRET = "whsec_test_do_not_use_in_production"


@pytest.fixture
def configured_webhook(monkeypatch):
    monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)


async def _deliver(session, event: dict) -> dict:
    """Put an event through the real signed-webhook path.

    Not a direct call to the router underneath: signature verification is over
    the exact bytes Razorpay signed, and a test that skips it would pass while
    the endpoint rejected every real delivery.
    """
    raw = json.dumps(event).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await payments.handle_webhook(session, raw_body=raw, signature=signature)


async def _balance(session, org) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == org.id
            )
        )
    )


class TestThePriceIsNeverAboveItsParts:
    """Starter may discount its contents. It may never sell above them.

    The price used to be derived as balance + rental so the three could not
    drift. That held while a plan was the only way to hold a number and the two
    prices were the same figure. They are not any more: Rs559 is what an *extra*
    number costs, while Starter's included number is priced inside its monthly
    price like every other tier's is. Deriving through the extra-number figure
    would mean raising it silently re-prices Starter — and Starter's price is
    pinned to a Razorpay plan collecting a fixed amount, so the two would come
    apart at the bank rather than in a spreadsheet.

    What the derivation actually protected is the invariant below, and it is
    also enforced by ``subscription_plans.save()``, which can see the whole
    plan rather than just these constants.
    """

    def test_the_plan_never_costs_more_than_it_contains(self):
        assert (
            STARTER_PLAN_PRICE_PAISE
            <= STARTER_PLAN_BALANCE_PAISE + NUMBER_RENTAL_PRICE_PAISE
        )

    def test_it_never_grants_more_balance_than_it_collects(self):
        """The one that is a loss on contact: granted balance is spendable
        immediately and at our cost."""
        assert STARTER_PLAN_BALANCE_PAISE <= STARTER_PLAN_PRICE_PAISE

    def test_the_advertised_figures_are_the_ones_shipped(self):
        """Rs2,500 of balance and a number, sold at Rs2,999 — a Rs60 discount
        against Rs559 of number, the same shape Growth and Scale carry."""
        assert STARTER_PLAN_BALANCE_PAISE == 250_000
        assert NUMBER_RENTAL_PRICE_PAISE == 55_900
        assert STARTER_PLAN_PRICE_PAISE == 299_900


class TestGrantingACycle:
    async def test_a_collection_grants_the_balance(self, db_session, async_session):
        org = await _org(async_session, "plan-grant")
        mandate = await _plan_mandate(async_session, org)
        await _charge(async_session, org, mandate=mandate)

        result = await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        assert result["status"] == "granted"
        assert result["granted_paise"] == STARTER_PLAN_BALANCE_PAISE
        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_the_grant_is_its_own_ledger_kind(self, db_session, async_session):
        """Not a top-up. A statement calling it one invites the question of
        when the customer bought it."""
        org = await _org(async_session, "plan-kind")
        mandate = await _plan_mandate(async_session, org)

        await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        entry = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert entry.kind == CreditLedgerKind.PLAN.value
        assert entry.ref_id == "pay_plan_1"

    async def test_the_same_collection_twice_grants_once(
        self, db_session, async_session
    ):
        """Razorpay delivers at least once. This is the money one."""
        org = await _org(async_session, "plan-replay")
        mandate = await _plan_mandate(async_session, org)
        await _charge(async_session, org, mandate=mandate)

        event = _collection("sub_plan_1")
        first = await grant_plan_cycle(async_session, mandate=mandate, event=event)
        second = await grant_plan_cycle(async_session, mandate=mandate, event=event)

        assert first["status"] == "granted"
        assert second["status"] == "already_granted"
        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_a_later_cycle_grants_again_and_retires_the_one_before(
        self, db_session, async_session
    ):
        """Idempotency must not become "once ever" — this is a monthly plan.

        And a second cycle is not a second balance. Plan balance is for the
        month it was granted for: since 21 Aug the arriving cycle retires the
        one before it, which is what makes a bundle worth more than a top-up of
        the same size rather than an unbounded liability. See
        ``test_plan_balance_expires.py`` for the whole rule, top-ups included.
        """
        org = await _org(async_session, "plan-month-two")
        mandate = await _plan_mandate(async_session, org)
        await _charge(async_session, org, mandate=mandate)

        first = await grant_plan_cycle(
            async_session,
            mandate=mandate,
            event=_collection("sub_plan_1", payment_id="pay_month_1"),
        )
        second = await grant_plan_cycle(
            async_session,
            mandate=mandate,
            event=_collection("sub_plan_1", payment_id="pay_month_2"),
        )

        # Two distinct collections, two grants: the idempotency key is the
        # payment, not the account.
        assert first["status"] == "granted"
        assert second["status"] == "granted"
        # One month's worth on the account, not two.
        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_a_collection_with_no_payment_id_grants_nothing(
        self, db_session, async_session
    ):
        """Nothing to be idempotent on. Short a balance is visible and fixable;
        long one nobody can trace is neither."""
        org = await _org(async_session, "plan-no-id")
        mandate = await _plan_mandate(async_session, org)

        event = {
            "event": "subscription.charged",
            "payload": {"subscription": {"entity": {"id": "sub_plan_1"}}},
        }
        result = await grant_plan_cycle(async_session, mandate=mandate, event=event)

        assert result["status"] == "no_payment_id"
        assert await _balance(async_session, org) == 0

    async def test_a_non_positive_balance_is_refused(self, db_session, async_session):
        org = await _org(async_session, "plan-zero")
        mandate = await _plan_mandate(async_session, org)

        result = await grant_plan_cycle(
            async_session,
            mandate=mandate,
            event=_collection("sub_plan_1"),
            balance_paise=0,
        )

        assert result["status"] == "invalid_amount"
        assert await _balance(async_session, org) == 0


class TestTheRentIsSettledInTheSameCycle:
    async def test_the_numbers_period_is_recorded(self, db_session, async_session):
        """Otherwise the monthly job debits the rent from the balance the plan
        just granted — paying for one number twice out of one collection."""
        org = await _org(async_session, "plan-rent")
        mandate = await _plan_mandate(async_session, org)
        charge = await _charge(async_session, org, mandate=mandate)

        await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        period = await async_session.scalar(
            select(RecurringChargePeriodModel).where(
                RecurringChargePeriodModel.recurring_charge_id == charge.id
            )
        )
        assert period is not None
        assert period.collected_via == "mandate"
        assert period.provider_payment_id == "pay_plan_1"

    async def test_the_rent_does_not_debit_the_balance(self, db_session, async_session):
        """The balance after a cycle is the whole grant, not the grant minus
        the rent. The rent was already inside what the bank collected."""
        org = await _org(async_session, "plan-no-debit")
        mandate = await _plan_mandate(async_session, org)
        await _charge(async_session, org, mandate=mandate)

        await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_a_plan_with_no_number_yet_still_grants(
        self, db_session, async_session
    ):
        """The plan can be authorised before a number is chosen. The balance is
        not held hostage to that ordering."""
        org = await _org(async_session, "plan-no-number")
        mandate = await _plan_mandate(async_session, org)

        result = await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        assert result["status"] == "granted"
        assert result["rental"]["status"] == "no_charge"
        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE


class TestWhichMandateCoversANumber:
    async def test_the_plan_is_preferred_over_a_rental_mandate(
        self, db_session, async_session
    ):
        """An account on the plan has already authorised the rent. Sending it
        to take out a rental mandate as well would collect twice."""
        org = await _org(async_session, "plan-preferred")
        rental = PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            subscription_id="sub_rent",
            plan_id="plan_rent",
            status=MandateStatus.ACTIVE.value,
            price_paise=NUMBER_RENTAL_PRICE_PAISE,
        )
        async_session.add(rental)
        await async_session.flush()
        plan = await _plan_mandate(async_session, org, sub="sub_plan_2")

        found = await mandate_service.mandate_for_numbers(
            async_session, organization_id=org.id
        )
        assert found is not None
        assert found.id == plan.id

    async def test_a_rental_mandate_alone_still_covers_a_number(
        self, db_session, async_session
    ):
        """Accounts that predate the plan keep working unchanged."""
        org = await _org(async_session, "rental-only")
        rental = PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            subscription_id="sub_rent_2",
            plan_id="plan_rent",
            status=MandateStatus.ACTIVE.value,
            price_paise=NUMBER_RENTAL_PRICE_PAISE,
        )
        async_session.add(rental)
        await async_session.flush()

        found = await mandate_service.mandate_for_numbers(
            async_session, organization_id=org.id
        )
        assert found is not None
        assert found.id == rental.id

    async def test_no_mandate_at_all_is_none(self, db_session, async_session):
        org = await _org(async_session, "no-mandate")
        assert (
            await mandate_service.mandate_for_numbers(
                async_session, organization_id=org.id
            )
            is None
        )


class TestTheIndexItselfHolds:
    """The read-path check makes a redelivery cheap; the index makes a race
    correct. They are different guarantees and only one survives concurrency,
    so it is worth proving the database is carrying it rather than the Python.
    """

    async def test_a_duplicate_grant_is_refused_by_the_database(
        self, db_session, async_session
    ):
        org = await _org(async_session, "plan-index")
        mandate = await _plan_mandate(async_session, org)
        await grant_plan_cycle(
            async_session, mandate=mandate, event=_collection("sub_plan_1")
        )

        # Straight past grant_plan_cycle's own check, the way a second worker
        # racing the first would arrive.
        duplicate = CreditLedgerModel(
            organization_id=org.id,
            delta_paise=STARTER_PLAN_BALANCE_PAISE,
            kind=CreditLedgerKind.PLAN.value,
            ref_type="plan_collection",
            ref_id="pay_plan_1",
            balance_after_paise=STARTER_PLAN_BALANCE_PAISE * 2,
        )
        with pytest.raises(IntegrityError):
            async with async_session.begin_nested():
                async_session.add(duplicate)
                await async_session.flush()

        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_the_index_does_not_block_a_different_collection(
        self, db_session, async_session
    ):
        """Scoped to the payment id, not to the account. A monthly plan grants
        every month."""
        org = await _org(async_session, "plan-index-open")
        mandate = await _plan_mandate(async_session, org)

        await grant_plan_cycle(
            async_session,
            mandate=mandate,
            event=_collection("sub_plan_1", payment_id="pay_a"),
        )
        result = await grant_plan_cycle(
            async_session,
            mandate=mandate,
            event=_collection("sub_plan_1", payment_id="pay_b"),
        )
        assert result["status"] == "granted"


class TestTheWebhookRoutesOnPurpose:
    """Routing on the mandate's purpose, not on the amount. Two plans priced
    the same would be indistinguishable by amount, and the difference is what
    the customer was sold."""

    async def test_a_plan_collection_grants_a_balance(
        self, db_session, async_session, configured_webhook
    ):
        org = await _org(async_session, "route-plan")
        mandate = await _plan_mandate(async_session, org, sub="sub_route_plan")
        await _charge(async_session, org, mandate=mandate)

        outcome = await _deliver(async_session, _collection("sub_route_plan"))

        assert outcome["charged"] is True
        assert outcome["plan"]["status"] == "granted"
        assert await _balance(async_session, org) == STARTER_PLAN_BALANCE_PAISE

    async def test_a_rental_collection_grants_no_balance(
        self, db_session, async_session, configured_webhook
    ):
        """The path that existed before the plan, unchanged: a rental
        collection settles a period and touches no credit."""
        org = await _org(async_session, "route-rental")
        rental = PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            subscription_id="sub_route_rent",
            plan_id="plan_rent",
            status=MandateStatus.ACTIVE.value,
            price_paise=NUMBER_RENTAL_PRICE_PAISE,
        )
        async_session.add(rental)
        await async_session.flush()
        await _charge(async_session, org, mandate=rental)

        outcome = await _deliver(async_session, _collection("sub_route_rent"))

        assert outcome["charged"] is True
        assert "plan" not in outcome
        assert outcome["period"]["status"] not in (None, "no_charge")
        assert await _balance(async_session, org) == 0

"""What ₹2,999 entitles you to, and the balance at which calling stops.

Two features, one theme: a limit that is not enforced is not a limit, and both
of these were unenforced in a way that read as working.

**The plan's number.** A plan mandate authorises the rent for the numbers the
plan includes. A rental charge carrying a mandate is skipped by the monthly job
— the bank is collecting for it instead — while a collection settles exactly
one charge. So before ``included_numbers`` existed, an account on the plan could
provision any number of numbers and every one of them was skipped, one was
settled, and the rest were free for ever. Nothing logged an error, and the
monthly revenue simply came in lower than the number count implied.

**The floor.** Calling stopped at a balance of zero, which sounds right and is
not: a call's cost is not known until it ends, so the last call an account can
start is one it finishes overdrawn. The floor makes the last call one it could
always afford.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api import constants
from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
)
from api.enums import (
    CreditLedgerKind,
    MandateStatus,
    RecurringChargeStatus,
    RecurringChargeType,
)
from api.services.billing import mandates as mandate_service
from api.services.billing import rentals, reservations, subscription_plans


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _has_paid_before(session, org) -> None:
    """Give the account one settled top-up.

    The first-payment floor is higher than the ordinary one, so a brand-new
    account is refused on the minimum before the step check is ever reached.
    These tests are about the step, so they need an account past its first
    payment — otherwise they assert on a message the amount never got to.
    """
    session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=100_000,
            kind=CreditLedgerKind.TOPUP.value,
            ref_type="payment",
            ref_id=f"pay_seed_{org.id}",
            balance_after_paise=100_000,
        )
    )
    await session.flush()


async def _mandate(session, org, *, purpose, plan_code=None, sub="sub_1"):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=purpose,
        subscription_id=sub,
        status=MandateStatus.ACTIVE.value,
        price_paise=299_900,
        plan_code=plan_code,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _number(session, org, *, mandate_id, resource_id, status="active"):
    charge = RecurringChargeModel(
        organization_id=org.id,
        charge_type=RecurringChargeType.NUMBER_RENTAL.value,
        resource_id=resource_id,
        status=status,
        cost_paise=25_000,
        price_paise=49_900,
        mandate_id=mandate_id,
        started_at=datetime.now(UTC),
        next_charge_at=datetime.now(UTC),
    )
    session.add(charge)
    await session.flush()
    return charge


async def _credit(session, org, paise: int):
    session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=paise,
            kind=CreditLedgerKind.TOPUP.value,
            ref_type="test",
            ref_id=f"seed-{org.id}-{paise}",
            balance_after_paise=paise,
            note="seed",
        )
    )
    await session.flush()


class TestThePlanIncludesOneNumber:
    """₹2,999 buys a number. It does not buy every number."""

    async def test_the_first_number_is_covered_by_the_plan(self, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "first-number")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            plan_code=subscription_plans.STARTER,
        )

        attached = await rentals._mandate_for_this_number(
            async_session, organization_id=org.id, mandate_id=mandate.id
        )

        assert attached == mandate.id

    async def test_the_second_number_bills_from_the_balance(self, async_session):
        """The leak: without this, number two was skipped and never settled."""
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "second-number")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            plan_code=subscription_plans.STARTER,
            sub="sub_2",
        )
        await _number(async_session, org, mandate_id=mandate.id, resource_id=1)

        attached = await rentals._mandate_for_this_number(
            async_session, organization_id=org.id, mandate_id=mandate.id
        )

        # None means "the monthly job bills this one from the balance", which
        # is what makes an extra number cost ₹499 a month rather than nothing.
        assert attached is None

    async def test_a_released_number_gives_the_entitlement_back(self, async_session):
        """An account that hands its number back can claim another one."""
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "released")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            plan_code=subscription_plans.STARTER,
            sub="sub_3",
        )
        await _number(
            async_session,
            org,
            mandate_id=mandate.id,
            resource_id=1,
            status=RecurringChargeStatus.RELEASED.value,
        )

        attached = await rentals._mandate_for_this_number(
            async_session, organization_id=org.id, mandate_id=mandate.id
        )

        assert attached == mandate.id

    async def test_a_bigger_plan_covers_more_numbers(self, async_session):
        """The whole point of the plans table: a second plan needs no release."""
        org = await _org(async_session, "bigger")
        await subscription_plans.save(
            async_session,
            code="growth",
            label="Growth",
            price_paise=699_900,
            balance_paise=630_000,
            included_numbers=2,
            extra_number_price_paise=55_900,
        )
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            plan_code="growth",
            sub="sub_4",
        )
        await _number(async_session, org, mandate_id=mandate.id, resource_id=1)

        # Second is still inside the entitlement...
        assert (
            await rentals._mandate_for_this_number(
                async_session, organization_id=org.id, mandate_id=mandate.id
            )
            == mandate.id
        )

        await _number(async_session, org, mandate_id=mandate.id, resource_id=2)

        # ...the third is not.
        assert (
            await rentals._mandate_for_this_number(
                async_session, organization_id=org.id, mandate_id=mandate.id
            )
            is None
        )

    async def test_a_rental_mandate_covers_exactly_one(self, async_session):
        """The same hole existed for a plain rental mandate, and is the same fix."""
        org = await _org(async_session, "rental-mandate")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            sub="sub_5",
        )
        assert (
            await rentals.numbers_a_mandate_covers(async_session, mandate=mandate) == 1
        )

        await _number(async_session, org, mandate_id=mandate.id, resource_id=1)
        assert (
            await rentals._mandate_for_this_number(
                async_session, organization_id=org.id, mandate_id=mandate.id
            )
            is None
        )

    async def test_the_extra_number_is_priced_by_the_plan(self, async_session):
        org = await _org(async_session, "extra-price")
        await subscription_plans.save(
            async_session,
            code="growth2",
            label="Growth",
            price_paise=699_900,
            balance_paise=630_000,
            included_numbers=2,
            extra_number_price_paise=55_900,
        )
        await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            plan_code="growth2",
            sub="sub_6",
        )

        assert (
            await rentals.next_number_price_paise(async_session, organization_id=org.id)
            == 55_900
        )

    async def test_an_account_with_no_plan_pays_the_platform_price(self, async_session):
        org = await _org(async_session, "no-plan")
        assert (
            await rentals.next_number_price_paise(async_session, organization_id=org.id)
            == constants.NUMBER_RENTAL_PRICE_PAISE
        )


class TestAPlanCannotBeSoldAtALoss:
    async def test_granting_more_balance_than_it_collects_is_refused(
        self, async_session
    ):
        """Balance is spendable at our cost the moment it lands."""
        with pytest.raises(subscription_plans.PlanError, match="loses money"):
            await subscription_plans.save(
                async_session,
                code="silly",
                label="Silly",
                price_paise=100_000,
                balance_paise=250_000,
                included_numbers=1,
            )

    async def test_the_seeded_starter_is_exactly_what_was_being_sold(
        self, async_session
    ):
        plan = await subscription_plans.ensure_seeded(async_session)

        assert plan.price_paise == constants.STARTER_PLAN_PRICE_PAISE
        assert plan.balance_paise == constants.STARTER_PLAN_BALANCE_PAISE
        assert plan.included_numbers == 1
        # ₹2,500 of balance + one ₹499 number sold for ₹2,999: priced exactly at
        # its parts, which is the deliberate ₹0 discount the plan was designed
        # around rather than an accident.
        assert plan.parts_paise == plan.price_paise
        assert plan.discount_paise == 0

    async def test_seeding_twice_does_not_overwrite_an_edited_plan(self, async_session):
        await subscription_plans.ensure_seeded(async_session)
        await subscription_plans.save(
            async_session,
            code=subscription_plans.STARTER,
            label="Starter",
            price_paise=349_900,
            balance_paise=300_000,
            included_numbers=1,
        )

        again = await subscription_plans.ensure_seeded(async_session)

        assert again.price_paise == 349_900


class TestCallingStopsAtTheFloor:
    """₹20, not zero. See the module docstring for why zero is the wrong floor."""

    @pytest.fixture(autouse=True)
    def _enforcement_on(self, monkeypatch):
        monkeypatch.setattr(reservations, "BALANCE_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(reservations, "MIN_BALANCE_PAISE", 2_000)

    async def test_an_account_below_the_floor_cannot_start_a_call(self, async_session):
        org = await _org(async_session, "below-floor")
        await _credit(async_session, org, 1_900)

        assert (
            await reservations.has_credit(async_session, organization_id=org.id)
            is False
        )
        assert (
            await reservations.reserve(
                async_session, organization_id=org.id, workflow_run_id=1
            )
            is None
        )

    async def test_an_account_exactly_at_the_floor_can(self, async_session):
        """Inclusive. A customer told the floor is ₹20 can spend at ₹20."""
        org = await _org(async_session, "at-floor")
        await _credit(async_session, org, 2_000)

        assert (
            await reservations.has_credit(async_session, organization_id=org.id) is True
        )

    async def test_a_call_already_holding_funds_is_never_refused(self, async_session):
        """A reconnect must not drop a call the customer has already paid for."""
        org = await _org(async_session, "reconnect")
        await _credit(async_session, org, 50_000)

        held = await reservations.reserve(
            async_session, organization_id=org.id, workflow_run_id=77
        )
        assert held is not None

        # The hold has driven the balance to where a naive check would refuse.
        assert (
            await reservations.has_credit(
                async_session, organization_id=org.id, workflow_run_id=77
            )
            is True
        )

    async def test_the_message_names_the_floor_and_the_way_back(self):
        from api.services.quota_service import no_credit_message

        message = no_credit_message()

        # "Out of credit" to an account showing ₹18 reads as our arithmetic
        # being wrong rather than as something they can fix.
        assert "₹20" in message
        assert "₹100" in message


class TestTopUpsComeInSteps:
    def test_the_minimum_is_itself_a_valid_step(self):
        """A floor that is not a multiple of the step is a floor nobody can pay."""
        assert constants.MIN_TOPUP_PAISE % constants.TOPUP_INCREMENT_PAISE == 0

    @pytest.mark.parametrize("amount", [10_000, 20_000, 100_000])
    async def test_a_whole_step_is_accepted(self, async_session, amount):
        from api.services.billing.payments import PaymentError, create_topup_order

        org = await _org(async_session, f"topup-ok-{amount}")
        await _has_paid_before(async_session, org)
        # Refused for want of Razorpay credentials, never for the amount: the
        # amount check runs first and this asserts it did not fire.
        with pytest.raises(PaymentError) as caught:
            await create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=amount,
                created_by=None,
            )
        assert "steps of" not in str(caught.value)

    @pytest.mark.parametrize("amount", [13_700, 10_001, 99_999])
    async def test_anything_else_is_refused_with_the_next_step_named(
        self, async_session, amount
    ):
        from api.services.billing.payments import PaymentError, create_topup_order

        org = await _org(async_session, f"topup-bad-{amount}")
        await _has_paid_before(async_session, org)
        with pytest.raises(PaymentError, match="steps of ₹100"):
            await create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=amount,
                created_by=None,
            )

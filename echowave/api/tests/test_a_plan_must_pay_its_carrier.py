"""A plan's price has to cover what its contents *cost*, not just what they list at.

`save()` already refused a plan granting more balance than it collects — balance
is spendable immediately and at our cost, so that one is a loss on contact. But
balance is not the only cost inside a plan. An included number is rented from a
carrier every month whether the customer calls or not, and a plan priced just
above its balance still loses exactly that rent, on every account, every cycle.

The gap is narrow and entirely plausible on a form. ₹2,000 collected, ₹1,990 of
balance and one number: the old check passes it by ₹10 and the carrier takes
₹250. Nothing downstream notices — the plan sells, the mandate authorises, calls
connect — and the loss shows up in a month-end reconciliation nobody runs until
something else goes wrong.
"""

from __future__ import annotations

import pytest

from api import constants
from api.services.billing import subscription_plans
from api.services.billing.subscription_plans import PlanError


@pytest.mark.asyncio
class TestTheCostOfWhatAPlanIncludes:
    async def test_a_plan_that_cannot_pay_its_carrier_rent_is_refused(
        self, db_session, async_session
    ):
        """₹3,000 of balance (₹1,860 at cost) and a ₹250 number out of
        ₹2,000 collected: the balance alone clears, the rent tips it."""
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="thin",
                label="Thin",
                price_paise=200_000,
                balance_paise=300_000,
                included_numbers=1,
                razorpay_plan_id="plan_thin",
            )
        message = str(caught.value)
        assert "carrier" in message
        # Named in rupees, not paise: an operator has to know how far to move
        # the balance, and a figure in paise reads as a hundredfold error.
        assert "110.00" in message, message

    async def test_the_balance_alone_would_have_let_that_through(self):
        """Why the rent is in the sum. Balance is checked at cost (a credit is
        marked-up cost, KAN-52), and ₹3,000 at cost is ₹1,860, which
        ₹2,000 covers; only the ₹250 of rent makes it a loss."""
        assert 300_000 * subscription_plans.BALANCE_COST_BPS // 10_000 < 200_000

    async def test_the_shortfall_scales_with_the_numbers_included(
        self, db_session, async_session
    ):
        """Four numbers is four times the rent. A check that priced one and
        assumed the rest were free would pass the plans that lose the most."""
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="four",
                label="Four",
                price_paise=200_000,
                balance_paise=200_000,
                included_numbers=4,
                razorpay_plan_id="plan_four",
            )
        # 200,000 at cost is 124,000; plus 4 x 25,000 is 224,000 against
        # 200,000 collected.
        assert "240.00" in str(caught.value), str(caught.value)

    async def test_a_solvent_plan_saves(self, db_session, async_session):
        """The shape actually being sold: ₹2,999 net covers ₹2,500 of balance
        and a ₹250 number with ₹249 left over."""
        plan = await subscription_plans.save(
            async_session,
            code="starter-like",
            label="Starter",
            price_paise=constants.STARTER_PLAN_PRICE_PAISE,
            balance_paise=constants.STARTER_PLAN_BALANCE_PAISE,
            included_numbers=1,
            razorpay_plan_id="plan_ok",
        )
        assert plan.price_paise == constants.STARTER_PLAN_PRICE_PAISE
        margin = (
            plan.price_paise
            - plan.balance_paise
            - plan.included_numbers * constants.NUMBER_RENTAL_COST_PAISE
        )
        assert margin > 0

    async def test_a_plan_including_no_numbers_is_unaffected(
        self, db_session, async_session
    ):
        """The carrier cost is zero, so this check must not fire at all —
        otherwise every balance-only plan priced at its contents is refused."""
        plan = await subscription_plans.save(
            async_session,
            code="credit-only",
            label="Credit",
            price_paise=100_000,
            balance_paise=100_000,
            included_numbers=0,
            razorpay_plan_id="plan_credit",
        )
        assert plan.included_numbers == 0

    async def test_the_error_says_which_lever_to_pull(self, db_session, async_session):
        """Three of them, because which one is right is a pricing decision and
        the message should not pretend otherwise."""
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="levers",
                label="Levers",
                price_paise=200_000,
                balance_paise=300_000,
                included_numbers=1,
            )
        message = str(caught.value)
        assert "Lower the balance" in message
        assert "raise the price" in message
        assert "fewer numbers" in message

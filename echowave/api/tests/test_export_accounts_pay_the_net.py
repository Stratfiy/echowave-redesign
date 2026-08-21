"""An account outside India is charged what it owes, or refused — never 18% over.

A pinned Razorpay plan charges everyone the same amount, and that amount is what
the bank collects: nothing in this codebase can change it once the instruction
is held. The domestic figure is the gross — ₹2,999 net plus 18% GST is
₹3,538.82.

An account outside India with an LUT on file is **zero-rated** and owes the net
₹2,999. Subscribing it to the domestic plan overcharges it by the tax, every
month, for as long as the mandate lives, and the receipt voucher issued against
it would split a tax that was never owed.

The amount guard already refuses that mismatch rather than collecting it, which
left such an account unable to subscribe at all — the right direction and not a
fix. The fix is a second provider plan on the plan row, created at the net
price, and these tests hold the three things that makes true: the right plan is
picked, a missing one refuses with the figure to create it at, and one id
cannot be pinned at two amounts.
"""

from __future__ import annotations

import pytest

from api.services.billing import subscription_plans
from api.services.billing.tax import compute_tax


class TestTheTaxItself:
    """The premise, asserted rather than assumed."""

    def test_an_export_with_an_lut_owes_the_net(self, monkeypatch):
        from api.services.billing import tax

        monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)
        breakdown = compute_tax(
            taxable_paise=299_900, country_code="US", state_code=None
        )

        assert breakdown.supply_type == "export"
        assert breakdown.total_paise == 299_900
        assert breakdown.rate_basis_points == 0

    def test_a_domestic_account_owes_the_gross(self):
        breakdown = compute_tax(
            taxable_paise=299_900, country_code="IN", state_code="29"
        )

        # ₹3,538.82 — the figure the Razorpay plan must be created at.
        assert breakdown.total_paise == 353_882
        assert breakdown.supply_type != "export"


class TestThePlanRowCarriesBoth:
    async def test_an_export_plan_id_is_stored_and_read_back(
        self, db_session, async_session
    ):
        plan = await subscription_plans.save(
            async_session,
            code="starter-two",
            label="Starter",
            price_paise=299_900,
            balance_paise=250_000,
            included_numbers=1,
            extra_number_price_paise=49_900,
            razorpay_plan_id="plan_domestic",
            razorpay_plan_id_export="plan_export",
        )

        assert plan.razorpay_plan_id == "plan_domestic"
        assert plan.razorpay_plan_id_export == "plan_export"

    async def test_one_id_cannot_be_pinned_at_two_amounts(
        self, db_session, async_session
    ):
        """Pointing both fields at one provider plan does not make an export
        account pay the net. It makes the amount guard pass for one of them and
        refuse the other, depending on who subscribes."""
        with pytest.raises(
            subscription_plans.PlanError, match="separate Razorpay plan"
        ):
            await subscription_plans.save(
                async_session,
                code="starter-same",
                label="Starter",
                price_paise=299_900,
                balance_paise=250_000,
                included_numbers=1,
                extra_number_price_paise=49_900,
                razorpay_plan_id="plan_one",
                razorpay_plan_id_export="plan_one",
            )

    async def test_a_plan_with_no_export_id_is_still_a_plan(
        self, db_session, async_session
    ):
        """Most deployments will never have an export customer. Requiring the
        second plan up front would make everybody create something nobody
        needs."""
        plan = await subscription_plans.save(
            async_session,
            code="starter-domestic",
            label="Starter",
            price_paise=299_900,
            balance_paise=250_000,
            included_numbers=1,
            extra_number_price_paise=49_900,
            razorpay_plan_id="plan_domestic",
        )

        assert plan.razorpay_plan_id_export is None


class TestWhichPlanAMandateSubscribesTo:
    """The decision, read off the source.

    The provider call itself needs live Razorpay credentials and a network, so
    what is pinned here is the branch: an export account must reach the export
    id, and a missing one must raise rather than fall through to the domestic
    plan. Falling through is the whole bug.
    """

    def test_the_supply_type_picks_the_plan(self):
        import inspect

        from api.services.billing import mandates

        source = inspect.getsource(mandates.create_plan_mandate)

        assert 'breakdown.supply_type == "export"' in source
        assert (
            "plan.razorpay_plan_id_export if is_export else plan.razorpay_plan_id"
            in source
        )

    def test_a_missing_export_plan_refuses_rather_than_falling_through(self):
        import inspect

        from api.services.billing import mandates

        source = inspect.getsource(mandates.create_plan_mandate)

        assert "if is_export and plan is not None and not pinned:" in source
        assert "raise MandateError(" in source
        # The refusal has to name the amount to create it at; an operator
        # reading "cannot subscribe" with no figure has to derive the net
        # price from a tax rule to act on it.
        assert "zero-rated for GST" in source
        assert "/superadmin/billing/plans" in source

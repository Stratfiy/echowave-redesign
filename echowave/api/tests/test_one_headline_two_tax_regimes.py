"""Selling "₹2,999" to everybody, when only some of them pay GST.

A domestic account is charged the net plus 18%; a zero-rated export account is
charged the net outright. So a plan with one headline price is two different
nets that happen to collect the same amount — ₹2,541.53 domestic and ₹2,999
export both take ₹2,999 from the customer.

**The arithmetic that makes this dangerous.** Holding the headline constant
lowers the domestic net by the tax, and the balance granted does not move with
it. ₹2,999 including GST is ₹2,541.53 net; grant ₹2,500 of balance out of that
and ₹41.53 is left to rent a number that costs ₹250 a month. That is a loss of
₹208.47 per account per cycle, and the pre-existing guard misses it — it
compares balance against price and 2,500 < 2,541.53 passes.

So the guard has to know what the plan's contents *cost*, not just what they
are worth. These tests are that guard and the two-net mechanism it protects.
"""

from __future__ import annotations

import pytest

from api import constants
from api.services.billing import subscription_plans
from api.services.billing.subscription_plans import Plan, PlanError

GST_MULTIPLIER = 1.18
HEADLINE = 299_900
DOMESTIC_NET = round(HEADLINE / GST_MULTIPLIER)  # 254153


def _plan(**kw) -> Plan:
    base = dict(
        code="starter",
        label="Starter",
        blurb="",
        price_paise=HEADLINE,
        balance_paise=250_000,
        included_numbers=1,
        extra_number_price_paise=constants.NUMBER_RENTAL_PRICE_PAISE,
        knowledge_base_bytes=0,
        knowledge_base_max_file_bytes=0,
        platform_rate_mpaise=None,
        razorpay_plan_id="plan_dom",
        razorpay_plan_id_export="plan_exp",
        enabled=True,
        sort_order=0,
    )
    base.update(kw)
    return Plan(**base)


class TestWhichNetAnAccountIsPricedAt:
    def test_an_unset_export_price_means_the_same_as_domestic(self):
        """What every plan did before the column existed. Anything else would
        reprice existing export accounts the moment this shipped."""
        plan = _plan(price_paise_export=None)
        assert plan.net_for(is_export=True) == plan.price_paise
        assert plan.net_for(is_export=False) == plan.price_paise

    def test_a_set_export_price_is_used_only_for_export(self):
        plan = _plan(price_paise=DOMESTIC_NET, price_paise_export=HEADLINE)
        assert plan.net_for(is_export=False) == DOMESTIC_NET
        assert plan.net_for(is_export=True) == HEADLINE

    def test_one_headline_reaches_the_customer_from_both_regimes(self):
        """The point of the whole mechanism, stated as arithmetic: a domestic
        account grossed up and an export account outright both pay ₹2,999."""
        plan = _plan(price_paise=DOMESTIC_NET, price_paise_export=HEADLINE)

        domestic_charged = round(plan.net_for(is_export=False) * GST_MULTIPLIER)
        export_charged = plan.net_for(is_export=True)

        assert abs(domestic_charged - HEADLINE) <= 1, (
            "a rupee of rounding is tolerable; more means the two headlines "
            "differ on a customer's statement"
        )
        assert export_charged == HEADLINE

    def test_the_export_price_is_a_net_not_a_gross(self):
        """A zero-rated account owes the net outright, so the column holds a
        net like every other money column here. Storing a gross would double
        the tax the first time somebody grossed it up."""
        plan = _plan(price_paise=DOMESTIC_NET, price_paise_export=HEADLINE)
        assert plan.net_for(is_export=True) == HEADLINE


@pytest.mark.asyncio
class TestAPlanThatCannotPayForItself:
    async def test_the_exact_loss_this_change_would_have_created(
        self, db_session, async_session
    ):
        """₹2,999 including GST, with the balance left at ₹2,500.

        The number costs ₹250 a month from the carrier and only ₹41.53 is left
        to pay for it. Refused, with the shortfall named — a plan that loses
        money silently loses it on every account, every cycle, for as long as
        nobody does this arithmetic by hand.
        """
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="starter-inclusive",
                label="Starter",
                price_paise=DOMESTIC_NET,
                balance_paise=250_000,
                included_numbers=1,
                razorpay_plan_id="plan_dom",
            )
        message = str(caught.value)
        assert "208.47" in message, message
        assert "carrier" in message

    async def test_the_old_guard_alone_would_have_let_it_through(self):
        """Why a second check was needed rather than a stricter first one: the
        original compares balance against price, and ₹2,500 against ₹2,541.53
        passes."""
        assert 250_000 < DOMESTIC_NET

    async def test_lowering_the_balance_makes_it_sellable(
        self, db_session, async_session
    ):
        """The remedy the error names, and the figure that keeps the included
        number worth what it was: ₹2,541.53 net less ₹499."""
        balance = DOMESTIC_NET - 49_900
        plan = await subscription_plans.save(
            async_session,
            code="starter-fixed",
            label="Starter",
            price_paise=DOMESTIC_NET,
            balance_paise=balance,
            included_numbers=1,
            razorpay_plan_id="plan_dom_fixed",
        )
        assert plan.price_paise == DOMESTIC_NET
        assert plan.balance_paise == balance

    async def test_a_plan_including_no_numbers_is_unaffected(
        self, db_session, async_session
    ):
        """The carrier cost is zero, so the new check must not fire at all —
        otherwise every balance-only plan priced near its contents is refused."""
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

    async def test_the_shortfall_is_named_in_rupees_not_paise(
        self, db_session, async_session
    ):
        """An operator reading it has to know how much to move the balance by.
        A figure in paise reads as a hundredfold error and gets ignored."""
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="thin",
                label="Thin",
                price_paise=200_000,
                balance_paise=199_000,
                included_numbers=1,
                razorpay_plan_id="plan_thin",
            )
        # Rs1,990 balance + Rs250 carrier rent against Rs2,000 collected.
        assert "240.00" in str(caught.value), str(caught.value)


@pytest.mark.asyncio
class TestTheExportNetSurvivesARoundTrip:
    async def test_it_is_stored_and_read_back(self, db_session, async_session):
        plan = await subscription_plans.save(
            async_session,
            code="dual",
            label="Dual",
            price_paise=DOMESTIC_NET,
            price_paise_export=HEADLINE,
            balance_paise=DOMESTIC_NET - 49_900,
            included_numbers=1,
            razorpay_plan_id="plan_d",
            razorpay_plan_id_export="plan_e",
        )
        assert plan.price_paise_export == HEADLINE
        assert plan.net_for(is_export=True) == HEADLINE
        assert plan.net_for(is_export=False) == DOMESTIC_NET

    async def test_omitting_it_stores_null_rather_than_a_copy(
        self, db_session, async_session
    ):
        """A copy goes stale the day the domestic price moves, and then quietly
        prices export accounts at last month's figure."""
        plan = await subscription_plans.save(
            async_session,
            code="single",
            label="Single",
            price_paise=HEADLINE,
            balance_paise=200_000,
            included_numbers=1,
            razorpay_plan_id="plan_s",
        )
        assert plan.price_paise_export is None


@pytest.mark.asyncio
class TestWhatTheMandateActuallyPricesAtNo:
    """The gap a mutation found: `net_for` existing is not the same as the
    mandate path using it.

    Nothing else notices if it does not. The plan screen shows the right two
    figures, the export plan id is present, the mandate authorises — and the
    bank collects the domestic net from an export account, monthly, for the
    life of the instruction.
    """

    async def _export_org(self, session, slug: str):
        from api.db.models import OrganizationModel
        from api.services.billing import billing_profile

        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        session.add(org)
        await session.flush()
        await billing_profile.save_profile(
            session,
            organization_id=org.id,
            legal_name="Acme Inc",
            address_line1="1 Market St",
            country_code="US",
        )
        return org

    async def _domestic_org(self, session, slug: str):
        from api.db.models import OrganizationModel
        from api.services.billing import billing_profile

        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        session.add(org)
        await session.flush()
        await billing_profile.save_profile(
            session,
            organization_id=org.id,
            legal_name="Acme Pvt Ltd",
            address_line1="1 MG Road",
            state_code="29",
            country_code="IN",
        )
        return org

    async def test_an_export_account_is_priced_at_the_export_net(
        self, db_session, async_session
    ):
        """Asked of the profile rather than of the tax breakdown, because the
        net has to be chosen *before* tax is computed — choosing it afterwards
        prices the account on one figure and charges it the other."""
        from api.services.billing import billing_profile

        org = await self._export_org(async_session, "exp-price")
        profile = await billing_profile.get_profile(
            async_session, organization_id=org.id
        )
        plan = _plan(price_paise=DOMESTIC_NET, price_paise_export=HEADLINE)

        assert profile.is_export is True
        assert plan.net_for(is_export=profile.is_export) == HEADLINE

    async def test_a_domestic_account_is_priced_at_the_domestic_net(
        self, db_session, async_session
    ):
        from api.services.billing import billing_profile

        org = await self._domestic_org(async_session, "dom-price")
        profile = await billing_profile.get_profile(
            async_session, organization_id=org.id
        )
        plan = _plan(price_paise=DOMESTIC_NET, price_paise_export=HEADLINE)

        assert profile.is_export is False
        assert plan.net_for(is_export=profile.is_export) == DOMESTIC_NET

    async def test_the_mandate_path_reads_the_profile_before_choosing_the_net(
        self,
    ):
        """Guards the ordering itself. `create_plan_mandate` used to compute the
        net from `plan.price_paise` and only afterwards learn, from the tax
        breakdown, which regime the account was in."""
        import inspect

        from api.services.billing import mandates

        source = inspect.getsource(mandates.create_plan_mandate)
        profile_at = source.index("profile = await get_profile")
        net_at = source.index("net_paise = int(")
        assert profile_at < net_at, (
            "the net is chosen before the profile is read, so an export "
            "account would be priced on the domestic figure"
        )
        assert "net_for(is_export=profile.is_export)" in source

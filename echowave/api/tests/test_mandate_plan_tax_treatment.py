"""A pinned Razorpay plan carries one amount. Not every account owes it.

The mandate path grosses up its net price against the account's own billing
profile — correctly, and for the reason ``tax.py`` states at length: a supply
outside India under an LUT is zero-rated, and charging GST on one overcharges a
customer in a way a credit note cannot fully undo.

That gross was then thrown away whenever ``RAZORPAY_STARTER_PLAN_ID`` or
``RAZORPAY_RENTAL_PLAN_ID`` was set, which is the configuration the code itself
asks operators to use. The export customer was subscribed to the domestic plan
and the bank collected the domestic gross — 18% of tax that was never due, once
a month, by standing instruction, with no invoice that could carry it.

These tests pin the behaviour at the seam: what amount the subscription is
created against, for two accounts whose only difference is where they are.
"""

from __future__ import annotations

import pytest

from api.db.models import BillingProfileModel, OrganizationModel
from api.services.billing import mandates as mandate_service
from api.services.billing.tax import gross_up


#: The domestic gross of the starter plan, which is what a pinned plan would
#: have been created at. Derived rather than typed — the plan price is itself
#: derived from the rental price, and a constant here would go stale the first
#: time either moved.
def _domestic_gross(net_paise: int) -> int:
    return gross_up(taxable_paise=net_paise, country_code="IN", state_code="29")


async def _org(session, slug: str, *, country_code: str, state_code: str | None):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    session.add(
        BillingProfileModel(
            organization_id=org.id,
            legal_name=f"{slug} Ltd",
            country_code=country_code,
            state_code=state_code,
        )
    )
    await session.flush()
    return org


@pytest.fixture
def razorpay(monkeypatch):
    """A recording stand-in for the two Razorpay calls that matter.

    ``created_against`` is what the test asserts on: the amount the bank would
    actually be told to collect, whether that came from the pinned plan or from
    one made for this account.
    """
    state: dict = {"created_against": None, "plans_created": [], "subscriptions": []}

    async def _post(path, payload, *, client=None):
        if path == "/plans":
            state["plans_created"].append(payload)
            state["created_against"] = int(payload["item"]["amount"])
            return {"id": f"plan_made_{len(state['plans_created'])}"}
        if path == "/subscriptions":
            state["subscriptions"].append(payload)
            return {
                "id": "sub_test",
                "status": "created",
                "short_url": "https://rzp.example/auth",
                "customer_id": "cust_test",
            }
        raise AssertionError(f"unexpected POST {path}")

    async def _get(path, *, client=None):
        assert path.startswith("/plans/")
        # The pinned plan, priced for a domestic account.
        state["created_against"] = state["pinned_amount"]
        return {
            "id": path.rsplit("/", 1)[1],
            "item": {"amount": state["pinned_amount"]},
        }

    monkeypatch.setattr(mandate_service, "_post", _post)
    monkeypatch.setattr(mandate_service, "_get", _get)
    monkeypatch.setattr(mandate_service, "_auth", lambda: ("key", "secret"))
    return state


class TestAPinnedPlanIsUsedOnlyWhenItCollectsWhatIsOwed:
    async def test_a_domestic_account_stays_on_the_pinned_plan(
        self, db_session, async_session, razorpay, monkeypatch
    ):
        """The common case, and the reason the plan is pinned at all: one plan
        id across environments, so the provider's own reporting is not split
        into fragments nobody can tell apart afterwards."""
        from api.constants import STARTER_PLAN_PRICE_PAISE

        razorpay["pinned_amount"] = _domestic_gross(STARTER_PLAN_PRICE_PAISE)
        monkeypatch.setattr(mandate_service, "RAZORPAY_STARTER_PLAN_ID", "plan_pinned")

        org = await _org(async_session, "domestic", country_code="IN", state_code="36")
        mandate = await mandate_service.create_plan_mandate(
            async_session, organization_id=org.id
        )

        assert mandate.plan_id == "plan_pinned"
        assert razorpay["plans_created"] == [], "a plan was made where one was pinned"

    async def test_a_zero_rated_export_is_not_collected_at_the_domestic_gross(
        self, db_session, async_session, razorpay, monkeypatch
    ):
        """The defect, stated as money.

        An export under an LUT owes the net figure and nothing more. Subscribed
        to the domestic plan it would be debited the domestic gross every
        month — tax that is not due, on a supply we cannot issue a taxed
        invoice for.
        """
        from api.constants import STARTER_PLAN_PRICE_PAISE

        monkeypatch.setattr("api.services.billing.tax.SUPPLIER_HAS_LUT", True)
        razorpay["pinned_amount"] = _domestic_gross(STARTER_PLAN_PRICE_PAISE)
        monkeypatch.setattr(mandate_service, "RAZORPAY_STARTER_PLAN_ID", "plan_pinned")

        org = await _org(async_session, "export", country_code="US", state_code=None)
        mandate = await mandate_service.create_plan_mandate(
            async_session, organization_id=org.id
        )

        assert mandate.plan_id != "plan_pinned"
        assert razorpay["created_against"] == STARTER_PLAN_PRICE_PAISE
        assert razorpay["created_against"] < razorpay["pinned_amount"]

    async def test_the_mandate_still_records_the_net_price(
        self, db_session, async_session, razorpay, monkeypatch
    ):
        """Whichever plan it lands on. Everything reading a mandate's price
        reads a net figure; the gross lives in the provider payload."""
        from api.constants import STARTER_PLAN_PRICE_PAISE

        monkeypatch.setattr("api.services.billing.tax.SUPPLIER_HAS_LUT", True)
        razorpay["pinned_amount"] = _domestic_gross(STARTER_PLAN_PRICE_PAISE)
        monkeypatch.setattr(mandate_service, "RAZORPAY_STARTER_PLAN_ID", "plan_pinned")

        org = await _org(async_session, "net-price", country_code="US", state_code=None)
        mandate = await mandate_service.create_plan_mandate(
            async_session, organization_id=org.id
        )

        assert mandate.price_paise == STARTER_PLAN_PRICE_PAISE


class TestAnUnreadablePlanDoesNotBlockASignup:
    async def test_a_failed_read_proceeds_on_the_pinned_plan(
        self, db_session, async_session, razorpay, monkeypatch
    ):
        """Act on evidence, never on its absence.

        Refusing — or fragmenting — because Razorpay did not answer would take
        every signup down with one provider blip, to guard against a mismatch
        nobody has observed. The pinned plan is right for almost every account.
        """
        from api.constants import STARTER_PLAN_PRICE_PAISE

        async def _failing_get(path, *, client=None):
            raise mandate_service.MandateError("Razorpay request failed: timeout")

        razorpay["pinned_amount"] = _domestic_gross(STARTER_PLAN_PRICE_PAISE)
        monkeypatch.setattr(mandate_service, "_get", _failing_get)
        monkeypatch.setattr(mandate_service, "RAZORPAY_STARTER_PLAN_ID", "plan_pinned")

        org = await _org(async_session, "blip", country_code="IN", state_code="36")
        mandate = await mandate_service.create_plan_mandate(
            async_session, organization_id=org.id
        )

        assert mandate.plan_id == "plan_pinned"
        assert razorpay["plans_created"] == []


class TestTheRentalMandateGoesThroughTheSameGate:
    async def test_an_export_rental_is_not_collected_at_the_domestic_gross(
        self, db_session, async_session, razorpay, monkeypatch
    ):
        """Both mandates share ``_ensure_plan``, so both are covered — but the
        rental is the one that has been live longest, and asserting it here
        stops a later split of the two helpers losing the check on one."""
        from api.constants import NUMBER_RENTAL_PRICE_PAISE

        monkeypatch.setattr("api.services.billing.tax.SUPPLIER_HAS_LUT", True)
        razorpay["pinned_amount"] = _domestic_gross(NUMBER_RENTAL_PRICE_PAISE)
        monkeypatch.setattr(mandate_service, "RAZORPAY_RENTAL_PLAN_ID", "plan_rent")

        org = await _org(
            async_session, "rent-export", country_code="US", state_code=None
        )
        mandate = await mandate_service.create_rental_mandate(
            async_session, organization_id=org.id
        )

        assert mandate.plan_id != "plan_rent"
        assert razorpay["created_against"] == NUMBER_RENTAL_PRICE_PAISE

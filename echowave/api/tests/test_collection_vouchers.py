"""The tax document for money a bank moved on a standing instruction.

Every prepaid top-up produced a receipt voucher. Every autopay collection
produced nothing — no invoice, no voucher, no line in a return — while the
customer's bank was debited every month. Time of supply for a service is the
earlier of invoice or payment, so tax fell due the instant the bank paid; the
only thing missing was the evidence.

The failure modes worth naming, because each one costs money or a filing:

* **Tax computed on a tax-inclusive figure.** The provider reports the gross,
  because the mandate was registered for the gross. Treating it as the taxable
  value adds 18% to a number that already contains it, and the document
  overstates both the supply and the liability.
* **A second voucher for a redelivered webhook.** Razorpay delivers at least
  once. Two vouchers for one payment is a correction to file, not a row to
  delete.
* **A voucher per half of the starter plan.** The customer paid once. One
  collection buys a call balance *and* a month of rent, and two documents for
  one payment is two entries in a return.
* **A voucher failure eating the collection.** The bank has already moved the
  money and the period is already recorded. A webhook that raises here is
  retried against a settled collection forever.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.constants import NUMBER_RENTAL_PRICE_PAISE, STARTER_PLAN_PRICE_PAISE
from api.db.models import (
    BillingProfileModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
    TaxDocumentModel,
)
from api.enums import MandateStatus, RecurringChargeStatus, RecurringChargeType
from api.services.billing import documents, payments, tax
from api.services.billing import mandates as mandate_service

OUR_STATE = "29"
WEBHOOK_SECRET = "whsec_test_do_not_use_in_production"

#: ₹2,999 plus 18%, which is what the bank is actually told to collect.
PLAN_GROSS_PAISE = 353_882
RENTAL_GROSS_PAISE = 58_882


@pytest.fixture(autouse=True)
def supplier_configured(monkeypatch):
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(tax, "GST_RATE_BASIS_POINTS", 1800)
    monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Test Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "29AAAAA0000A1Z5")
    monkeypatch.setattr(documents, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(documents, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)


async def _org(session, slug: str, *, state=OUR_STATE, country="IN"):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    session.add(
        BillingProfileModel(
            organization_id=org.id,
            legal_name=f"{slug.title()} Pvt Ltd",
            address_line1="1 Test Road",
            city="Bengaluru",
            state_code=state,
            country_code=country,
        )
    )
    await session.flush()
    return org


async def _mandate(session, org, *, purpose, sub, price):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=purpose,
        subscription_id=sub,
        plan_id="plan_x",
        status=MandateStatus.ACTIVE.value,
        price_paise=price,
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
        cost_paise=25_000,
        price_paise=NUMBER_RENTAL_PRICE_PAISE,
        mandate_id=mandate.id,
        started_at=datetime.now(UTC) - timedelta(days=1),
        next_charge_at=datetime.now(UTC),
    )
    session.add(charge)
    await session.flush()
    return charge


def _collection(subscription_id: str, *, payment_id: str, amount: int) -> dict:
    return {
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {"id": subscription_id, "status": "active"}},
            "payment": {"entity": {"id": payment_id, "amount": amount}},
        },
    }


async def _deliver(session, event: dict) -> dict:
    """Through the real signed path: a test that skips verification would pass
    while every genuine delivery was rejected."""
    raw = json.dumps(event).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await payments.handle_webhook(session, raw_body=raw, signature=signature)


async def _vouchers(session, org) -> list[TaxDocumentModel]:
    return list(
        (
            await session.scalars(
                select(TaxDocumentModel).where(
                    TaxDocumentModel.organization_id == org.id,
                    TaxDocumentModel.kind == documents.RECEIPT_VOUCHER,
                )
            )
        ).all()
    )


class TestTheGrossIsSplitRatherThanTaxedAgain:
    async def test_the_taxable_value_is_recovered_from_what_the_bank_took(
        self, db_session, async_session
    ):
        """₹3,538.82 collected is ₹2,999.00 of supply and ₹539.82 of tax.

        Not ₹3,538.82 of supply — which is what treating the provider's figure
        as taxable would record, and which would overstate the liability by the
        tax on the tax.
        """
        org = await _org(async_session, "gross")

        voucher = await documents.issue_collection_voucher(
            async_session,
            organization_id=org.id,
            provider_payment_id="pay_gross_1",
            gross_paise=PLAN_GROSS_PAISE,
            description="Starter plan",
        )

        assert voucher is not None
        assert voucher.taxable_paise == STARTER_PLAN_PRICE_PAISE
        assert voucher.total_paise == PLAN_GROSS_PAISE
        assert voucher.cgst_paise + voucher.sgst_paise == 53_982
        assert voucher.igst_paise == 0

    async def test_the_document_totals_to_exactly_what_was_collected(
        self, db_session, async_session
    ):
        """The property that makes the split safe at any amount: taxable plus
        tax equals the money that moved, to the paise, or the return will not
        reconcile against the bank statement."""
        org = await _org(async_session, "totals")

        for i, gross in enumerate((100, 1_181, 58_882, 353_882, 999_999)):
            voucher = await documents.issue_collection_voucher(
                async_session,
                organization_id=org.id,
                provider_payment_id=f"pay_total_{i}",
                gross_paise=gross,
                description="Collection",
            )
            assert voucher is not None
            assert (
                voucher.taxable_paise
                + voucher.cgst_paise
                + voucher.sgst_paise
                + voucher.igst_paise
                == gross
            )


class TestOneCollectionOneDocument:
    async def test_a_redelivered_collection_reuses_the_voucher(
        self, db_session, async_session
    ):
        org = await _org(async_session, "redeliver")

        first = await documents.issue_collection_voucher(
            async_session,
            organization_id=org.id,
            provider_payment_id="pay_same",
            gross_paise=RENTAL_GROSS_PAISE,
            description="Monthly phone number rental",
        )
        second = await documents.issue_collection_voucher(
            async_session,
            organization_id=org.id,
            provider_payment_id="pay_same",
            gross_paise=RENTAL_GROSS_PAISE,
            description="Monthly phone number rental",
        )

        assert first is not None and second is not None
        assert first.number == second.number
        assert len(await _vouchers(async_session, org)) == 1

    async def test_the_starter_plan_gets_one_voucher_not_two(
        self, db_session, async_session
    ):
        """One collection buys a balance and a month of rent. The customer paid
        once, so there is one supply to document."""
        org = await _org(async_session, "plan")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_STARTER_PLAN,
            sub="sub_plan_v",
            price=STARTER_PLAN_PRICE_PAISE,
        )
        await _charge(async_session, org, mandate=mandate)

        await _deliver(
            async_session,
            _collection("sub_plan_v", payment_id="pay_plan_v", amount=PLAN_GROSS_PAISE),
        )

        issued = await _vouchers(async_session, org)
        assert len(issued) == 1
        assert issued[0].taxable_paise == STARTER_PLAN_PRICE_PAISE
        assert issued[0].provider_payment_id == "pay_plan_v"


class TestTheWebhookPathIssuesOne:
    async def test_a_rental_collection_is_documented(self, db_session, async_session):
        org = await _org(async_session, "rental")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            sub="sub_rent_v",
            price=NUMBER_RENTAL_PRICE_PAISE,
        )
        await _charge(async_session, org, mandate=mandate)

        outcome = await _deliver(
            async_session,
            _collection(
                "sub_rent_v", payment_id="pay_rent_v", amount=RENTAL_GROSS_PAISE
            ),
        )

        assert outcome.get("voucher") is not None
        issued = await _vouchers(async_session, org)
        assert len(issued) == 1
        assert issued[0].taxable_paise == NUMBER_RENTAL_PRICE_PAISE

    async def test_a_voucher_failure_does_not_lose_the_collection(
        self, db_session, async_session, monkeypatch
    ):
        """The money has moved and the period is recorded. Raising here would
        have the provider retry a settled collection until it gave up.
        """
        org = await _org(async_session, "resilient")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            sub="sub_boom",
            price=NUMBER_RENTAL_PRICE_PAISE,
        )
        charge = await _charge(async_session, org, mandate=mandate)

        async def _explode(*args, **kwargs):
            raise RuntimeError("document sequence unavailable")

        monkeypatch.setattr(documents, "issue_collection_voucher", _explode)

        outcome = await _deliver(
            async_session,
            _collection("sub_boom", payment_id="pay_boom", amount=RENTAL_GROSS_PAISE),
        )

        assert outcome["period"]["status"] == "recorded"
        periods = await async_session.scalar(
            select(func.count(RecurringChargePeriodModel.id)).where(
                RecurringChargePeriodModel.recurring_charge_id == charge.id
            )
        )
        assert periods == 1
        assert await _vouchers(async_session, org) == []


class TestWhatTheColumnMeans:
    async def test_a_collected_period_records_the_net(self, db_session, async_session):
        """``charged_paise`` is net everywhere else — the balance-collected path
        writes the charge's own price, which is net. Writing the provider's
        gross here put tax-inclusive rupees in the same column, and nothing
        reading it could tell the two apart: mandate revenue read 18% high
        against rentals paid from a balance.
        """
        org = await _org(async_session, "net")
        mandate = await _mandate(
            async_session,
            org,
            purpose=mandate_service.PURPOSE_NUMBER_RENTAL,
            sub="sub_net",
            price=NUMBER_RENTAL_PRICE_PAISE,
        )
        charge = await _charge(async_session, org, mandate=mandate)

        await _deliver(
            async_session,
            _collection("sub_net", payment_id="pay_net", amount=RENTAL_GROSS_PAISE),
        )

        period = await async_session.scalar(
            select(RecurringChargePeriodModel).where(
                RecurringChargePeriodModel.recurring_charge_id == charge.id
            )
        )
        assert period is not None
        assert period.charged_paise == NUMBER_RENTAL_PRICE_PAISE
        # And the gross is not lost: it is the voucher's total.
        voucher = (await _vouchers(async_session, org))[0]
        assert voucher.total_paise == RENTAL_GROSS_PAISE

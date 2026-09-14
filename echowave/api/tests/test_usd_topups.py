"""USD top-up packs (KAN-135): $12 / $60 / $240 through Razorpay dollar orders.

An account billed outside India sees the dollar ladder and nothing else; an
account in India never sees it. The order goes to Razorpay in cents, the
ledger is still credited in paise at fifty a credit, and the receipt voucher
is zero-rated under the LUT with the rupee value of the dollars received.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    PaymentModel,
)
from api.services.billing import billing_profile, payments, rates, tax, topup_packs

WEBHOOK_SECRET = "whsec_test_do_not_use_in_production"


class TestTheDollarLadder:
    def test_the_three_packs_and_what_they_grant(self):
        assert [(p.price_paise, p.credits) for p in topup_packs.USD_PACKS] == [
            (1_200, 2_000),
            (6_000, 10_500),
            (24_000, 44_000),
        ]
        assert all(p.currency == "USD" for p in topup_packs.USD_PACKS)
        assert not any(p.restricted for p in topup_packs.USD_PACKS)

    def test_the_bonus_reads_against_six_tenths_of_a_cent(self):
        # $0.006 a credit: $12 buys exactly 2,000; $60 would buy 10,000 and
        # grants 10,500; $240 would buy 40,000 and grants 44,000.
        assert topup_packs.pack_for("u12").bonus_credits == 0
        assert topup_packs.pack_for("u60").bonus_credits == 500
        assert topup_packs.pack_for("u240").bonus_credits == 4_000
        # A dollar pack carries no rupee bonus: what it grants is its credits.
        assert topup_packs.pack_for("u60").bonus_paise == 0

    def test_a_dollar_pack_is_still_worth_its_credits_in_paise(self):
        assert topup_packs.pack_for("u60").credit_paise == 10_500 * 50

    def test_the_rupee_ladder_is_untouched(self):
        assert [p.code for p in topup_packs.PACKS] == [
            "p500",
            "p999",
            "p4999",
            "p19999",
        ]
        assert all(p.currency == "INR" for p in topup_packs.PACKS)

    def test_the_dicts_say_which_currency(self):
        rows = topup_packs.packs_as_dicts(currency="USD")
        assert [r["code"] for r in rows] == ["u12", "u60", "u240"]
        assert rows[1] == {
            "code": "u60",
            "currency": "USD",
            "price_minor": 6_000,
            "price_paise": 6_000,
            "credits": 10_500,
            "bonus_credits": 500,
            "restricted": False,
        }
        assert all(r["currency"] == "INR" for r in topup_packs.packs_as_dicts())


async def _org(async_session, slug: str, *, country: str) -> OrganizationModel:
    """An account on the Business plan, whose top-up ceiling (100,000 credits)
    is above every pack; Free holds 2,000 and would refuse the $60 pack for
    a reason this file is not about."""
    from api.enums import MandateStatus
    from api.services.billing import subscription_plans
    from api.services.billing.mandates import PURPOSE_STARTER_PLAN

    await subscription_plans.ensure_seeded(async_session)
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    async_session.add(
        PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=PURPOSE_STARTER_PLAN,
            subscription_id=f"sub_{org.id}_business",
            plan_id="plan_business",
            plan_code="business",
            status=MandateStatus.ACTIVE.value,
            price_paise=0,
        )
    )
    await async_session.flush()
    await billing_profile.save_profile(
        async_session,
        organization_id=org.id,
        legal_name=f"{slug} Inc",
        country_code=country,
        state_code="29" if country == "IN" else None,
    )
    return org


@pytest.mark.asyncio
class TestWhoSeesWhichLadder:
    async def test_an_account_outside_india_is_billed_in_dollars(
        self, db_session, async_session
    ):
        org = await _org(async_session, "us", country="US")
        assert (
            await topup_packs.billing_currency(async_session, organization_id=org.id)
            == "USD"
        )
        rows = await topup_packs.packs_for(async_session, organization_id=org.id)
        assert [r["code"] for r in rows] == ["u12", "u60", "u240"]

    async def test_an_indian_account_never_sees_a_dollar_pack(
        self, db_session, async_session
    ):
        org = await _org(async_session, "in", country="IN")
        assert (
            await topup_packs.billing_currency(async_session, organization_id=org.id)
            == "INR"
        )
        rows = await topup_packs.packs_for(async_session, organization_id=org.id)
        assert [r["code"] for r in rows] == ["p999", "p4999", "p19999"]

    async def test_an_account_with_no_profile_is_indian(
        self, db_session, async_session
    ):
        org = OrganizationModel(provider_id="org-blank", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        assert (
            await topup_packs.billing_currency(async_session, organization_id=org.id)
            == "INR"
        )


class _Gateway:
    """Stands in for Razorpay's order endpoint and remembers what it was sent."""

    def __init__(self):
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, auth, json):
        self.sent.append(json)
        return SimpleNamespace(
            status_code=200, json=lambda: {"id": f"order_{len(self.sent)}"}, text=""
        )


@pytest.fixture
def gateway(monkeypatch):
    stub = _Gateway()
    monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(payments.httpx, "AsyncClient", lambda **kw: stub)
    monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "2099-03-31")
    from api.services.billing import documents

    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Labs Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "33AAAAA0000A1Z5")
    monkeypatch.setattr(documents, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(documents, "SUPPLIER_LUT_NUMBER", "AD330126000123X")

    async def fixed_rate(session, *, at):
        return rates.ResolvedFxRate(paise_per_usd=8_800, source="test")

    monkeypatch.setattr(rates, "resolve_usd_inr", fixed_rate)
    return stub


def _captured(*, order_id: str, payment_id: str, amount: int, currency: str) -> bytes:
    return json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": currency,
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()


def _sign(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
class TestADollarOrder:
    async def test_it_goes_to_razorpay_in_cents_with_no_tax(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "buyer", country="US")
        order = await payments.create_topup_order(
            async_session, organization_id=org.id, pack_code="u60", created_by=None
        )

        assert gateway.sent[0]["amount"] == 6_000
        assert gateway.sent[0]["currency"] == "USD"
        assert order.currency == "USD"
        assert order.amount_minor == 6_000
        assert order.gross_minor == 6_000
        assert order.tax_paise == 0
        assert order.pack_code == "u60"

    async def test_the_row_records_the_dollars_and_the_rate_applied(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "row", country="US")
        await payments.create_topup_order(
            async_session, organization_id=org.id, pack_code="u60", created_by=None
        )
        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        assert row.currency == "USD"
        assert row.amount_minor == 6_000
        assert row.fx_paise_per_usd == 8_800
        assert row.credits_granted == 10_500
        # The rupee value of the dollars: $60 at ₹88.00. This is what the
        # export voucher states and what the GST return reports.
        assert row.amount_paise == 528_000
        assert row.gross_paise == 528_000
        assert row.bonus_paise == 0

    async def test_an_indian_account_cannot_buy_a_dollar_pack(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "indian", country="IN")
        with pytest.raises(payments.PaymentError, match="outside India"):
            await payments.create_topup_order(
                async_session, organization_id=org.id, pack_code="u12", created_by=None
            )
        assert gateway.sent == []

    async def test_a_dollar_account_cannot_buy_a_rupee_pack(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "abroad", country="US")
        with pytest.raises(payments.PaymentError, match="dollar pack"):
            await payments.create_topup_order(
                async_session, organization_id=org.id, pack_code="p999", created_by=None
            )
        assert gateway.sent == []

    async def test_a_rupee_order_is_unchanged(self, db_session, async_session, gateway):
        org = await _org(async_session, "rupee", country="IN")
        order = await payments.create_topup_order(
            async_session, organization_id=org.id, pack_code="p999", created_by=None
        )
        assert gateway.sent[0]["currency"] == "INR"
        assert gateway.sent[0]["amount"] == order.gross_paise == 117_882
        assert order.currency == "INR"
        assert order.amount_minor == order.amount_paise == 99_900
        assert order.gross_minor == order.gross_paise
        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        assert row.currency == "INR"
        assert row.amount_minor == 99_900
        assert row.credits_granted == 2_000
        assert row.fx_paise_per_usd is None


@pytest.mark.asyncio
class TestTheDollarsLanding:
    async def _bought(self, async_session, org) -> PaymentModel:
        await payments.create_topup_order(
            async_session, organization_id=org.id, pack_code="u60", created_by=None
        )
        return await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )

    async def test_sixty_dollars_credits_ten_and_a_half_thousand_credits(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "landed", country="US")
        row = await self._bought(async_session, org)
        body = _captured(
            order_id=row.order_id, payment_id="pay_usd", amount=6_000, currency="USD"
        )
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["status"] == "credited"
        assert result["credited_paise"] == 10_500 * 50
        total = await async_session.scalar(
            select(func.sum(CreditLedgerModel.delta_paise)).where(
                CreditLedgerModel.organization_id == org.id
            )
        )
        assert int(total) == 525_000
        entry = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert "$60.00" in entry.note
        assert "u60" in entry.note

    async def test_the_voucher_is_a_zero_rated_export_in_rupees(
        self, db_session, async_session, gateway
    ):
        from api.db.models import TaxDocumentModel

        org = await _org(async_session, "voucher", country="US")
        row = await self._bought(async_session, org)
        body = _captured(
            order_id=row.order_id, payment_id="pay_v", amount=6_000, currency="USD"
        )
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["receipt_voucher_id"] is not None
        doc = await async_session.get(TaxDocumentModel, result["receipt_voucher_id"])
        assert doc.supply_type == "export"
        assert doc.igst_paise == doc.cgst_paise == doc.sgst_paise == 0
        assert doc.taxable_paise == doc.total_paise == 528_000
        assert "USD 60.00" in json.dumps(doc.line_items)

    async def test_a_capture_in_the_wrong_currency_is_refused(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "wrong", country="US")
        row = await self._bought(async_session, org)
        # 6,000 of the wrong unit: ₹60 against a $60 order.
        body = _captured(
            order_id=row.order_id, payment_id="pay_w", amount=6_000, currency="INR"
        )
        with pytest.raises(payments.PaymentError, match="currency"):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=_sign(body)
            )
        total = await async_session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == org.id
            )
        )
        assert int(total) == 0

    async def test_a_short_capture_credits_its_share(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "short", country="US")
        row = await self._bought(async_session, org)
        body = _captured(
            order_id=row.order_id, payment_id="pay_s", amount=3_000, currency="USD"
        )
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["credited_paise"] == 5_250 * 50

    async def test_the_history_reads_in_dollars(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "history", country="US")
        await self._bought(async_session, org)
        [entry] = await payments.list_payments(async_session, organization_id=org.id)
        assert entry["currency"] == "USD"
        assert entry["amount_minor"] == 6_000
        assert entry["gross_minor"] == 6_000
        assert entry["credits"] == 10_500
        assert entry["amount_paise"] == 528_000


@pytest.mark.asyncio
class TestARupeeCaptureStillNeedsRupees:
    async def test_a_rupee_order_paid_in_dollars_is_refused(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "inr-usd", country="IN")
        await payments.create_topup_order(
            async_session, organization_id=org.id, pack_code="p999", created_by=None
        )
        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        body = _captured(
            order_id=row.order_id, payment_id="pay_x", amount=117_882, currency="USD"
        )
        with pytest.raises(payments.PaymentError, match="currency"):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=_sign(body)
            )

    async def test_an_old_row_without_a_currency_is_rupees(
        self, db_session, async_session, gateway
    ):
        """Rows from before the column existed carry the default and are
        credited as they always were."""
        org = await _org(async_session, "old", country="IN")
        row = PaymentModel(
            organization_id=org.id,
            provider=payments.PROVIDER,
            order_id="order_old",
            amount_paise=50_000,
            status="created",
        )
        async_session.add(row)
        await async_session.flush()
        body = _captured(
            order_id="order_old", payment_id="pay_old", amount=50_000, currency="INR"
        )
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["credited_paise"] == 50_000

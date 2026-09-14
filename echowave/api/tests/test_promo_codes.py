"""Promo codes (KAN-134): staff make them, checkout honours them, once.

A percent or amount code reduces what the gateway charges and prints on the
voucher as a discount line; a bonus-credit code lands credits when the
money does. Each guard is worded for the customer and pinned here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    PaymentModel,
    PromoRedemptionModel,
    TaxDocumentModel,
)
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import billing_profile, payments, promo_codes, tax
from api.services.billing.mandates import PURPOSE_STARTER_PLAN

WEBHOOK_SECRET = "whsec_test_do_not_use_in_production"
NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


async def _org(session, slug: str, *, plan: str = "business", **fields):
    from api.services.billing import subscription_plans

    await subscription_plans.ensure_seeded(session)
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0, **fields)
    session.add(org)
    await session.flush()
    session.add(
        PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=PURPOSE_STARTER_PLAN,
            subscription_id=f"sub_{org.id}",
            plan_id=f"plan_{plan}",
            plan_code=plan,
            status=MandateStatus.ACTIVE.value,
            price_paise=0,
        )
    )
    await session.flush()
    await billing_profile.save_profile(
        session,
        organization_id=org.id,
        legal_name=f"{slug} Ltd",
        country_code="IN",
        state_code="29",
    )
    return org


def _pack(code="p4999"):
    from api.services.billing.topup_packs import pack_for

    pack = pack_for(code)
    return promo_codes.Purchase(
        kind="pack",
        code=pack.code,
        currency=pack.currency,
        amount_minor=pack.price_minor,
    )


class TestTheTermsStaffCanSet:
    def test_the_three_kinds_and_the_targets(self):
        assert promo_codes.KINDS == ("percent", "amount", "bonus_credits")
        assert promo_codes._check_target("plan:business") == "plan:business"
        assert promo_codes._check_target("PACK:p4999") == "pack:p4999"
        with pytest.raises(ValueError):
            promo_codes._check_target("everything")
        with pytest.raises(ValueError):
            promo_codes._check_terms(kind="percent", value=120, currency=None)
        assert (
            promo_codes._check_terms(kind="amount", value=500, currency=None) == "INR"
        )
        assert (
            promo_codes._check_terms(kind="bonus_credits", value=500, currency="INR")
            is None
        )

    def test_codes_are_upper_case_without_spaces(self):
        assert promo_codes.normalize("  launch 20 ") == "LAUNCH20"
        assert promo_codes.normalize("") is None


@pytest.mark.asyncio
class TestCreatingAndChanging:
    async def test_create_read_update_revoke(self, db_session, async_session):
        promo = await promo_codes.create(
            async_session,
            code="launch20",
            kind="percent",
            value=20,
            applies_to="any_pack",
            valid_until=NOW + timedelta(days=45),
            max_redemptions=100,
            first_payment_only=True,
            note="Launch offer",
        )
        assert promo.code == "LAUNCH20"
        with pytest.raises(ValueError, match="already exists"):
            await promo_codes.create(
                async_session, code="LAUNCH20", kind="percent", value=5
            )
        changed = await promo_codes.update(
            async_session, promo_id=promo.id, value=25, max_redemptions=None
        )
        assert changed.value == 25 and changed.max_redemptions is None
        with pytest.raises(ValueError, match="Cannot change"):
            await promo_codes.update(async_session, promo_id=promo.id, code="OTHER")
        revoked = await promo_codes.revoke(async_session, promo_id=promo.id)
        assert revoked.active is False
        [row] = await promo_codes.report(async_session)
        assert row["code"] == "LAUNCH20" and row["redemptions"] == 0


@pytest.mark.asyncio
class TestWhatACodeIsWorth:
    async def test_twenty_percent_off_the_five_thousand_pack(
        self, db_session, async_session
    ):
        org = await _org(async_session, "pct")
        await promo_codes.create(
            async_session,
            code="LAUNCH20",
            kind="percent",
            value=20,
            applies_to="any_pack",
        )
        applied = await promo_codes.validate(
            async_session, code="launch20", organization_id=org.id, purchase=_pack()
        )
        assert applied.discount_minor == 99_980  # 20% of ₹4,999
        assert applied.bonus_credits == 0

    async def test_an_amount_off_is_capped_at_the_price(
        self, db_session, async_session
    ):
        org = await _org(async_session, "amt")
        await promo_codes.create(
            async_session, code="OFF6000", kind="amount", value=600_000, currency="INR"
        )
        applied = await promo_codes.validate(
            async_session, code="OFF6000", organization_id=org.id, purchase=_pack()
        )
        assert applied.discount_minor == 499_900

    async def test_bonus_credits_take_nothing_off(self, db_session, async_session):
        org = await _org(async_session, "bonus")
        await promo_codes.create(
            async_session, code="PLUS500", kind="bonus_credits", value=500
        )
        applied = await promo_codes.validate(
            async_session, code="PLUS500", organization_id=org.id, purchase=_pack()
        )
        assert applied.discount_minor == 0 and applied.bonus_credits == 500


@pytest.mark.asyncio
class TestEveryRefusalInWords:
    async def _refused(self, session, org, code, purchase=None, **create):
        if create:
            await promo_codes.create(session, code=code, **create)
        with pytest.raises(promo_codes.PromoError) as excinfo:
            await promo_codes.validate(
                session,
                code=code,
                organization_id=org.id,
                purchase=purchase or _pack(),
            )
        return str(excinfo.value)

    async def test_unknown(self, db_session, async_session):
        org = await _org(async_session, "u")
        assert "not one we know" in await self._refused(async_session, org, "NOPE")

    async def test_revoked(self, db_session, async_session):
        org = await _org(async_session, "r")
        promo = await promo_codes.create(
            async_session, code="GONE", kind="percent", value=10
        )
        await promo_codes.revoke(async_session, promo_id=promo.id)
        assert "no longer active" in await self._refused(async_session, org, "GONE")

    async def test_outside_the_window(self, db_session, async_session):
        org = await _org(async_session, "w")
        assert "expired" in await self._refused(
            async_session,
            org,
            "OLD",
            kind="percent",
            value=10,
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
        assert "not valid yet" in await self._refused(
            async_session,
            org,
            "SOON",
            kind="percent",
            value=10,
            valid_from=datetime.now(UTC) + timedelta(days=1),
        )

    async def test_wrong_target(self, db_session, async_session):
        org = await _org(async_session, "t")
        assert "does not apply" in await self._refused(
            async_session,
            org,
            "BIZONLY",
            kind="percent",
            value=10,
            applies_to="plan:business",
        )
        assert "does not apply" in await self._refused(
            async_session,
            org,
            "OTHERPACK",
            kind="percent",
            value=10,
            applies_to="pack:p999",
        )

    async def test_a_discount_code_is_not_for_a_plan(self, db_session, async_session):
        org = await _org(async_session, "pl")
        plan = promo_codes.Purchase(
            kind="plan", code="business", currency="INR", amount_minor=299_900
        )
        assert "for top-ups" in await self._refused(
            async_session,
            org,
            "PCTPLAN",
            purchase=plan,
            kind="percent",
            value=10,
            applies_to="any_plan",
        )
        await promo_codes.create(
            async_session,
            code="PLANBONUS",
            kind="bonus_credits",
            value=300,
            applies_to="any_plan",
        )
        applied = await promo_codes.validate(
            async_session, code="PLANBONUS", organization_id=org.id, purchase=plan
        )
        assert applied.bonus_credits == 300

    async def test_wrong_currency(self, db_session, async_session):
        org = await _org(async_session, "c")
        assert "different currency" in await self._refused(
            async_session, org, "USD5", kind="amount", value=500, currency="USD"
        )

    async def test_first_payment_only(self, db_session, async_session):
        org = await _org(async_session, "fp")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=100_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=100_000,
            )
        )
        await async_session.flush()
        assert "first payment only" in await self._refused(
            async_session,
            org,
            "FIRST",
            kind="percent",
            value=10,
            first_payment_only=True,
        )

    async def test_internal_accounts(self, db_session, async_session):
        org = await _org(async_session, "int", internal_billing=True)
        assert "Internal accounts" in await self._refused(
            async_session, org, "STAFF", kind="percent", value=10
        )

    async def test_used_up_and_used_already(self, db_session, async_session):
        org = await _org(async_session, "lim")
        other = await _org(async_session, "lim2")
        promo = await promo_codes.create(
            async_session, code="ONCE", kind="percent", value=10, max_redemptions=1
        )
        async_session.add(
            PromoRedemptionModel(
                promo_code_id=promo.id, organization_id=other.id, discount_minor=1
            )
        )
        await async_session.flush()
        assert "used up" in await self._refused(async_session, org, "ONCE")
        promo2 = await promo_codes.create(
            async_session, code="MINE", kind="percent", value=10
        )
        async_session.add(
            PromoRedemptionModel(
                promo_code_id=promo2.id, organization_id=org.id, discount_minor=1
            )
        )
        await async_session.flush()
        assert "already used" in await self._refused(async_session, org, "MINE")


class _Gateway:
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
    from api.services.billing import documents

    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Labs Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "33AAAAA0000A1Z5")
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", "33")
    return stub


def _captured(*, order_id, payment_id, amount) -> bytes:
    return json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()


def _sign(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


async def _credits(session, org) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == org.id
        )
    )
    return int(total or 0) // 50


@pytest.mark.asyncio
class TestCheckoutWithACode:
    async def test_launch20_on_the_five_thousand_pack(
        self, db_session, async_session, gateway
    ):
        """The acceptance case, on a pack: ₹4,999 less 20% is ₹3,999.20 plus
        GST, the voucher shows the discount line, and the credits are the
        pack's own 10,500."""
        org = await _org(async_session, "launch")
        await promo_codes.create(
            async_session,
            code="LAUNCH20",
            kind="percent",
            value=20,
            applies_to="any_pack",
            max_redemptions=100,
            first_payment_only=True,
        )
        order = await payments.create_topup_order(
            async_session,
            organization_id=org.id,
            pack_code="p4999",
            promo_code="launch20",
            created_by=None,
        )
        assert order.promo_code == "LAUNCH20"
        assert order.discount_minor == 99_980
        assert order.amount_paise == 399_920
        assert order.gross_paise == 471_906  # ₹3,999.20 + 18% GST
        assert gateway.sent[0]["amount"] == 471_906
        assert order.credits == 10_500

        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        body = _captured(order_id=row.order_id, payment_id="pay_L", amount=471_906)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["status"] == "credited"
        assert result["promo"]["status"] == "recorded"
        assert await _credits(async_session, org) == 10_500

        doc = await async_session.get(TaxDocumentModel, result["receipt_voucher_id"])
        assert doc.taxable_paise == 399_920
        assert [item["amount_paise"] for item in doc.line_items] == [499_900, -99_980]
        assert "LAUNCH20" in doc.line_items[1]["description"]

        [redemption] = (
            await async_session.scalars(
                select(PromoRedemptionModel).where(
                    PromoRedemptionModel.organization_id == org.id
                )
            )
        ).all()
        assert redemption.payment_id == row.id and redemption.discount_minor == 99_980

        # A second use by the same account is refused at the order. The
        # first-payment rule speaks first, since the account has now paid;
        # the per-account count would refuse it too.
        with pytest.raises(payments.PaymentError, match="first payment only"):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                pack_code="p999",
                promo_code="LAUNCH20",
                created_by=None,
            )

    async def test_a_bonus_code_lands_with_the_money_once(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "plus")
        await promo_codes.create(
            async_session, code="PLUS500", kind="bonus_credits", value=500
        )
        order = await payments.create_topup_order(
            async_session,
            organization_id=org.id,
            pack_code="p999",
            promo_code="plus500",
            created_by=None,
        )
        assert order.discount_minor == 0 and order.bonus_credits == 500
        assert gateway.sent[0]["amount"] == 117_882  # the full ₹999 + GST
        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        body = _captured(order_id=row.order_id, payment_id="pay_P", amount=117_882)
        await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert await _credits(async_session, org) == 2_500
        bonus = await async_session.scalar(
            select(CreditLedgerModel).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.ref_type == "promo:PLUS500",
            )
        )
        assert bonus is not None and bonus.kind == CreditLedgerKind.TRIAL.value
        # Replayed: nothing twice.
        again = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert again["status"] == "already_credited"
        assert await _credits(async_session, org) == 2_500

    async def test_a_bad_code_stops_the_order_before_the_gateway(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "bad")
        with pytest.raises(payments.PaymentError, match="not one we know"):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                pack_code="p999",
                promo_code="NOPE",
                created_by=None,
            )
        assert gateway.sent == []

    async def test_revoking_stops_new_orders_but_honours_placed_ones(
        self, db_session, async_session, gateway
    ):
        org = await _org(async_session, "rev")
        promo = await promo_codes.create(
            async_session, code="EARLY", kind="percent", value=10
        )
        await payments.create_topup_order(
            async_session,
            organization_id=org.id,
            pack_code="p999",
            promo_code="EARLY",
            created_by=None,
        )
        await promo_codes.revoke(async_session, promo_id=promo.id)
        other = await _org(async_session, "rev2")
        with pytest.raises(payments.PaymentError, match="no longer active"):
            await payments.create_topup_order(
                async_session,
                organization_id=other.id,
                pack_code="p999",
                promo_code="EARLY",
                created_by=None,
            )
        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        body = _captured(
            order_id=row.order_id, payment_id="pay_R", amount=row.gross_paise
        )
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["promo"]["status"] == "recorded"


@pytest.mark.asyncio
class TestAPlanWithABonusCode:
    async def test_the_bonus_lands_on_the_first_collection_once(
        self, db_session, async_session
    ):
        org = await _org(async_session, "planbonus")
        await promo_codes.create(
            async_session,
            code="WELCOME300",
            kind="bonus_credits",
            value=300,
            applies_to="any_plan",
        )
        mandate = await async_session.scalar(
            select(PaymentMandateModel).where(
                PaymentMandateModel.organization_id == org.id
            )
        )
        mandate.promo_code = "WELCOME300"
        await async_session.flush()
        first = await promo_codes.settle_plan_collection(
            async_session, mandate=mandate, payment_ref="pay_1"
        )
        assert first == {
            "status": "recorded",
            "code": "WELCOME300",
            "bonus_credits": 300,
        }
        assert await _credits(async_session, org) == 300
        second = await promo_codes.settle_plan_collection(
            async_session, mandate=mandate, payment_ref="pay_2"
        )
        assert second["bonus_credits"] == 0
        assert await _credits(async_session, org) == 300

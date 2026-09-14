"""Top-up packs that never expire, and voice overage at one credit more.

Arrival tests for KAN-55. The acceptance case, verbatim: a Business account
with 0 plan credits and 2,000 top-up credits completes a call and is charged
at 11 credits per minute from the top-up pool.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    PaymentModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import payments, plans, subscription_plans, topup_packs
from api.services.billing.credits import credits_for_charge
from api.services.billing.mandates import PURPOSE_STARTER_PLAN

WEBHOOK_SECRET = "whsec_packs"


class TestThePacks:
    def test_the_four_packs_and_their_credits(self):
        by = {p.price_paise // 100: p.credits for p in topup_packs.PACKS}
        assert by == {500: 1_000, 999: 2_000, 4_999: 10_500, 19_999: 44_000}

    def test_the_ratio_rises_with_the_pack(self):
        ratios = [p.credits / (p.price_paise / 100) for p in topup_packs.PACKS]
        assert ratios == sorted(ratios)
        assert round(ratios[0], 2) == 2.0 and round(ratios[-1], 2) == 2.2

    def test_the_bonus_is_the_balance_above_face(self):
        assert topup_packs.pack_for("p500").bonus_paise == 0
        assert topup_packs.pack_for("p4999").bonus_paise == 25_100
        assert topup_packs.pack_for("p19999").bonus_credits == 4_002

    def test_an_unknown_pack_is_none(self):
        assert topup_packs.pack_for("p1000") is None
        assert topup_packs.pack_for("") is None

    def test_the_dicts_the_screen_reads(self):
        rows = topup_packs.packs_as_dicts()
        assert rows[1] == {
            "code": "p4999",
            "price_paise": 499_900,
            "credits": 10_500,
            "bonus_credits": 502,
            "restricted": False,
        }


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _entry(session, org, *, delta: int, kind: CreditLedgerKind, ref_id=None):
    balance = await payments.current_balance_paise(session, organization_id=org.id)
    row = CreditLedgerModel(
        organization_id=org.id,
        delta_paise=delta,
        kind=kind.value,
        ref_type=plans.REF_TYPE if kind == CreditLedgerKind.PLAN else None,
        ref_id=ref_id,
        balance_after_paise=balance + delta,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def _on_plan(session, org, code: str):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=PURPOSE_STARTER_PLAN,
        subscription_id=f"sub_{org.id}_{code}",
        plan_id=f"plan_{code}",
        plan_code=code,
        status=MandateStatus.ACTIVE.value,
        price_paise=0,
    )
    session.add(mandate)
    await session.flush()
    return mandate


@pytest.mark.asyncio
class TestTheTwoPools:
    async def test_no_grant_means_no_plan_credits(self, db_session, async_session):
        org = await _org(async_session, "nogrant")
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.plan_paise == 0
        assert pools.topup_paise == 100_000

    async def test_spending_comes_out_of_the_plan_first(
        self, db_session, async_session
    ):
        org = await _org(async_session, "planfirst")
        await _entry(
            async_session, org, delta=300_000, kind=CreditLedgerKind.PLAN, ref_id="g1"
        )
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        await _entry(async_session, org, delta=-120_000, kind=CreditLedgerKind.USAGE)
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.plan_paise == 180_000
        assert pools.topup_paise == 100_000

    async def test_an_expired_grant_leaves_only_the_top_up(
        self, db_session, async_session
    ):
        org = await _org(async_session, "expired")
        grant = await _entry(
            async_session, org, delta=300_000, kind=CreditLedgerKind.PLAN, ref_id="g2"
        )
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        await plans.expire_grant(async_session, grant=grant)
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.plan_paise == 0
        assert pools.topup_paise == 100_000

    async def test_a_plan_spent_through_leaves_the_top_up_intact(
        self, db_session, async_session
    ):
        org = await _org(async_session, "spent")
        await _entry(
            async_session, org, delta=300_000, kind=CreditLedgerKind.PLAN, ref_id="g3"
        )
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        await _entry(async_session, org, delta=-350_000, kind=CreditLedgerKind.USAGE)
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.plan_paise == 0
        assert pools.topup_paise == 50_000


@pytest.mark.asyncio
class TestTheNextRung:
    async def test_business_growth_scale_and_the_last_rung(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        nxt = subscription_plans.next_voice_plan_code
        assert await nxt(async_session, code="business") == "growth"
        assert await nxt(async_session, code="growth") == "scale"
        assert await nxt(async_session, code="scale") is None
        assert await nxt(async_session, code=subscription_plans.STARTER) == "growth"

    async def test_a_plan_off_the_ladder_has_no_overage_rung(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        assert (
            await subscription_plans.next_voice_plan_code(async_session, code="nope")
            is None
        )


def _sign(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _event(*, order_id: str, payment_id: str, amount: int) -> bytes:
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


@pytest.mark.asyncio
class TestAPackLandsWithItsBonus:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def _pack_order(self, session, org, *, order_id: str, code: str):
        pack = topup_packs.pack_for(code)
        row = PaymentModel(
            organization_id=org.id,
            provider=payments.PROVIDER,
            order_id=order_id,
            amount_paise=pack.price_paise,
            bonus_paise=pack.bonus_paise,
            pack_code=pack.code,
            gross_paise=pack.price_paise,
            status="created",
        )
        session.add(row)
        await session.flush()
        return row

    async def test_the_five_thousand_pack_credits_ten_thousand_five_hundred(
        self, db_session, async_session
    ):
        org = await _org(async_session, "pack5k")
        await self._pack_order(async_session, org, order_id="order_P5", code="p4999")
        body = _event(order_id="order_P5", payment_id="pay_P5", amount=499_900)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["credited_paise"] == 525_000
        entry = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert entry.kind == CreditLedgerKind.TOPUP.value
        assert entry.delta_paise == 525_000
        assert "502 bonus credits" in entry.note
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.topup_paise == 525_000

    async def test_a_short_capture_credits_the_bonus_in_proportion(
        self, db_session, async_session
    ):
        org = await _org(async_session, "packshort")
        await self._pack_order(async_session, org, order_id="order_PS", code="p19999")
        body = _event(order_id="order_PS", payment_id="pay_PS", amount=999_950)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        # Half the money, half the 44,000 credits: 22,000 credits.
        assert result["credited_paise"] == 1_100_000

    async def test_a_plain_amount_still_credits_at_face(
        self, db_session, async_session
    ):
        org = await _org(async_session, "plain")
        async_session.add(
            PaymentModel(
                organization_id=org.id,
                provider=payments.PROVIDER,
                order_id="order_F",
                amount_paise=30_000,
                gross_paise=30_000,
                status="created",
            )
        )
        await async_session.flush()
        body = _event(order_id="order_F", payment_id="pay_F", amount=30_000)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )
        assert result["credited_paise"] == 30_000


@pytest.mark.asyncio
class TestTheOrderRefusesWhatItShould:
    @pytest.fixture(autouse=True)
    def _keys(self, monkeypatch):
        monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test_key")
        monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")

    async def test_an_unknown_pack(self, db_session, async_session):
        org = await _org(async_session, "unknownpack")
        with pytest.raises(payments.PaymentError, match="no top-up pack"):
            await payments.create_topup_order(
                async_session, organization_id=org.id, pack_code="p7", created_by=None
            )

    async def test_neither_a_pack_nor_an_amount(self, db_session, async_session):
        org = await _org(async_session, "neither")
        with pytest.raises(payments.PaymentError, match="pack or an amount"):
            await payments.create_topup_order(
                async_session, organization_id=org.id, created_by=None
            )

    async def test_the_top_up_ceiling_names_where_to_raise_it(
        self, db_session, async_session
    ):
        """Free holds 2,000 top-up credits (section 7 of the spec). Holding
        1,500 and buying the ₹1,000 pack (2,000) would be 3,500; refused,
        and told the rung that holds more: Everyday, at 20,000."""
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "ceiling")
        await _entry(async_session, org, delta=75_000, kind=CreditLedgerKind.TOPUP)
        with pytest.raises(payments.PaymentError) as excinfo:
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                pack_code="p999",
                created_by=None,
            )
        assert "2,000 top-up credits" in str(excinfo.value)
        assert "upgrade to Everyday" in str(excinfo.value)
        count = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.organization_id == org.id)
        )
        assert count is None


@pytest.mark.asyncio
class TestOverageAtTheNextTiersRate:
    """The acceptance case. A Business account, plan credits gone, 2,000
    top-up credits, one minute on the Everyday bundle: 14 credits (13 plus
    one), from the top-up pool. Scale, the last rung, stays at 11."""

    async def _business_with_a_bundle(self, session, monkeypatch, slug: str):
        from api.services.configuration import agent_options, bundles

        await subscription_plans.ensure_seeded(session)
        await bundles.ensure_seeded(session)
        from api.db.models import ManagedBundleModel

        row = await session.scalar(
            select(ManagedBundleModel).where(ManagedBundleModel.slug == "everyday")
        )
        row.list_paise_per_minute = 650
        row.plan_rates = {"business": 650, "growth": 600, "scale": 550}
        await session.flush()

        async def _everyday(*, organization_id):
            return {"bundle": "everyday"}

        monkeypatch.setattr(agent_options, "selected_bundle", _everyday)

        user = UserModel(provider_id=f"user-{slug}")
        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        session.add_all([user, org])
        await session.flush()
        await _on_plan(session, org, "business")
        workflow = WorkflowModel(
            name=f"wf-{slug}",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        session.add(workflow)
        await session.flush()
        return org, workflow

    async def _one_minute_run(self, session, workflow):
        run = WorkflowRunModel(
            name="run",
            workflow_id=workflow.id,
            mode="plivo",
            usage_info={},
            cost_info={},
            initial_context={},
            gathered_context={},
            is_completed=True,
            billable_seconds=60,
            created_at=datetime.now(UTC),
            answered_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        return run

    async def test_a_business_account_out_of_plan_credits_pays_one_more(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.billing.costing import cost_workflow_run

        org, workflow = await self._business_with_a_bundle(
            async_session, monkeypatch, "overage"
        )
        # 0 plan credits: a grant fully spent. 2,000 top-up credits.
        await _entry(
            async_session, org, delta=60_000, kind=CreditLedgerKind.PLAN, ref_id="gA"
        )
        await _entry(async_session, org, delta=-60_000, kind=CreditLedgerKind.USAGE)
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)

        run = await self._one_minute_run(async_session, workflow)
        cost = await cost_workflow_run(async_session, run.id)

        assert credits_for_charge(cost.total_charged_paise) == 14
        assert run.overage_applied is True
        pools = await plans.pool_balances(async_session, organization_id=org.id)
        assert pools.plan_paise == 0
        assert pools.topup_paise == 100_000 - 700

    async def test_inside_the_plan_the_same_call_is_thirteen(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.billing.costing import cost_workflow_run

        org, workflow = await self._business_with_a_bundle(
            async_session, monkeypatch, "inplan"
        )
        await _entry(
            async_session, org, delta=300_000, kind=CreditLedgerKind.PLAN, ref_id="gB"
        )
        run = await self._one_minute_run(async_session, workflow)
        cost = await cost_workflow_run(async_session, run.id)
        assert credits_for_charge(cost.total_charged_paise) == 13
        assert not run.overage_applied

    async def test_scale_pays_its_own_rate_on_overage(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.billing.costing import cost_workflow_run

        org, workflow = await self._business_with_a_bundle(
            async_session, monkeypatch, "scaleover"
        )
        mandate = await async_session.scalar(
            select(PaymentMandateModel).where(
                PaymentMandateModel.organization_id == org.id
            )
        )
        mandate.plan_code = "scale"
        await async_session.flush()
        await _entry(async_session, org, delta=100_000, kind=CreditLedgerKind.TOPUP)
        run = await self._one_minute_run(async_session, workflow)
        cost = await cost_workflow_run(async_session, run.id)
        assert credits_for_charge(cost.total_charged_paise) == 11
        assert not run.overage_applied

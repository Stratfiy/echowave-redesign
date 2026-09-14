"""Friend referral (KAN-133): 200 credits each, on the friend's first payment.

Every account has a code and a link. A signup through the link is attributed
at provisioning, as it already was for partners. Nothing is paid on signup;
when the referred account's first payment lands, both sides get 200 credits
as trial rows, once per pair. A partner's referral pays commission instead,
never both. Internal accounts earn nothing. The referrer's earnings are
capped a month, and staff can raise the cap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from api.db.models import CreditLedgerModel, OrganizationModel, PartnerCommissionModel
from api.enums import CreditLedgerKind
from api.services.billing import referral_rewards


async def _org(session, slug: str, **fields) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0, **fields)
    session.add(org)
    await session.flush()
    return org


async def _referred(session, slug: str, referrer: OrganizationModel, **fields):
    return await _org(
        session,
        slug,
        referred_by_organization_id=referrer.id,
        referred_at=datetime.now(UTC),
        **fields,
    )


async def _credits(session, org) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == org.id
        )
    )
    return int(total or 0) // 50


async def _rows(session, org) -> list[CreditLedgerModel]:
    return list(
        (
            await session.scalars(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.ref_type == referral_rewards.REF_TYPE,
                )
            )
        ).all()
    )


class TestTheTerms:
    def test_two_hundred_each_and_twenty_a_month(self):
        assert referral_rewards.REWARD_CREDITS == 200
        assert referral_rewards.DEFAULT_MONTHLY_CAP == 20
        assert referral_rewards.REF_TYPE == "referral"


@pytest.mark.asyncio
class TestEveryAccountHasACode:
    async def test_a_code_is_minted_on_first_read_and_then_kept(
        self, db_session, async_session
    ):
        org = await _org(async_session, "codes")
        first = await referral_rewards.state(async_session, organization_id=org.id)
        assert len(first["code"]) == 8
        assert first["link"].endswith(f"/auth/signup?ref={first['code']}")
        second = await referral_rewards.state(async_session, organization_id=org.id)
        assert second["code"] == first["code"]

    async def test_the_state_lists_who_came_and_whether_they_paid(
        self, db_session, async_session
    ):
        me = await _org(async_session, "me", billing_name="Me Ltd")
        friend = await _referred(async_session, "friend", me, billing_name="Friend Co")
        await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_1"
        )
        waiting = await _referred(async_session, "waiting", me)
        state = await referral_rewards.state(async_session, organization_id=me.id)
        by_name = {row["name"]: row for row in state["accounts"]}
        assert by_name["Friend Co"]["status"] == "paid"
        assert by_name[f"Account {waiting.id}"]["status"] == "signed_up"
        assert state["earned_credits"] == 200
        assert state["this_month"] == 1
        assert state["monthly_cap"] == 20


@pytest.mark.asyncio
class TestTheFirstPaymentPaysBothSides:
    async def test_two_hundred_each_as_trial_rows(self, db_session, async_session):
        me = await _org(async_session, "referrer")
        friend = await _referred(async_session, "paid", me)
        result = await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_A"
        )
        assert result == {"referrer": 200, "referred": 200}
        assert await _credits(async_session, me) == 200
        assert await _credits(async_session, friend) == 200
        for org in (me, friend):
            [row] = await _rows(async_session, org)
            assert row.kind == CreditLedgerKind.TRIAL.value
            assert row.ref_id == str(friend.id)

    async def test_a_replayed_payment_grants_nothing_twice(
        self, db_session, async_session
    ):
        me = await _org(async_session, "once")
        friend = await _referred(async_session, "once-friend", me)
        await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_B"
        )
        again = await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_B"
        )
        assert again == {"referrer": 0, "referred": 0}
        later = await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_C"
        )
        assert later == {"referrer": 0, "referred": 0}
        assert await _credits(async_session, me) == 200
        assert await _credits(async_session, friend) == 200

    async def test_an_unreferred_account_pays_nobody(self, db_session, async_session):
        org = await _org(async_session, "alone")
        result = await referral_rewards.settle_first_payment(
            async_session, organization_id=org.id, payment_ref="pay_D"
        )
        assert result == {"referrer": 0, "referred": 0}
        assert await _credits(async_session, org) == 0


@pytest.mark.asyncio
class TestWhoEarnsNothing:
    async def test_a_partners_referral_is_commission_not_credits(
        self, db_session, async_session
    ):
        partner = await _org(async_session, "partner")
        async_session.add(
            PartnerCommissionModel(
                organization_id=partner.id,
                commission_bps=1_000,
                basis="net_revenue",
                effective_from=datetime.now(UTC),
            )
        )
        await async_session.flush()
        client = await _referred(async_session, "client", partner)
        result = await referral_rewards.settle_first_payment(
            async_session, organization_id=client.id, payment_ref="pay_E"
        )
        assert result == {"referrer": 0, "referred": 0}
        assert await _credits(async_session, partner) == 0
        assert await _credits(async_session, client) == 0

    async def test_an_internal_account_earns_nothing_on_either_side(
        self, db_session, async_session
    ):
        staff = await _org(async_session, "staff", internal_billing=True)
        friend = await _referred(async_session, "of-staff", staff)
        assert await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_F"
        ) == {"referrer": 0, "referred": 200}

        me = await _org(async_session, "me2")
        tester = await _referred(async_session, "tester", me, internal_billing=True)
        assert await referral_rewards.settle_first_payment(
            async_session, organization_id=tester.id, payment_ref="pay_G"
        ) == {"referrer": 0, "referred": 0}

    async def test_the_monthly_cap_stops_the_referrer_not_the_friend(
        self, db_session, async_session
    ):
        me = await _org(async_session, "capped", referral_monthly_cap=2)
        for i in range(3):
            friend = await _referred(async_session, f"cap-{i}", me)
            result = await referral_rewards.settle_first_payment(
                async_session, organization_id=friend.id, payment_ref=f"pay_H{i}"
            )
            assert result["referred"] == 200
            assert result["referrer"] == (200 if i < 2 else 0)
        assert await _credits(async_session, me) == 400

    async def test_staff_can_raise_the_cap(self, db_session, async_session):
        me = await _org(async_session, "raised")
        await referral_rewards.set_monthly_cap(
            async_session, organization_id=me.id, cap=50
        )
        state = await referral_rewards.state(async_session, organization_id=me.id)
        assert state["monthly_cap"] == 50
        await referral_rewards.set_monthly_cap(
            async_session, organization_id=me.id, cap=None
        )
        state = await referral_rewards.state(async_session, organization_id=me.id)
        assert state["monthly_cap"] == 20

    async def test_switched_off_pays_nothing(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(referral_rewards, "ENABLED", False)
        me = await _org(async_session, "off")
        friend = await _referred(async_session, "off-friend", me)
        assert await referral_rewards.settle_first_payment(
            async_session, organization_id=friend.id, payment_ref="pay_I"
        ) == {"referrer": 0, "referred": 0}

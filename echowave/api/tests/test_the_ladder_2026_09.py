"""The 21 September 2026 ladder, behind a flag: Free, Go, Personal, Business, Pro, Scale.

Decided by the founder on 21 Sept 2026 (see the vault's Credits Plan). Nothing
here goes live until ``PLAN_LADDER_2026_09_ENABLED`` is on; with it off, the
14 Sept ladder is untouched and every test in ``test_the_plan_ladder.py``
still holds.

* six plans on sale in this order: free, go, personal, business_v2, pro,
  scale_v2 — new codes, so a deployed database keeps every existing row and
  every existing account's entitlement; the old rungs are withdrawn from
  sale the way Starter was, not deleted;
* no plan includes a phone number: numbers are an add-on at the rental price;
* the free welcome grant is 100 credits, earned in steps, and the unspent
  part expires 30 days after it was granted — top-ups never do;
* the separate "builder message past the allowance" fee is retired: a
  builder call costs the model rate from the same allowance and nothing on
  top;
* global rows (INR, with a USD reference) are seeded but not on sale until
  billing-region gating exists;
* every figure is a replay hypothesis, and this file is where a changed one
  gets changed first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api import constants
from api.db.models import CreditLedgerModel, OrganizationModel
from api.enums import CreditLedgerKind
from api.services.billing import (
    events,
    onboarding_credits,
    plan_limits,
    subscription_plans,
)

pytestmark = pytest.mark.asyncio

NEW_ON_SALE = ["free", "go", "personal", "business_v2", "pro", "scale_v2"]
OLD_ON_SALE = ["free", "everyday", "business", "growth", "scale"]


@pytest.fixture
def ladder_on(monkeypatch):
    monkeypatch.setattr(constants, "PLAN_LADDER_2026_09_ENABLED", True)


@pytest.fixture
def ladder_off(monkeypatch):
    monkeypatch.setattr(constants, "PLAN_LADDER_2026_09_ENABLED", False)


async def _org(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _balance(session, org) -> int:
    from api.services.billing.payments import current_balance_paise

    return await current_balance_paise(session, organization_id=org.id)


async def _welcome_row(session, org, *, paise: int, age_days: int):
    balance = await _balance(session, org)
    row = CreditLedgerModel(
        organization_id=org.id,
        delta_paise=paise,
        kind=CreditLedgerKind.TRIAL.value,
        ref_type="onboarding:verify_email",
        ref_id=str(org.id),
        balance_after_paise=balance + paise,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    session.add(row)
    await session.flush()
    return row


class TestWithTheFlagOff:
    async def test_the_old_ladder_is_exactly_what_it_was(
        self, ladder_off, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        on_sale = [p.code for p in await subscription_plans.list_plans(async_session)]
        assert on_sale == OLD_ON_SALE
        assert await subscription_plans.get_plan(async_session, code="go") is None

    def test_the_old_fees_and_grant_stand(self, ladder_off):
        assert events.credits_for(events.BUILDER_MESSAGE) == 5
        assert onboarding_credits.free_credits() == 1_000
        assert sum(s.credits for s in onboarding_credits.steps()) == 1_000
        assert plan_limits.ladder() == tuple(OLD_ON_SALE)


class TestTheNewLadderLands:
    async def test_six_plans_with_their_decided_figures(
        self, ladder_on, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        by_code = {
            p.code: p
            for p in await subscription_plans.list_plans(
                async_session, enabled_only=False
            )
        }
        expected = {
            # code: (label, price rupees, credits a month, voice)
            "go": ("Go", 499, 300, False),
            "personal": ("Personal", 999, 700, False),
            "business_v2": ("Business", 2_999, 1_800, True),
            "pro": ("Pro", 9_999, 12_000, True),
            "scale_v2": ("Scale", 19_999, 30_000, True),
        }
        for code, (label, rupees, grant, voice) in expected.items():
            plan = by_code[code]
            assert plan.label == label, code
            assert plan.price_paise == rupees * 100, code
            assert plan.credits == grant, code
            assert plan.included_numbers == 0, code
            assert plan.voice_allowed is voice, code
            assert plan.annual_price_paise == plan.price_paise * 10, code
            assert plan.purchasable is True, code

    async def test_the_old_rungs_are_withdrawn_not_deleted(
        self, ladder_on, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        on_sale = [p.code for p in await subscription_plans.list_plans(async_session)]
        assert on_sale == NEW_ON_SALE
        for code in ("everyday", "business", "growth", "scale", "starter"):
            old = await subscription_plans.get_plan(async_session, code=code)
            assert old is not None, code
            assert old.enabled is False, code

    async def test_global_rows_are_seeded_but_not_on_sale_yet(
        self, ladder_on, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        expected = {
            # code: (price rupees, credits, usd reference cents)
            "personal_global": (1_440, 1_200, 1_500),
            "business_global": (6_600, 4_500, 6_900),
            "pro_global": (22_000, 20_000, 22_900),
            "scale_global": (38_300, 40_000, 39_900),
        }
        for code, (rupees, grant, usd) in expected.items():
            plan = await subscription_plans.get_plan(async_session, code=code)
            assert plan is not None, code
            assert plan.price_paise == rupees * 100, code
            assert plan.credits == grant, code
            assert plan.price_usd_cents == usd, code
            assert plan.included_numbers == 0, code
            assert plan.enabled is False, code

    async def test_seeding_twice_changes_nothing(
        self, ladder_on, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        first = [
            (p.code, p.price_paise, p.credits, p.enabled)
            for p in await subscription_plans.list_plans(
                async_session, enabled_only=False
            )
        ]
        await subscription_plans.ensure_seeded(async_session)
        again = [
            (p.code, p.price_paise, p.credits, p.enabled)
            for p in await subscription_plans.list_plans(
                async_session, enabled_only=False
            )
        ]
        assert again == first

    async def test_the_caps_climb_the_new_ladder(
        self, ladder_on, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        assert plan_limits.ladder() == tuple(NEW_ON_SALE)
        table = await plan_limits.limits_for_plans(
            async_session, plan_codes=NEW_ON_SALE
        )
        expected = {
            # code: (members, bots, concurrent calls, routines, min interval minutes)
            "free": (1, 1, 0, 2, 24 * 60),  # existing row; not re-seeded
            "go": (1, 3, 0, 3, 60),
            "personal": (1, 10, 0, 10, 15),
            "business_v2": (5, 30, 1, 50, 15),
            "pro": (15, 100, 5, None, 5),
            "scale_v2": (30, 250, 10, None, 5),
        }
        for code, (members, bots, calls, routines, interval) in expected.items():
            caps = table[code]
            assert caps["team_members"] == members, code
            assert caps["bots"] == bots, code
            assert caps["concurrent_calls"] == calls, code
            assert caps["routines"] == routines, code
            assert caps["routine_min_interval_minutes"] == interval, code
        # The next rung with more calls above Business is Pro, not Growth.
        more = await plan_limits.resolve(
            async_session, plan_code="business_v2", key="concurrent_calls"
        )
        assert more.raise_to == "pro"


class TestTheBuilderFeeIsRetired:
    def test_a_builder_message_costs_nothing_on_top(self, ladder_on):
        assert events.credits_for(events.BUILDER_MESSAGE) == 0
        assert events.paise_for(events.BUILDER_MESSAGE) == 0
        assert events.paise_for(events.BUILDER_MESSAGE, 7) == 0


class TestTheWelcomeGrant:
    def test_it_is_a_hundred_credits_in_steps(self, ladder_on):
        assert onboarding_credits.free_credits() == 100
        steps = onboarding_credits.steps()
        assert sum(s.credits for s in steps) == 100
        assert [s.key for s in steps] == [s.key for s in onboarding_credits.STEPS]

    async def test_the_unspent_part_expires_after_thirty_days(
        self, ladder_on, db_session, async_session
    ):
        org = await _org(async_session, "welcome-old")
        await _welcome_row(async_session, org, paise=5_000, age_days=31)
        counters = await onboarding_credits.expire_welcome_grants(async_session)
        assert counters["expired"] == 1
        assert counters["paise"] == 5_000
        assert await _balance(async_session, org) == 0
        # Once. The second sweep finds the expiry already written.
        again = await onboarding_credits.expire_welcome_grants(async_session)
        assert again["expired"] == 0
        assert await _balance(async_session, org) == 0

    async def test_a_grant_inside_its_thirty_days_is_left_alone(
        self, ladder_on, db_session, async_session
    ):
        org = await _org(async_session, "welcome-new")
        await _welcome_row(async_session, org, paise=5_000, age_days=10)
        counters = await onboarding_credits.expire_welcome_grants(async_session)
        assert counters["expired"] == 0
        assert await _balance(async_session, org) == 5_000

    async def test_a_top_up_is_never_expired_with_it(
        self, ladder_on, db_session, async_session
    ):
        org = await _org(async_session, "welcome-topup")
        await _welcome_row(async_session, org, paise=5_000, age_days=40)
        balance = await _balance(async_session, org)
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=25_000,
                kind=CreditLedgerKind.TOPUP.value,
                ref_type="razorpay_payment",
                ref_id="pay_welcome_topup",
                balance_after_paise=balance + 25_000,
            )
        )
        await async_session.flush()
        counters = await onboarding_credits.expire_welcome_grants(async_session)
        assert counters["paise"] == 5_000
        assert await _balance(async_session, org) == 25_000

    async def test_nothing_expires_with_the_flag_off(
        self, ladder_off, db_session, async_session
    ):
        org = await _org(async_session, "welcome-flag-off")
        await _welcome_row(async_session, org, paise=5_000, age_days=60)
        counters = await onboarding_credits.expire_welcome_grants(async_session)
        assert counters["expired"] == 0
        assert await _balance(async_session, org) == 5_000

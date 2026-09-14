"""The plan ladder: Free, Everyday, Business, Growth, Scale, and the rules around it.

Arrival tests for KAN-53. Decided 14 Sept 2026 (KAN-47):

* five plans at the prices and credits on the pricing page, seeded from one
  place, read by the app's plan picker and the public pricing endpoint;
* Everyday is text only: an Everyday account cannot attach a phone number or
  start a campaign, and the refusal names the rung that can;
* plan credits expire at the end of the cycle they were granted for; top-ups
  are a separate pool that never expires;
* the credits-per-rupee ratio rises with the plan: 2.0 at Everyday and
  Business, 2.5 at Growth, 3.0 at Scale;
* annual is ten months for twelve, twelve months of credits granted at once
  and living a year;
* every cap is a row, never a constant, and every cap knows where "raise this"
  goes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import CreditLedgerModel, OrganizationModel, PaymentMandateModel
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import credits, plan_limits, plans, subscription_plans
from api.services.billing.mandates import PURPOSE_STARTER_PLAN
from api.services.billing.subscription_plans import PlanError, VoiceNotIncluded

pytestmark = pytest.mark.asyncio


async def _org(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _mandate(
    session, org, *, plan_code: str, status=MandateStatus.ACTIVE.value, **extra
):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=PURPOSE_STARTER_PLAN,
        subscription_id=f"sub_{org.id}_{plan_code}",
        plan_id=f"plan_{plan_code}",
        plan_code=plan_code,
        status=status,
        price_paise=0,
        **extra,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _grant(session, org, *, paise: int, ref: str, created_at=None):
    from api.services.billing.payments import current_balance_paise

    balance = await current_balance_paise(session, organization_id=org.id)
    row = CreditLedgerModel(
        organization_id=org.id,
        delta_paise=paise,
        kind=CreditLedgerKind.PLAN.value,
        ref_type=plans.REF_TYPE,
        ref_id=ref,
        balance_after_paise=balance + paise,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def _balance(session, org) -> int:
    from api.services.billing.payments import current_balance_paise

    return await current_balance_paise(session, organization_id=org.id)


class TestTheLadderIsSeeded:
    async def test_the_five_plans_land_with_their_decided_figures(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        by_code = {
            p.code: p
            for p in await subscription_plans.list_plans(
                async_session, enabled_only=False
            )
        }

        expected = {
            # code: (price rupees, credits a month, numbers, voice)
            "free": (0, 0, 0, False),
            "everyday": (999, 2_000, 0, False),
            "business": (2_999, 6_000, 1, True),
            "growth": (9_999, 25_000, 2, True),
            "scale": (19_999, 60_000, 4, True),
        }
        for code, (rupees, grant, numbers, voice) in expected.items():
            plan = by_code[code]
            assert plan.price_paise == rupees * 100, code
            assert plan.credits == grant, code
            assert plan.included_numbers == numbers, code
            assert plan.voice_allowed is voice, code

        # Everyday is the one dollar plan; the voice plans are India-only.
        assert by_code["everyday"].price_usd_cents == 1_000
        assert by_code["business"].price_usd_cents is None
        # Annual is ten months for twelve, on every paid plan.
        for code in ("everyday", "business", "growth", "scale"):
            assert by_code[code].annual_price_paise == by_code[code].price_paise * 10
        # Free is not bought, and neither is Campus Builder.
        assert by_code["free"].purchasable is False
        assert by_code["campus"].purchasable is False
        assert by_code["campus"].enabled is False

    async def test_the_old_starter_plan_is_withdrawn_from_sale_not_deleted(
        self, db_session, async_session
    ):
        """Accounts on it keep collecting and granting what they bought."""
        await subscription_plans.ensure_seeded(async_session)
        starter = await subscription_plans.get_plan(async_session, code="starter")
        assert starter is not None
        assert starter.enabled is False
        on_sale = [p.code for p in await subscription_plans.list_plans(async_session)]
        assert "starter" not in on_sale
        assert on_sale == ["free", "everyday", "business", "growth", "scale"]

    async def test_seeding_twice_changes_nothing(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        await subscription_plans.set_plan_platform_rate(
            async_session, code="growth", platform_rate_mpaise=0
        )
        edited = await subscription_plans.save(
            async_session,
            **_as_kwargs(
                await subscription_plans.get_plan(async_session, code="growth"),
                price_paise=899_900,
            ),
        )
        await subscription_plans.ensure_seeded(async_session)
        again = await subscription_plans.get_plan(async_session, code="growth")
        assert again.price_paise == edited.price_paise == 899_900


def _as_kwargs(plan, **overrides):
    fields = dict(
        code=plan.code,
        label=plan.label,
        blurb=plan.blurb,
        price_paise=plan.price_paise,
        balance_paise=plan.balance_paise,
        included_numbers=plan.included_numbers,
        extra_number_price_paise=plan.extra_number_price_paise,
        knowledge_base_bytes=plan.knowledge_base_bytes,
        knowledge_base_max_file_bytes=plan.knowledge_base_max_file_bytes,
        platform_rate_mpaise=plan.platform_rate_mpaise,
        razorpay_plan_id=plan.razorpay_plan_id,
        razorpay_plan_id_export=plan.razorpay_plan_id_export,
        enabled=plan.enabled,
        sort_order=plan.sort_order,
        voice_allowed=plan.voice_allowed,
        purchasable=plan.purchasable,
        price_usd_cents=plan.price_usd_cents,
        annual_price_paise=plan.annual_price_paise,
    )
    fields.update(overrides)
    return fields


class TestTheLossGuardReadsCreditsAsSellValue:
    async def test_scale_saves_because_a_credit_is_marked_up_cost(
        self, db_session, async_session
    ):
        """60,000 credits is Rs 30,000 of sell value for Rs 19,999. At the
        62% provider share that is Rs 18,600 of cost plus Rs 1,000 of rent,
        inside the price. The old guard read balance as cost and would have
        refused it."""
        plan = await subscription_plans.save(
            async_session,
            code="scale-check",
            label="Scale check",
            price_paise=1_999_900,
            balance_paise=credits.paise_for_credits(60_000),
            included_numbers=4,
            razorpay_plan_id="plan_scale_check",
        )
        assert plan.cost_of_balance_paise + 4 * 25_000 < plan.price_paise
        assert (
            plan.cost_of_balance_paise
            == 3_000_000 * subscription_plans.BALANCE_COST_BPS // 10_000
        )

    async def test_a_plan_that_gives_more_than_it_takes_is_still_refused(
        self, db_session, async_session
    ):
        with pytest.raises(PlanError) as caught:
            await subscription_plans.save(
                async_session,
                code="gift",
                label="Gift",
                price_paise=100_000,
                balance_paise=200_000,
                included_numbers=0,
                razorpay_plan_id="plan_gift",
            )
        assert "cost" in str(caught.value)

    async def test_a_free_plan_may_cost_nothing_only_if_it_is_not_sold(
        self, db_session, async_session
    ):
        with pytest.raises(PlanError):
            await subscription_plans.save(
                async_session,
                code="zero",
                label="Zero",
                price_paise=0,
                balance_paise=0,
                included_numbers=0,
            )
        plan = await subscription_plans.save(
            async_session,
            code="zero",
            label="Zero",
            price_paise=0,
            balance_paise=0,
            included_numbers=0,
            purchasable=False,
        )
        assert plan.price_paise == 0

    async def test_an_annual_price_is_at_most_twelve_months(
        self, db_session, async_session
    ):
        with pytest.raises(PlanError):
            await subscription_plans.save(
                async_session,
                code="dear",
                label="Dear",
                price_paise=100_000,
                balance_paise=0,
                included_numbers=0,
                annual_price_paise=1_300_000,
            )


class TestWhichPlanAnAccountIsOn:
    async def test_no_mandate_is_free(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "nobody")
        plan = await subscription_plans.plan_for_organization(
            async_session, organization_id=org.id
        )
        assert plan.code == "free"
        assert plan.voice_allowed is False

    async def test_an_authorised_mandate_names_its_plan(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "growth")
        await _mandate(async_session, org, plan_code="growth")
        plan = await subscription_plans.plan_for_organization(
            async_session, organization_id=org.id
        )
        assert plan.code == "growth"

    async def test_a_mandate_awaiting_authorisation_is_still_free(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "pending")
        await _mandate(
            async_session, org, plan_code="business", status=MandateStatus.CREATED.value
        )
        plan = await subscription_plans.plan_for_organization(
            async_session, organization_id=org.id
        )
        assert plan.code == "free"


class TestEverydayIsTextOnly:
    async def test_everyday_cannot_use_the_phone_and_is_told_where_it_can(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "everyday")
        await _mandate(async_session, org, plan_code="everyday")
        with pytest.raises(VoiceNotIncluded) as caught:
            await subscription_plans.assert_voice_allowed(
                async_session, organization_id=org.id
            )
        assert caught.value.plan_code == "everyday"
        assert caught.value.upgrade_to == "business"
        assert "Business" in str(caught.value)

    async def test_business_can(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "business")
        await _mandate(async_session, org, plan_code="business")
        await subscription_plans.assert_voice_allowed(
            async_session, organization_id=org.id
        )

    async def test_an_account_renting_a_number_on_its_own_mandate_keeps_it(
        self, db_session, async_session
    ):
        """The path that predates the ladder: a rental mandate is a paid
        number, and the ladder must not take it away."""
        from api.services.billing.mandates import PURPOSE_NUMBER_RENTAL

        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "rental")
        async_session.add(
            PaymentMandateModel(
                organization_id=org.id,
                provider="razorpay",
                purpose=PURPOSE_NUMBER_RENTAL,
                subscription_id="sub_rental",
                status=MandateStatus.ACTIVE.value,
                price_paise=55_900,
            )
        )
        await async_session.flush()
        await subscription_plans.assert_voice_allowed(
            async_session, organization_id=org.id
        )


class TestPlanCreditsExpireAtCycleEnd:
    async def test_a_renewal_retires_the_previous_cycle(
        self, db_session, async_session
    ):
        """Rs 3,000 unspent plus Rs 3,000 new is Rs 3,000, not Rs 6,000: plan
        credits are for the month they were granted for."""
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "expiry")
        mandate = await _mandate(async_session, org, plan_code="business")
        await _grant(async_session, org, paise=300_000, ref="pay_1")

        result = await plans.grant_plan_cycle(
            async_session,
            mandate=mandate,
            event={"payload": {"payment": {"entity": {"id": "pay_2"}}}},
        )
        assert result["status"] == "granted"
        assert await _balance(async_session, org) == 300_000

    async def test_a_top_up_survives_the_renewal(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "topup")
        mandate = await _mandate(async_session, org, plan_code="business")
        await _grant(async_session, org, paise=300_000, ref="pay_a")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=100_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=400_000,
            )
        )
        await async_session.flush()
        await plans.grant_plan_cycle(
            async_session,
            mandate=mandate,
            event={"payload": {"payment": {"entity": {"id": "pay_b"}}}},
        )
        assert await _balance(async_session, org) == 400_000

    async def test_an_annual_collection_grants_twelve_months(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "annual")
        mandate = await _mandate(
            async_session, org, plan_code="business", billing_period="annual"
        )
        result = await plans.grant_plan_cycle(
            async_session,
            mandate=mandate,
            event={"payload": {"payment": {"entity": {"id": "pay_year"}}}},
        )
        assert result["granted_paise"] == 300_000 * 12

    async def test_an_annual_grant_lives_a_year(self, db_session, async_session):
        """The lapsed sweep must not take a year's credits after a month."""
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "annual-sweep")
        await _mandate(
            async_session, org, plan_code="business", billing_period="annual"
        )
        await _grant(
            async_session,
            org,
            paise=300_000 * 12,
            ref="pay_year_old",
            created_at=datetime.now(UTC) - timedelta(days=200),
        )
        counters = await plans.sweep_lapsed_plan_balance(async_session)
        assert counters["expired"] == 0
        assert await _balance(async_session, org) == 300_000 * 12


class TestEveryCapIsARow:
    async def test_the_caps_seed_from_the_spec(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        table = await plan_limits.limits_for_plan(async_session, plan_code="business")
        assert table["knowledge_pages"] == 2_000
        assert table["builder_messages"] == 100
        assert table["concurrent_calls"] == 5
        scale = await plan_limits.limits_for_plan(async_session, plan_code="scale")
        assert scale["builder_messages"] is None  # unlimited
        # Every registry key has a row on every plan on the ladder.
        for code in plan_limits.LADDER:
            row = await plan_limits.limits_for_plan(async_session, plan_code=code)
            assert set(row) == {spec.key for spec in plan_limits.LIMITS}, code

    async def test_a_cap_knows_where_raise_this_goes(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        cap = await plan_limits.resolve(
            async_session, plan_code="everyday", key="knowledge_pages"
        )
        assert cap.value == 500
        assert cap.raise_to == "business"
        assert cap.raise_path == "upgrade:business"
        top = await plan_limits.resolve(
            async_session, plan_code="scale", key="knowledge_pages"
        )
        assert top.raise_to is None
        assert top.raise_path == "support"

    async def test_an_operator_edit_survives_a_reseed(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        await plan_limits.save(
            async_session, plan_code="business", key="knowledge_pages", value=3_000
        )
        await plan_limits.ensure_seeded(async_session)
        table = await plan_limits.limits_for_plan(async_session, plan_code="business")
        assert table["knowledge_pages"] == 3_000

    async def test_an_unknown_cap_is_refused(self, db_session, async_session):
        with pytest.raises(plan_limits.LimitError):
            await plan_limits.save(
                async_session, plan_code="business", key="moon_landings", value=1
            )

    async def test_the_cap_an_account_is_under(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org = await _org(async_session, "capped")
        cap = await plan_limits.limit_for_organization(
            async_session, organization_id=org.id, key="bots"
        )
        assert cap.plan_code == "free"
        assert cap.value == 1
        assert cap.allows(0) and not cap.allows(1)

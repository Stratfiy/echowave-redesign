"""What a subscription buys beyond minutes, and what an unsubscribed account gets.

Two entitlements that are only correct if they are *withheld* by default, and
both were previously ungated in a way that cost money quietly.

**The knowledge base.** Every document is embedded at ingestion on our own model
key, and embeddings have no cost component, no rate and no ledger debit anywhere
— by design, because nobody in the category meters them. The other half of that
design is that the allowance belongs to a plan. It did not: the ceiling was one
environment variable identical for every account, so an account that had never
paid could have a corpus embedded at our expense.

**The first top-up.** The ordinary floor keeps card fees from exceeding the
credit bought. The first-payment floor is commercial, and applies exactly once —
an existing customer topping up mid-campaign needs ₹200 to be ₹200.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.constants import (
    FIRST_TOPUP_MIN_PAISE,
    MIN_TOPUP_PAISE,
    STARTER_PLAN_KNOWLEDGE_BASE_BYTES,
    STARTER_PLAN_KNOWLEDGE_BASE_FILE_BYTES,
    TOPUP_INCREMENT_PAISE,
)
from api.db.models import CreditLedgerModel, OrganizationModel, PaymentMandateModel
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import mandates as mandate_service
from api.services.billing import payments, subscription_plans

MB = 1024 * 1024


async def _org(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _mandate(session, org, *, status: str, plan_code: str | None = "starter"):
    mandate = PaymentMandateModel(
        organization_id=org.id,
        provider="razorpay",
        purpose=mandate_service.PURPOSE_STARTER_PLAN,
        subscription_id=f"sub_{org.id}_{status}",
        plan_id="plan_starter",
        plan_code=plan_code,
        status=status,
        price_paise=299900,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def _topup_row(session, org, *, kind=CreditLedgerKind.TOPUP):
    session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=50_000,
            kind=kind.value,
            ref_type="payment",
            ref_id=f"pay_{org.id}_{kind.value}",
            balance_after_paise=50_000,
        )
    )
    await session.flush()


class TestTheKnowledgeBaseIsSomethingAPlanBuys:
    async def test_no_plan_means_no_allowance(self, async_session):
        """The value the whole column exists for. Nothing is not an edge case
        here — it is what stops an unsubscribed account having a corpus
        embedded on our key and billed to nobody."""
        org = await _org(async_session, "kb-none")
        allowance = await subscription_plans.knowledge_base_allowance_for(
            async_session, organization_id=org.id
        )
        assert allowance == subscription_plans.NO_KNOWLEDGE_BASE
        assert not allowance.includes_a_knowledge_base

    async def test_an_authorised_plan_grants_its_own_figures(self, async_session):
        org = await _org(async_session, "kb-active")
        await subscription_plans.ensure_seeded(async_session)
        await _mandate(async_session, org, status=MandateStatus.ACTIVE.value)

        allowance = await subscription_plans.knowledge_base_allowance_for(
            async_session, organization_id=org.id
        )
        assert allowance.total_bytes == STARTER_PLAN_KNOWLEDGE_BASE_BYTES
        assert allowance.max_file_bytes == STARTER_PLAN_KNOWLEDGE_BASE_FILE_BYTES

    async def test_a_started_but_unauthorised_mandate_grants_nothing(
        self, async_session
    ):
        """Beginning checkout is not paying for it. Entitling on an instruction
        the bank has not confirmed hands the feature to anyone who opens the
        payment sheet and closes it."""
        org = await _org(async_session, "kb-pending")
        await subscription_plans.ensure_seeded(async_session)
        await _mandate(async_session, org, status=MandateStatus.CREATED.value)

        assert (
            await subscription_plans.knowledge_base_allowance_for(
                async_session, organization_id=org.id
            )
            == subscription_plans.NO_KNOWLEDGE_BASE
        )

    async def test_both_figures_follow_the_plan_not_the_deployment(self, async_session):
        """Two plans, two allowances, one deployment. This is the property a
        single environment variable could not express, and the reason the
        columns exist rather than a larger default."""
        org = await _org(async_session, "kb-big")
        await subscription_plans.save(
            async_session,
            code="scale",
            label="Scale",
            price_paise=1_999_900,
            balance_paise=1_900_000,
            included_numbers=4,
            knowledge_base_bytes=500 * MB,
            knowledge_base_max_file_bytes=25 * MB,
            razorpay_plan_id="plan_scale",
        )
        await _mandate(
            async_session, org, status=MandateStatus.ACTIVE.value, plan_code="scale"
        )

        allowance = await subscription_plans.knowledge_base_allowance_for(
            async_session, organization_id=org.id
        )
        assert allowance.total_bytes == 500 * MB
        assert allowance.max_file_bytes == 25 * MB

    async def test_a_plan_with_no_file_figure_follows_the_deployment(
        self, async_session
    ):
        """Null-ish means "whatever the platform allows", exactly as
        extra_number_price_paise follows the rental price — so a plan that has
        no opinion does not pin a copy that goes stale."""
        from api.constants import KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES

        plan = await subscription_plans.save(
            async_session,
            code="quiet",
            label="Quiet",
            price_paise=100_000,
            balance_paise=100_000,
            included_numbers=0,
            knowledge_base_bytes=50 * MB,
            razorpay_plan_id="plan_quiet",
        )
        assert plan.knowledge_base_max_file_bytes == KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES

    async def test_a_file_limit_above_the_total_is_refused(self, async_session):
        """A per-file limit larger than the whole allowance is a number no
        document could ever reach: the total refuses the upload first. Shown on
        a screen it reads as a promise the product cannot keep."""
        with pytest.raises(subscription_plans.PlanError, match="single file larger"):
            await subscription_plans.save(
                async_session,
                code="impossible",
                label="Impossible",
                price_paise=100_000,
                balance_paise=100_000,
                included_numbers=0,
                knowledge_base_bytes=10 * MB,
                knowledge_base_max_file_bytes=25 * MB,
            )

    async def test_the_starter_plan_seeds_with_both(self, async_session):
        plan = await subscription_plans.ensure_seeded(async_session)
        assert plan.knowledge_base_bytes == STARTER_PLAN_KNOWLEDGE_BASE_BYTES
        assert plan.knowledge_base_max_file_bytes == (
            STARTER_PLAN_KNOWLEDGE_BASE_FILE_BYTES
        )
        assert plan.knowledge_base_bytes > 0


class TestTheFirstTopUpFloor:
    async def test_a_new_account_faces_the_higher_floor(self, async_session):
        org = await _org(async_session, "topup-new")
        assert (
            await payments.minimum_topup_paise(async_session, organization_id=org.id)
            == FIRST_TOPUP_MIN_PAISE
        )

    async def test_it_drops_once_a_payment_has_landed(self, async_session):
        """Applies once. An existing customer topping up mid-campaign needs
        ₹200 to be ₹200 — making them buy ₹1,000 to add it is a worse
        experience than the one the floor prevents."""
        org = await _org(async_session, "topup-returning")
        await _topup_row(async_session, org)

        assert (
            await payments.minimum_topup_paise(async_session, organization_id=org.id)
            == MIN_TOPUP_PAISE
        )

    async def test_trial_credit_is_not_a_payment(self, async_session):
        """A gift is not evidence the account will pay. Counting it would drop
        the floor for every account that ever received trial credit, which is
        all of them."""
        org = await _org(async_session, "topup-trial")
        await _topup_row(async_session, org, kind=CreditLedgerKind.TRIAL)

        assert (
            await payments.minimum_topup_paise(async_session, organization_id=org.id)
            == FIRST_TOPUP_MIN_PAISE
        )

    async def test_one_account_s_payment_does_not_free_another(self, async_session):
        """The query is org-scoped. Without the filter the first customer to pay
        would lower the floor for every account in the deployment."""
        payer = await _org(async_session, "topup-payer")
        stranger = await _org(async_session, "topup-stranger")
        await _topup_row(async_session, payer)

        assert (
            await payments.minimum_topup_paise(
                async_session, organization_id=stranger.id
            )
            == FIRST_TOPUP_MIN_PAISE
        )

    def test_the_floor_is_a_purchasable_amount(self):
        """Top-ups are sold in whole steps. A floor that is not one is a
        minimum nobody can actually buy — the picker's smallest option would be
        rejected by the very check that set it."""
        assert FIRST_TOPUP_MIN_PAISE % TOPUP_INCREMENT_PAISE == 0
        assert FIRST_TOPUP_MIN_PAISE >= MIN_TOPUP_PAISE


class TestTheRentalPlanHasAnExportTwin:
    """An extra number, sold to an account whose supply is zero-rated.

    Same shape as ``razorpay_plan_id_export`` on a plan row, and it exists for
    the same reason: a pinned plan holds one fixed amount at the provider, and
    ``_assert_pinned_plan_amount`` checks it against the gross this account
    actually owes -- Rs659.62 domestic, Rs559.00 for an export. One id cannot
    answer both, so pinning only the domestic plan refuses every export account
    at the guard. Correct, and not a fix.
    """

    def test_the_two_ids_are_separate_settings(self):
        from api import constants

        assert hasattr(constants, "RAZORPAY_RENTAL_PLAN_ID")
        assert hasattr(constants, "RAZORPAY_RENTAL_PLAN_ID_EXPORT")

    async def test_an_export_account_is_sent_to_the_export_plan(self, monkeypatch):
        from api.services.billing import mandates

        monkeypatch.setattr(mandates, "RAZORPAY_RENTAL_PLAN_ID", "plan_domestic")
        monkeypatch.setattr(mandates, "RAZORPAY_RENTAL_PLAN_ID_EXPORT", "plan_export")

        seen: dict = {}

        async def _fake_ensure(**kwargs):
            seen.update(kwargs)
            return kwargs["pinned"]

        monkeypatch.setattr(mandates, "_ensure_plan", _fake_ensure)

        assert (
            await mandates.ensure_rental_plan(price_paise=55_900, is_export=True)
            == "plan_export"
        )
        assert seen["env_var"] == "RAZORPAY_RENTAL_PLAN_ID_EXPORT"

    async def test_a_domestic_account_is_sent_to_the_domestic_plan(self, monkeypatch):
        """The default, and it must stay the default: an account with no export
        status is domestic, and billing it at the net would collect no GST at
        all, monthly, by standing instruction."""
        from api.services.billing import mandates

        monkeypatch.setattr(mandates, "RAZORPAY_RENTAL_PLAN_ID", "plan_domestic")
        monkeypatch.setattr(mandates, "RAZORPAY_RENTAL_PLAN_ID_EXPORT", "plan_export")

        async def _fake_ensure(**kwargs):
            return kwargs["pinned"]

        monkeypatch.setattr(mandates, "_ensure_plan", _fake_ensure)

        assert await mandates.ensure_rental_plan(price_paise=65_962) == "plan_domestic"


class TestPinningIsNotRepricing:
    """Adding a provider id must not rewrite what the plan costs.

    ``save`` rewrites the whole row, so using it to fill in a missing id also
    resets every other field to whatever the caller happened to be holding --
    silently, and over an operator's edit. A null id is "not configured yet"
    rather than a decision, so filling one in is safe; the price beside it is a
    decision, and is not this function's to touch.
    """

    async def test_it_leaves_the_price_alone(self, async_session):
        await subscription_plans.save(
            async_session,
            code="pinme",
            label="Pin me",
            price_paise=500_000,
            balance_paise=400_000,
            included_numbers=1,
        )
        # Stand in for an operator having edited the price after seeding.
        await subscription_plans.save(
            async_session,
            code="pinme",
            label="Pin me",
            price_paise=444_000,
            balance_paise=400_000,
            included_numbers=1,
        )

        pinned = await subscription_plans.set_provider_plan_ids(
            async_session, code="pinme", razorpay_plan_id="plan_live"
        )
        assert pinned.razorpay_plan_id == "plan_live"
        assert pinned.price_paise == 444_000

    async def test_omitting_an_id_does_not_clear_it(self, async_session):
        """A caller that knows only the domestic id must not blank the export
        one by saying nothing about it."""
        await subscription_plans.save(
            async_session,
            code="keepme",
            label="Keep me",
            price_paise=500_000,
            balance_paise=400_000,
            included_numbers=0,
            razorpay_plan_id="plan_dom",
            razorpay_plan_id_export="plan_exp",
        )

        pinned = await subscription_plans.set_provider_plan_ids(
            async_session, code="keepme", razorpay_plan_id="plan_dom_2"
        )
        assert pinned.razorpay_plan_id == "plan_dom_2"
        assert pinned.razorpay_plan_id_export == "plan_exp"

    async def test_it_still_refuses_one_id_for_both_amounts(self, async_session):
        """The guard from ``save`` applies here too: a pinned plan holds one
        fixed amount, so it cannot collect the gross and the net."""
        await subscription_plans.save(
            async_session,
            code="samesame",
            label="Same",
            price_paise=500_000,
            balance_paise=400_000,
            included_numbers=0,
            razorpay_plan_id="plan_one",
        )

        with pytest.raises(subscription_plans.PlanError, match="separate Razorpay"):
            await subscription_plans.set_provider_plan_ids(
                async_session, code="samesame", razorpay_plan_id_export="plan_one"
            )

    async def test_an_unknown_plan_is_refused(self, async_session):
        with pytest.raises(subscription_plans.PlanError, match="No plan with code"):
            await subscription_plans.set_provider_plan_ids(
                async_session, code="ghost", razorpay_plan_id="plan_x"
            )


class TestThePlanSetsThePerMinuteFee:
    """A tier's per-minute price is applied when the mandate is authorised.

    The platform fee was already per-account and effective-dated, but the only
    thing that ever wrote a row was an operator on the admin screen. So a tiered
    price list was something you could describe and not something the product
    did: every account kept the list rate until somebody went and changed it,
    one at a time. This is the wiring that makes the ladder real.
    """

    async def _authorise(self, session, org, *, plan_code: str):
        """Drive the mandate through the transition the webhook drives."""
        from api.services.billing import mandates

        session.add(
            PaymentMandateModel(
                organization_id=org.id,
                provider="razorpay",
                purpose=mandates.PURPOSE_STARTER_PLAN,
                subscription_id=f"sub_rate_{org.id}",
                plan_id="plan_x",
                plan_code=plan_code,
                status=MandateStatus.CREATED.value,
                price_paise=799_900,
            )
        )
        await session.flush()
        return await mandates.apply_subscription_event(
            session,
            event={
                "event": "subscription.activated",
                "payload": {"subscription": {"entity": {"id": f"sub_rate_{org.id}"}}},
            },
        )

    async def _rate(self, session, org) -> int:
        from api.services.billing.rates import resolve_platform_rate

        resolved = await resolve_platform_rate(
            session, organization_id=org.id, at=datetime.now(UTC)
        )
        return resolved.rate_mpaise

    async def test_authorising_moves_the_account_to_its_tier_rate(self, async_session):
        from api.constants import STARTER_PLAN_PRICE_PAISE

        org = await _org(async_session, "rate-growth")
        await subscription_plans.save(
            async_session,
            code="growth",
            label="Growth",
            price_paise=STARTER_PLAN_PRICE_PAISE,
            balance_paise=200_000,
            included_numbers=2,
            platform_rate_mpaise=200_000,
            razorpay_plan_id="plan_growth",
        )

        result = await self._authorise(async_session, org, plan_code="growth")
        assert result["newly_authorised"] is True
        assert await self._rate(async_session, org) == 200_000

    async def test_a_plan_with_no_figure_leaves_the_rate_alone(self, async_session):
        """Null is not zero. A plan with no opinion about the fee must not move
        an account off a rate somebody negotiated for it."""
        org = await _org(async_session, "rate-quiet")
        before = await self._rate(async_session, org)

        await subscription_plans.save(
            async_session,
            code="quietplan",
            label="Quiet",
            price_paise=100_000,
            balance_paise=100_000,
            included_numbers=0,
            razorpay_plan_id="plan_quiet_rate",
        )
        await self._authorise(async_session, org, plan_code="quietplan")

        assert await self._rate(async_session, org) == before

    async def test_a_negative_fee_is_refused(self, async_session):
        with pytest.raises(subscription_plans.PlanError, match="negative platform fee"):
            await subscription_plans.save(
                async_session,
                code="upsidedown",
                label="Upside down",
                price_paise=100_000,
                balance_paise=100_000,
                included_numbers=0,
                platform_rate_mpaise=-1,
            )

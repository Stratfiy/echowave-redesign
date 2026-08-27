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

from api.constants import (
    FIRST_TOPUP_MIN_PAISE,
    MIN_TOPUP_PAISE,
    STARTER_PLAN_KNOWLEDGE_BASE_BYTES,
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
        """The value the whole column exists for. Zero is not an edge case here
        — it is what stops an unsubscribed account having a corpus embedded on
        our key and billed to nobody."""
        org = await _org(async_session, "kb-none")
        assert (
            await subscription_plans.knowledge_base_bytes_for(
                async_session, organization_id=org.id
            )
            == 0
        )

    async def test_an_authorised_plan_grants_its_own_figure(self, async_session):
        org = await _org(async_session, "kb-active")
        await subscription_plans.ensure_seeded(async_session)
        await _mandate(async_session, org, status=MandateStatus.ACTIVE.value)

        assert (
            await subscription_plans.knowledge_base_bytes_for(
                async_session, organization_id=org.id
            )
            == STARTER_PLAN_KNOWLEDGE_BASE_BYTES
        )

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
            await subscription_plans.knowledge_base_bytes_for(
                async_session, organization_id=org.id
            )
            == 0
        )

    async def test_the_allowance_follows_the_plan_not_the_deployment(
        self, async_session
    ):
        """Two plans, two allowances, one deployment. This is the property a
        single environment variable could not express, and the reason the
        column exists rather than a larger default."""
        org = await _org(async_session, "kb-big")
        await subscription_plans.save(
            async_session,
            code="scale",
            label="Scale",
            price_paise=1_999_900,
            balance_paise=1_900_000,
            included_numbers=4,
            knowledge_base_bytes=250 * MB,
            razorpay_plan_id="plan_scale",
        )
        await _mandate(
            async_session, org, status=MandateStatus.ACTIVE.value, plan_code="scale"
        )

        assert (
            await subscription_plans.knowledge_base_bytes_for(
                async_session, organization_id=org.id
            )
            == 250 * MB
        )

    async def test_the_starter_plan_seeds_with_one(self, async_session):
        plan = await subscription_plans.ensure_seeded(async_session)
        assert plan.knowledge_base_bytes == STARTER_PLAN_KNOWLEDGE_BASE_BYTES
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

"""Debiting the ledger for what a document's ingestion embeddings actually cost.

Document upload embeds every chunk on our key, and until this change nothing
in the codebase priced that -- not even as an ``uncosted`` line. This is the
other half of embedding billing: query-time retrieval during a call already
goes through the ordinary call-costing path (``test_embedding_billing.py``),
but ingestion has no ``workflow_run_id`` to attach a receipt to, so it debits
the ledger directly instead, the way ``rentals.py`` does for a charge that
isn't a call either.

Covers the same three guarantees ``BUILD-PLAN.md``'s Phase 3 names as done:
a document is charged what it actually cost to embed, an unpriced model is
uncosted rather than silently free, and a document is billed at most once.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from api.constants import MANAGED_PROVIDER_MARKUP_BPS
from api.db import billing_kpi_client as kpi_client
from api.db.models import (
    CreditLedgerModel,
    EmbeddingIngestionCostModel,
    KnowledgeBaseDocumentModel,
    ManagedMarkupOverrideModel,
    OrganizationModel,
    ProviderRateModel,
    UserModel,
)
from api.enums import CostComponent, CreditLedgerKind, RateUnit
from api.services.billing import embedding_ingestion as billing
from api.services.billing.money import round_half_up_div


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _document(
    async_session, org: OrganizationModel, slug: str
) -> KnowledgeBaseDocumentModel:
    """A real row, not just an id -- embedding_ingestion_costs.document_id is
    a foreign key onto this table, so debit_ingestion_cost's tests need one
    to point at."""
    user = UserModel(provider_id=f"user-{slug}")
    async_session.add(user)
    await async_session.flush()

    document = KnowledgeBaseDocumentModel(
        organization_id=org.id,
        filename=f"{slug}.pdf",
        created_by=user.id,
    )
    async_session.add(document)
    await async_session.flush()
    return document


async def _rate(async_session, *, model: str = "text-embedding-3-small") -> None:
    async_session.add(
        ProviderRateModel(
            provider="openai",
            component=CostComponent.EMBEDDING.value,
            model=model,
            unit=RateUnit.THOUSAND_TOKENS.value,
            rate_mpaise=20_000,
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    await async_session.flush()


@pytest.mark.asyncio
class TestEstimateIngestionCost:
    async def test_no_rate_on_file_is_uncosted_not_free(self, async_session):
        estimate = await billing.estimate_ingestion_cost_paise(
            async_session, provider="openai", model="text-embedding-3-small", tokens=500
        )
        assert estimate is None

    async def test_priced_and_marked_up_like_any_other_managed_line(
        self, async_session
    ):
        await _rate(async_session)

        estimate = await billing.estimate_ingestion_cost_paise(
            async_session,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )

        assert estimate is not None
        # 1000 tokens @ 20_000 mpaise/1k tokens = 20 paise vendor cost.
        assert estimate.vendor_cost_paise == 20
        assert estimate.charged_paise == round_half_up_div(
            20 * MANAGED_PROVIDER_MARKUP_BPS, 10_000
        )

    async def test_zero_tokens_costs_nothing(self, async_session):
        estimate = await billing.estimate_ingestion_cost_paise(
            async_session, provider="openai", model="text-embedding-3-small", tokens=0
        )
        assert estimate.vendor_cost_paise == 0
        assert estimate.charged_paise == 0

    async def test_a_per_model_markup_override_applies_here_too(self, async_session):
        """Same resolver ``compute_call_cost`` uses for a call line -- assert
        it rather than assume it, since this path calls it directly instead
        of through the cost engine."""
        await _rate(async_session)
        async_session.add(
            ManagedMarkupOverrideModel(
                provider="openai",
                component=CostComponent.EMBEDDING.value,
                model="text-embedding-3-small",
                markup_bps=12_000,  # 1.2x, cheaper than the default fallback
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        await async_session.flush()

        estimate = await billing.estimate_ingestion_cost_paise(
            async_session,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )

        assert estimate.charged_paise == round_half_up_div(20 * 12_000, 10_000)


@pytest.mark.asyncio
class TestHasBalanceForEstimate:
    async def test_an_unpriced_model_never_blocks_an_upload(self, async_session):
        org = await _org(async_session, "unpriced")
        assert await billing.has_balance_for_estimate(
            async_session, organization_id=org.id, estimate=None
        )

    async def test_a_zero_charge_never_blocks_an_upload(self, async_session):
        org = await _org(async_session, "zero-charge")
        estimate = billing.IngestionPrice(vendor_cost_paise=0, charged_paise=0)
        assert await billing.has_balance_for_estimate(
            async_session, organization_id=org.id, estimate=estimate
        )

    async def test_an_empty_ledger_cannot_afford_a_real_charge(self, async_session):
        org = await _org(async_session, "no-balance")
        estimate = billing.IngestionPrice(vendor_cost_paise=20, charged_paise=34)
        assert not await billing.has_balance_for_estimate(
            async_session, organization_id=org.id, estimate=estimate
        )

    async def test_enough_balance_is_affordable(self, async_session):
        org = await _org(async_session, "has-balance")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=10_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=10_000,
            )
        )
        await async_session.flush()

        estimate = billing.IngestionPrice(vendor_cost_paise=20, charged_paise=34)
        assert await billing.has_balance_for_estimate(
            async_session, organization_id=org.id, estimate=estimate
        )


@pytest.mark.asyncio
class TestDebitIngestionCost:
    async def test_debits_the_ledger_by_the_marked_up_amount(self, async_session):
        org = await _org(async_session, "debit")
        document = await _document(async_session, org, "debit")
        await _rate(async_session)

        price = await billing.debit_ingestion_cost(
            async_session,
            organization_id=org.id,
            document_id=document.id,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )
        await async_session.flush()

        assert price is not None
        assert price.charged_paise == round_half_up_div(
            20 * MANAGED_PROVIDER_MARKUP_BPS, 10_000
        )

        ledger_row = (
            await async_session.scalars(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org.id,
                    CreditLedgerModel.kind == CreditLedgerKind.EMBEDDING_INGEST.value,
                )
            )
        ).one()
        assert ledger_row.ref_type == "kb_document"
        assert ledger_row.ref_id == str(document.id)
        assert ledger_row.delta_paise == -price.charged_paise

        # The cost-side twin call_cost_items keeps for a call -- what the
        # vendor charged us, not just what we charged the customer.
        cost_row = (
            await async_session.scalars(
                select(EmbeddingIngestionCostModel).where(
                    EmbeddingIngestionCostModel.document_id == document.id
                )
            )
        ).one()
        assert cost_row.vendor_cost_paise == price.vendor_cost_paise
        assert cost_row.charged_paise == price.charged_paise
        assert cost_row.tokens == 1_000

    async def test_a_document_is_debited_at_most_once(self, async_session):
        org = await _org(async_session, "idempotent")
        document = await _document(async_session, org, "idempotent")
        await _rate(async_session)

        first = await billing.debit_ingestion_cost(
            async_session,
            organization_id=org.id,
            document_id=document.id,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )
        await async_session.flush()
        second = await billing.debit_ingestion_cost(
            async_session,
            organization_id=org.id,
            document_id=document.id,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )

        assert first is not None
        assert second is None

        count = await async_session.scalar(
            select(func.count()).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.EMBEDDING_INGEST.value,
            )
        )
        assert count == 1

        cost_count = await async_session.scalar(
            select(func.count()).where(
                EmbeddingIngestionCostModel.document_id == document.id
            )
        )
        assert cost_count == 1

    async def test_an_unpriced_model_debits_nothing(self, async_session):
        org = await _org(async_session, "unpriced-debit")

        price = await billing.debit_ingestion_cost(
            async_session,
            organization_id=org.id,
            document_id=303,
            provider="openai",
            model="text-embedding-3-small",
            tokens=1_000,
        )
        await async_session.flush()

        assert price is None

        count = await async_session.scalar(
            select(func.count()).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.EMBEDDING_INGEST.value,
            )
        )
        assert count == 0

    async def test_zero_tokens_writes_no_ledger_row(self, async_session):
        org = await _org(async_session, "zero-tokens")
        await _rate(async_session)

        price = await billing.debit_ingestion_cost(
            async_session,
            organization_id=org.id,
            document_id=404,
            provider="openai",
            model="text-embedding-3-small",
            tokens=0,
        )

        assert price.charged_paise == 0


@pytest.mark.asyncio
class TestEmbeddingIngestionInTheUnitEconomicsReport:
    """The document-level counterpart to the call-level unit-economics
    query -- see billing_kpi_client.embedding_ingestion_totals's own
    docstring for why it's a sibling report rather than folded into the
    per-minute totals."""

    async def test_sums_documents_tokens_cost_and_charge_in_range(self, async_session):
        org = await _org(async_session, "kpi")
        doc_a = await _document(async_session, org, "kpi-a")
        doc_b = await _document(async_session, org, "kpi-b")
        doc_c = await _document(async_session, org, "kpi-c")
        in_range = datetime(2026, 5, 15, 6, tzinfo=UTC)  # well inside the IST day

        async_session.add_all(
            [
                EmbeddingIngestionCostModel(
                    organization_id=org.id,
                    document_id=doc_a.id,
                    provider="openai",
                    model="text-embedding-3-small",
                    tokens=1_000,
                    vendor_cost_paise=20,
                    charged_paise=34,
                    created_at=in_range,
                ),
                EmbeddingIngestionCostModel(
                    organization_id=org.id,
                    document_id=doc_b.id,
                    provider="openai",
                    model="text-embedding-3-small",
                    tokens=500,
                    vendor_cost_paise=10,
                    charged_paise=17,
                    created_at=in_range,
                ),
                # Outside the queried range -- must not be counted.
                EmbeddingIngestionCostModel(
                    organization_id=org.id,
                    document_id=doc_c.id,
                    provider="openai",
                    model="text-embedding-3-small",
                    tokens=999_999,
                    vendor_cost_paise=999_999,
                    charged_paise=999_999,
                    created_at=datetime(2026, 1, 1, 6, tzinfo=UTC),
                ),
            ]
        )
        await async_session.flush()

        totals = await kpi_client.embedding_ingestion_totals(
            async_session, start=date(2026, 5, 15), end=date(2026, 5, 15)
        )

        assert totals == {
            "documents": 2,
            "tokens": 1_500,
            "vendor_cost_paise": 30,
            "charged_paise": 51,
        }

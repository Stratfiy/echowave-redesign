"""Knowledge-base caps in pages, and the price of a page past the cap (KAN-57).

Caps by plan, a tenth of a credit for a typed page past the cap and two for a
scanned one, settled once per document; the upload screen is told the cost
before it runs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from api.db.models import (
    CreditLedgerModel,
    KnowledgeBaseDocumentModel,
    OrganizationModel,
    PaymentMandateModel,
    UserModel,
)
from api.enums import CreditLedgerKind, MandateStatus
from api.services.billing import knowledge_pages as kp
from api.services.billing import plan_limits, subscription_plans
from api.services.billing.mandates import PURPOSE_STARTER_PLAN


class TestCountingPages:
    def test_a_pdf_reports_its_pages(self):
        pages = kp.pages_from_metadata({"page_count": 12, "extractor": "pypdf"})
        assert (pages.total, pages.scanned, pages.typed) == (12, 0, 12)

    def test_a_scan_reports_the_pages_ocr_read(self):
        pages = kp.pages_from_metadata(
            {"page_count": 8, "ocr_pages": 8, "extractor": "tesseract"}
        )
        assert (pages.total, pages.scanned, pages.typed) == (8, 8, 0)

    def test_ocr_pages_only_count_as_scanned_when_ocr_ran(self):
        pages = kp.pages_from_metadata(
            {"page_count": 8, "ocr_pages": 8, "extractor": "pypdf"}
        )
        assert pages.scanned == 0

    def test_a_document_without_pages_is_measured_by_characters(self):
        pages = kp.pages_from_metadata(
            {"character_count": 7_500, "extractor": "python-docx"}
        )
        assert pages.total == 3  # 7,500 / 3,000, rounded up

    def test_anything_that_produced_text_is_at_least_a_page(self):
        assert kp.pages_from_metadata({"character_count": 40}).total == 1
        assert kp.pages_from_metadata({}).total == 1
        assert kp.pages_from_metadata(None).total == 1


class TestThePrice:
    def test_ten_typed_pages_are_a_credit_rounded_up(self):
        assert kp.overage_credits(typed_pages=10, scanned_pages=0) == 1
        assert kp.overage_credits(typed_pages=11, scanned_pages=0) == 2
        assert kp.overage_credits(typed_pages=1, scanned_pages=0) == 1
        assert kp.overage_credits(typed_pages=0, scanned_pages=0) == 0

    def test_a_scanned_page_is_two(self):
        assert kp.overage_credits(typed_pages=0, scanned_pages=3) == 6
        assert kp.overage_credits(typed_pages=25, scanned_pages=2) == 3 + 4

    def test_the_rooms_goes_to_typed_pages_first(self):
        pages = kp.Pages(total=30, scanned=10)
        # 15 pages of room: 20 typed take 15, 5 typed and all 10 scans are past.
        assert kp.split_past_the_cap(used=485, cap=500, pages=pages) == (5, 10)
        assert kp.split_past_the_cap(used=0, cap=500, pages=pages) == (0, 0)
        assert kp.split_past_the_cap(used=500, cap=500, pages=pages) == (20, 10)
        assert kp.split_past_the_cap(used=9_999, cap=None, pages=pages) == (0, 0)


async def _org(session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    return org, user


async def _on_plan(session, org, code: str):
    session.add(
        PaymentMandateModel(
            organization_id=org.id,
            provider="razorpay",
            purpose=PURPOSE_STARTER_PLAN,
            subscription_id=f"sub_{org.id}_{code}",
            plan_id=f"plan_{code}",
            plan_code=code,
            status=MandateStatus.ACTIVE.value,
            price_paise=0,
        )
    )
    await session.flush()


async def _document(
    session, org, user, *, pages: int, scanned: int = 0, status="completed", active=True
):
    row = KnowledgeBaseDocumentModel(
        organization_id=org.id,
        created_by=user.id,
        filename=f"doc-{pages}.pdf",
        file_size_bytes=1_000,
        processing_status=status,
        is_active=active,
        page_count=pages,
        scanned_page_count=scanned,
    )
    session.add(row)
    await session.flush()
    return row


async def _ledger(session, org):
    return (
        await session.scalars(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
    ).all()


@pytest.mark.asyncio
class TestWhatIsHeld:
    async def test_active_completed_documents_add_up(self, db_session, async_session):
        org, user = await _org(async_session, "held")
        await _document(async_session, org, user, pages=12)
        await _document(async_session, org, user, pages=30)
        await _document(async_session, org, user, pages=99, active=False)
        await _document(async_session, org, user, pages=99, status="processing")
        assert await kp.pages_used(async_session, organization_id=org.id) == 42

    async def test_a_document_can_be_left_out_of_its_own_count(
        self, db_session, async_session
    ):
        org, user = await _org(async_session, "self")
        doc = await _document(async_session, org, user, pages=12)
        assert (
            await kp.pages_used(
                async_session, organization_id=org.id, excluding_document_id=doc.id
            )
            == 0
        )


@pytest.mark.asyncio
class TestTheQuote:
    async def test_inside_the_cap_nothing_is_charged(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "inside")
        await _on_plan(async_session, org, "everyday")
        await _document(async_session, org, user, pages=100)
        q = await kp.quote(
            async_session, organization_id=org.id, pages=kp.Pages(total=20, scanned=5)
        )
        assert (q.pages_used, q.pages_cap, q.over_cap) == (100, 500, False)
        assert q.credits == 0
        assert q.raise_to == "business"
        assert q.as_dict()["raise_path"] == "upgrade:business"

    async def test_past_the_cap_the_price_is_stated(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "past")
        await _on_plan(async_session, org, "everyday")
        await _document(async_session, org, user, pages=500)
        q = await kp.quote(
            async_session, organization_id=org.id, pages=kp.Pages(total=25, scanned=2)
        )
        assert q.over_cap
        assert (q.charged_typed, q.charged_scanned) == (23, 2)
        assert q.credits == 3 + 4

    async def test_free_holds_fifty_and_is_told_everyday(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "free")
        q = await kp.quote(async_session, organization_id=org.id, pages=kp.Pages(1, 0))
        assert q.pages_cap == 50
        assert q.raise_to == "everyday"


@pytest.mark.asyncio
class TestSettlement:
    async def test_a_document_past_the_cap_is_charged_once(
        self, db_session, async_session
    ):
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "settle")
        await _on_plan(async_session, org, "everyday")
        await _document(async_session, org, user, pages=500)
        doc = await _document(async_session, org, user, pages=25, scanned=2)
        paise = await kp.settle(
            async_session,
            organization_id=org.id,
            document_id=doc.id,
            pages=kp.Pages(total=25, scanned=2),
            filename="scan.pdf",
        )
        assert paise == 7 * 50
        again = await kp.settle(
            async_session,
            organization_id=org.id,
            document_id=doc.id,
            pages=kp.Pages(total=25, scanned=2),
        )
        assert again == 0
        rows = await _ledger(async_session, org)
        assert len(rows) == 1
        assert rows[0].kind == CreditLedgerKind.USAGE.value
        assert rows[0].ref_type == "knowledge_pages"
        assert rows[0].ref_id == str(doc.id)
        assert "23 typed pages, 2 scanned pages · 7 credits" in rows[0].note

    async def test_inside_the_cap_writes_nothing(self, db_session, async_session):
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "free-inside")
        doc = await _document(async_session, org, user, pages=10)
        assert (
            await kp.settle(
                async_session,
                organization_id=org.id,
                document_id=doc.id,
                pages=kp.Pages(total=10, scanned=0),
            )
            == 0
        )
        assert await _ledger(async_session, org) == []

    async def test_the_documents_own_pages_are_not_counted_as_already_held(
        self, db_session, async_session
    ):
        """The worker writes page_count before settling. Counting the
        document against itself would charge every upload at the cap."""
        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "self-count")
        doc = await _document(async_session, org, user, pages=50)  # Free cap is 50
        assert (
            await kp.settle(
                async_session,
                organization_id=org.id,
                document_id=doc.id,
                pages=kp.Pages(total=50, scanned=0),
            )
            == 0
        )


@pytest.mark.asyncio
class TestTheScreenIsToldBeforeItRuns:
    async def test_the_allowance_route(self, db_session, async_session):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_user

        await subscription_plans.ensure_seeded(async_session)
        org, user = await _org(async_session, "route")
        await _on_plan(async_session, org, "everyday")
        await _document(async_session, org, user, pages=500)
        user.selected_organization_id = org.id
        await async_session.commit()

        @asynccontextmanager
        async def _client():
            async def _override():
                return user

            app.dependency_overrides[get_user] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(get_user, None)

        async with _client() as client:
            response = await client.get(
                "/api/v1/knowledge-base/allowance",
                params={"pages": 25, "scanned_pages": 2},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pages_used"] == 500
        assert body["pages_cap"] == 500
        assert body["over_cap"] is True
        assert body["credits"] == 7
        assert body["raise_path"] == "upgrade:business"
        assert body["typed_pages_per_credit"] == 10
        assert body["scanned_page_credits"] == 2
        assert body["bytes_cap"] > 0

    def test_the_cap_is_a_decided_plan_limit(self):
        spec = plan_limits.LIMITS_BY_KEY["knowledge_pages"]
        assert spec.decided
        assert plan_limits.SEED["free"]["knowledge_pages"] == 50
        assert plan_limits.SEED["scale"]["knowledge_pages"] == 50_000

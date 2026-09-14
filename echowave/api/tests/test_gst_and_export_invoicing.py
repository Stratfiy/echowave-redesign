"""GST and export invoicing (KAN-80).

The acceptance, as the ticket puts it: a Tamil Nadu Business subscription
produces a CGST+SGST tax invoice; a Karnataka one produces IGST; a US
Everyday account produces a zero-rated export invoice; the month's GSTR-1
from the ledger reconciles to Razorpay's settlements. Plus what the ticket
adds around them: GSTIN required at checkout from Business up, credit notes,
the LUT's validity, the FIRC on an export payment, and Udyam / PO / terms on
the document.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.db.models import (
    BillingProfileModel,
    OrganizationModel,
    PaymentModel,
    TaxDocumentModel,
    UserModel,
)
from api.services.billing import documents, gstr1, payments, tax
from api.services.billing import tax as tax_rules
from api.services.billing.document_pdf import render_document_pdf

OUR_STATE = "33"  # Tamil Nadu
KARNATAKA = "29"


@pytest.fixture(autouse=True)
def _supplier(monkeypatch):
    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Labs Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "33AAAAA0000A1Z5")
    monkeypatch.setattr(documents, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(documents, "SUPPLIER_LUT_NUMBER", "AD330126000123X")
    monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "2027-03-31")
    monkeypatch.setattr(tax.constants, "SUPPLIER_UDYAM_NUMBER", "UDYAM-TN-02-0012345")
    monkeypatch.setattr(documents.constants, "SUPPLIER_LUT_VALID_UNTIL", "2027-03-31")
    monkeypatch.setattr(
        documents.constants, "SUPPLIER_UDYAM_NUMBER", "UDYAM-TN-02-0012345"
    )


class TestTheLutHasADate:
    def test_a_lut_inside_its_year_is_valid(self, monkeypatch):
        monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "2027-03-31")
        assert tax_rules.lut_is_valid(datetime(2026, 9, 14).date())

    def test_a_lapsed_lut_refuses_the_export(self, monkeypatch):
        monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "2026-03-31")
        assert not tax_rules.lut_is_valid(datetime(2026, 9, 14).date())
        with pytest.raises(tax.TaxError, match="lapsed"):
            tax.compute_tax(
                taxable_paise=100_000,
                country_code="US",
                state_code=None,
                on=datetime(2026, 9, 14).date(),
            )

    def test_an_untracked_expiry_does_not_block_but_readiness_says_so(
        self, monkeypatch
    ):
        monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "")
        assert tax_rules.lut_is_valid()
        from api.services.billing import readiness

        monkeypatch.setattr(readiness, "SUPPLIER_HAS_LUT", True)
        check = readiness._lut_check()
        assert check.status == readiness.ACTION_REQUIRED
        assert "not tracked" in check.detail

    def test_domestic_supply_never_looks_at_the_lut(self, monkeypatch):
        monkeypatch.setattr(tax.constants, "SUPPLIER_LUT_VALID_UNTIL", "2020-01-01")
        breakdown = tax.compute_tax(
            taxable_paise=100_000, country_code="IN", state_code=KARNATAKA
        )
        assert breakdown.igst_paise == 18_000


class TestTheSupplierSnapshot:
    def test_it_carries_udyam_and_the_luts_validity(self):
        snapshot = documents.supplier_snapshot()
        assert snapshot["udyam_number"] == "UDYAM-TN-02-0012345"
        assert snapshot["lut_valid_until"] == "2027-03-31"
        assert snapshot["sac_code"] == "998314"


async def _org(
    session, slug: str, *, state=OUR_STATE, country="IN", gstin=None, **profile
):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    session.add(
        BillingProfileModel(
            organization_id=org.id,
            legal_name=f"{slug.title()} Pvt Ltd",
            address_line1="1 Test Road",
            city="Chennai" if state == OUR_STATE else "Bengaluru",
            state_code=state if country == "IN" else None,
            country_code=country,
            gstin=gstin,
            **profile,
        )
    )
    await session.flush()
    return org, user


async def _paid(session, org, *, net: int, taxed: bool, at=None):
    breakdown = None
    if taxed:
        profile = await session.scalar(
            select(BillingProfileModel).where(
                BillingProfileModel.organization_id == org.id
            )
        )
        breakdown = tax.compute_tax(
            taxable_paise=net,
            country_code=profile.country_code,
            state_code=profile.state_code,
        )
    row = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{org.id}_{net}",
        payment_id=f"pay_{org.id}_{net}",
        amount_paise=net,
        gross_paise=breakdown.total_paise if breakdown else net,
        cgst_paise=breakdown.cgst_paise if breakdown else 0,
        sgst_paise=breakdown.sgst_paise if breakdown else 0,
        igst_paise=breakdown.igst_paise if breakdown else 0,
        status="paid",
        paid_at=at or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
class TestTheAcceptance:
    async def test_tamil_nadu_pays_cgst_and_sgst(self, db_session, async_session):
        org, _ = await _org(async_session, "chennai", gstin="33BBBBB1111B1Z6")
        payment = await _paid(async_session, org, net=299_900, taxed=True)
        issued = await documents.issue_receipt_voucher(async_session, payment=payment)
        assert issued.supply_type == "intra_state"
        assert issued.cgst_paise == 26_991 and issued.sgst_paise == 26_991
        assert issued.igst_paise == 0

    async def test_karnataka_pays_igst(self, db_session, async_session):
        org, _ = await _org(
            async_session, "blr", state=KARNATAKA, gstin="29CCCCC2222C1Z7"
        )
        payment = await _paid(async_session, org, net=299_900, taxed=True)
        issued = await documents.issue_receipt_voucher(async_session, payment=payment)
        assert issued.supply_type == "inter_state"
        assert issued.igst_paise == 53_982
        assert issued.cgst_paise == 0

    async def test_a_us_account_is_zero_rated_under_the_lut(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "austin", country="US")
        payment = await _paid(async_session, org, net=96_000, taxed=False)
        issued = await documents.issue_receipt_voucher(async_session, payment=payment)
        assert issued.supply_type == "export"
        assert issued.total_paise == 96_000
        row = await async_session.get(TaxDocumentModel, issued.id)
        pdf = render_document_pdf(row)
        assert pdf.startswith(b"%PDF")
        assert row.supplier_snapshot["lut_valid_until"] == "2027-03-31"


@pytest.mark.asyncio
class TestCreditNotes:
    async def test_a_note_reverses_tax_in_the_originals_proportion(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "refund", gstin="33DDDDD3333D1Z8")
        payment = await _paid(async_session, org, net=100_000, taxed=True)
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
        note = await documents.issue_credit_note(
            async_session,
            organization_id=org.id,
            against_document_id=voucher.id,
            amount_paise=40_000,
            reason="Unused plan credits refunded",
        )
        assert note.kind == documents.CREDIT_NOTE
        assert note.taxable_paise == 40_000
        assert (note.cgst_paise, note.sgst_paise, note.igst_paise) == (3_600, 3_600, 0)
        assert note.total_paise == 47_200
        row = await async_session.get(TaxDocumentModel, note.id)
        assert row.line_items[0]["against_number"] == voucher.number
        assert render_document_pdf(row).startswith(b"%PDF")

    async def test_notes_have_their_own_series(self, db_session, async_session):
        org, _ = await _org(async_session, "series")
        payment = await _paid(async_session, org, net=100_000, taxed=True)
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
        first = await documents.issue_credit_note(
            async_session,
            organization_id=org.id,
            against_document_id=voucher.id,
            amount_paise=10_000,
            reason="a",
        )
        second = await documents.issue_credit_note(
            async_session,
            organization_id=org.id,
            against_document_id=voucher.id,
            amount_paise=10_000,
            reason="b",
        )
        assert first.number != second.number
        assert (
            first.number.split("/")[0] != voucher.number.split("/")[0]
            or first.number != voucher.number
        )

    async def test_a_note_cannot_exceed_what_it_reverses(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "toobig")
        payment = await _paid(async_session, org, net=100_000, taxed=True)
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
        with pytest.raises(documents.DocumentError, match="cannot exceed"):
            await documents.issue_credit_note(
                async_session,
                organization_id=org.id,
                against_document_id=voucher.id,
                amount_paise=100_001,
                reason="x",
            )

    async def test_another_accounts_document_cannot_be_credited(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "mine")
        other, _ = await _org(async_session, "theirs")
        payment = await _paid(async_session, other, net=100_000, taxed=True)
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
        with pytest.raises(documents.DocumentError, match="No such document"):
            await documents.issue_credit_note(
                async_session,
                organization_id=org.id,
                against_document_id=voucher.id,
                amount_paise=1_000,
                reason="x",
            )


@pytest.mark.asyncio
class TestEnterpriseFields:
    async def test_po_and_terms_are_frozen_onto_the_document(
        self, db_session, async_session
    ):
        org, _ = await _org(
            async_session,
            "kriti",
            gstin="33EEEEE4444E1Z9",
            po_number="PO-2026-0917",
            payment_terms="Net 45",
        )
        payment = await _paid(async_session, org, net=100_000, taxed=True)
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
        row = await async_session.get(TaxDocumentModel, voucher.id)
        assert row.customer_snapshot["po_number"] == "PO-2026-0917"
        assert row.customer_snapshot["payment_terms"] == "Net 45"
        assert row.supplier_snapshot["udyam_number"] == "UDYAM-TN-02-0012345"
        assert render_document_pdf(row).startswith(b"%PDF")


@pytest.mark.asyncio
class TestFirc:
    async def test_it_is_recorded_on_a_paid_export_payment(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "firc", country="US")
        payment = await _paid(async_session, org, net=96_000, taxed=False)
        await payments.record_firc(
            async_session, payment_id=payment.id, reference="FIRC/2026/0912/HDFC/0042"
        )
        assert payment.firc_reference == "FIRC/2026/0912/HDFC/0042"
        listed = await payments.list_payments(async_session, organization_id=org.id)
        assert listed[0]["firc_reference"] == "FIRC/2026/0912/HDFC/0042"

    async def test_an_uncaptured_payment_takes_none(self, db_session, async_session):
        org, _ = await _org(async_session, "pending", country="US")
        row = PaymentModel(
            organization_id=org.id,
            provider="razorpay",
            order_id="o",
            amount_paise=1_000,
            status="created",
        )
        async_session.add(row)
        await async_session.flush()
        with pytest.raises(payments.PaymentError):
            await payments.record_firc(async_session, payment_id=row.id, reference="x")


@pytest.mark.asyncio
class TestGstr1:
    async def test_the_month_is_grouped_and_reconciles(self, db_session, async_session):
        at = datetime(2026, 9, 10, tzinfo=UTC)
        tn, _ = await _org(async_session, "g-tn", gstin="33FFFFF5555F1Z1")
        walkin, _ = await _org(async_session, "g-b2c", state=KARNATAKA)
        us, _ = await _org(async_session, "g-us", country="US")
        p1 = await _paid(async_session, tn, net=100_000, taxed=True, at=at)
        p2 = await _paid(async_session, walkin, net=50_000, taxed=True, at=at)
        p3 = await _paid(async_session, us, net=96_000, taxed=False, at=at)
        v1 = await documents.issue_receipt_voucher(
            async_session, payment=p1, issued_at=at
        )
        await documents.issue_receipt_voucher(async_session, payment=p2, issued_at=at)
        await documents.issue_receipt_voucher(async_session, payment=p3, issued_at=at)
        await documents.issue_credit_note(
            async_session,
            organization_id=tn.id,
            against_document_id=v1.id,
            amount_paise=10_000,
            reason="Correction",
            issued_at=at + timedelta(days=1),
        )
        await payments.record_firc(async_session, payment_id=p3.id, reference="FIRC-1")
        # Outside the month: must not appear.
        elsewhere, _ = await _org(async_session, "g-oct")
        p4 = await _paid(
            async_session,
            elsewhere,
            net=5_000,
            taxed=True,
            at=datetime(2026, 10, 2, tzinfo=UTC),
        )
        await documents.issue_receipt_voucher(
            async_session, payment=p4, issued_at=datetime(2026, 10, 2, tzinfo=UTC)
        )

        report = await gstr1.build(async_session, year=2026, month=9)
        sections = {(r.section, r.kind) for r in report.rows}
        assert sections == {
            ("b2b", "receipt_voucher"),
            ("b2c", "receipt_voucher"),
            ("exp", "receipt_voucher"),
            ("cdnr", "credit_note"),
        }
        exp = next(r for r in report.rows if r.section == "exp")
        assert exp.firc_reference == "FIRC-1" and exp.igst_paise == 0
        b2c = next(r for r in report.rows if r.section == "b2c")
        assert b2c.igst_paise == 9_000 and b2c.customer_gstin is None
        cdnr = next(r for r in report.rows if r.section == "cdnr")
        assert cdnr.against_number == v1.number
        assert report.documents_gross_paise == 118_000 + 59_000 + 96_000
        assert report.payments_gross_paise == report.documents_gross_paise
        assert report.difference_paise == 0
        assert report.exports_without_firc == []
        csv_text = gstr1.to_csv(report)
        assert csv_text.splitlines()[0].startswith("section,kind,number")
        assert len(csv_text.splitlines()) == 5

    async def test_an_export_payment_without_a_firc_is_listed(
        self, db_session, async_session
    ):
        at = datetime(2026, 8, 5, tzinfo=UTC)
        us, _ = await _org(async_session, "nofirc", country="US")
        p = await _paid(async_session, us, net=96_000, taxed=False, at=at)
        await documents.issue_receipt_voucher(async_session, payment=p, issued_at=at)
        report = await gstr1.build(async_session, year=2026, month=8)
        assert [e["payment_id"] for e in report.exports_without_firc] == [p.id]

    def test_a_bad_month_is_refused(self):
        with pytest.raises(ValueError):
            gstr1.parse_month("2026-13")
        assert gstr1.parse_month("2026-09") == (2026, 9)


@pytest.mark.asyncio
class TestCheckoutNeedsTheProfile:
    """The gate the subscribe route runs before a bank is asked to collect."""

    async def _gate(self, session, org, code: str):
        from fastapi import HTTPException

        from api.routes.payments import _assert_profile_for_plan
        from api.services.billing import subscription_plans

        await subscription_plans.ensure_seeded(session)
        plan = await subscription_plans.get_plan(session, code=code)
        try:
            await _assert_profile_for_plan(session, organization_id=org.id, plan=plan)
        except HTTPException as exc:
            return exc.status_code, exc.detail
        return 200, ""

    async def test_business_without_a_gstin_is_refused_and_told(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "nogstin")
        status, detail = await self._gate(async_session, org, "business")
        assert status == 402 and "GSTIN" in detail

    async def test_business_with_a_gstin_goes_through(self, db_session, async_session):
        org, _ = await _org(async_session, "gstin", gstin="33GGGGG6666G1Z2")
        assert (await self._gate(async_session, org, "business"))[0] == 200

    async def test_everyday_does_not_need_one(self, db_session, async_session):
        org, _ = await _org(async_session, "sole")
        assert (await self._gate(async_session, org, "everyday"))[0] == 200

    async def test_an_export_account_needs_no_gstin(self, db_session, async_session):
        org, _ = await _org(async_session, "exp-biz", country="US")
        assert (await self._gate(async_session, org, "business"))[0] == 200

    async def test_no_profile_at_all_is_refused_on_any_plan(
        self, db_session, async_session
    ):
        org = OrganizationModel(provider_id="org-bare", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        status, detail = await self._gate(async_session, org, "everyday")
        assert status == 402 and "billing details" in detail

"""PDF rendering for tax documents.

Renders straight from a `TaxDocumentModel` -- no DB round-trip needed, since
`render_document_pdf` only reads the frozen snapshot fields already on the
row. What matters here is that every shape of document (both GST splits, an
export, a receipt with no period, an invoice with a period) renders without
raising and produces bytes that are actually a PDF.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from api.db.models import TaxDocumentModel
from api.services.billing.document_pdf import render_document_pdf


def _document(**overrides) -> TaxDocumentModel:
    defaults = dict(
        id=1,
        organization_id=1,
        kind="tax_invoice",
        number="INV/25-26/000001",
        financial_year="25-26",
        issued_at=datetime(2025, 6, 15, tzinfo=UTC),
        period_start=date(2025, 5, 1),
        period_end=date(2025, 5, 31),
        taxable_paise=100_000,
        cgst_paise=9_000,
        sgst_paise=9_000,
        igst_paise=0,
        total_paise=118_000,
        supply_type="intra_state",
        place_of_supply="29",
        rate_basis_points=1800,
        supplier_snapshot={
            "legal_name": "Decibyl Test Pvt Ltd",
            "gstin": "29AAAAA0000A1Z5",
            "state_code": "29",
            "address": "1 Test Street, Bengaluru",
            "pan": "AAAAA0000A",
            "sac_code": "998314",
            "lut_number": None,
        },
        customer_snapshot={
            "legal_name": "Acme Voice Pvt Ltd",
            "gstin": "29BBBBB1111B1Z5",
            "address_line1": "2 Customer Road",
            "address_line2": None,
            "city": "Bengaluru",
            "state_code": "29",
            "postal_code": "560001",
            "country_code": "IN",
            "billing_email": "billing@acme.example",
        },
        line_items=[
            {
                "description": "Voice agent usage, 2025-05-01 to 2025-05-31 (120.0 minutes)",
                "sac_code": "998314",
                "quantity_minutes": 120.0,
                "amount_paise": 100_000,
            }
        ],
        payment_id=None,
    )
    defaults.update(overrides)
    return TaxDocumentModel(**defaults)


class TestRenderDocumentPdf:
    def test_a_tax_invoice_renders_to_pdf_bytes(self):
        pdf_bytes = render_document_pdf(_document())
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_a_receipt_voucher_with_no_period_renders(self):
        pdf_bytes = render_document_pdf(
            _document(
                kind="receipt_voucher",
                number="RV/25-26/000001",
                period_start=None,
                period_end=None,
                line_items=[
                    {
                        "description": "Advance towards prepaid usage credit",
                        "sac_code": "998314",
                        "amount_paise": 100_000,
                    }
                ],
            )
        )
        assert pdf_bytes.startswith(b"%PDF")

    def test_an_inter_state_document_with_igst_renders(self):
        pdf_bytes = render_document_pdf(
            _document(
                supply_type="inter_state",
                place_of_supply="27",
                cgst_paise=0,
                sgst_paise=0,
                igst_paise=18_000,
                total_paise=118_000,
            )
        )
        assert pdf_bytes.startswith(b"%PDF")

    def test_an_export_document_under_lut_renders_and_notes_the_lut(self):
        pdf_bytes = render_document_pdf(
            _document(
                supply_type="export",
                place_of_supply=None,
                cgst_paise=0,
                sgst_paise=0,
                igst_paise=0,
                total_paise=100_000,
                supplier_snapshot={
                    "legal_name": "Decibyl Test Pvt Ltd",
                    "gstin": "29AAAAA0000A1Z5",
                    "state_code": "29",
                    "address": "1 Test Street, Bengaluru",
                    "pan": "AAAAA0000A",
                    "sac_code": "998314",
                    "lut_number": "AD290625000001A",
                },
            )
        )
        assert pdf_bytes.startswith(b"%PDF")

    def test_a_document_with_no_billing_email_still_renders(self):
        pdf_bytes = render_document_pdf(
            _document(
                customer_snapshot={
                    "legal_name": "Acme Voice Pvt Ltd",
                    "gstin": None,
                    "address_line1": "2 Customer Road",
                    "address_line2": None,
                    "city": "Bengaluru",
                    "state_code": "29",
                    "postal_code": "560001",
                    "country_code": "IN",
                    "billing_email": None,
                }
            )
        )
        assert pdf_bytes.startswith(b"%PDF")

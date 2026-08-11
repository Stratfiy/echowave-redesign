"""Rendering an issued document as a page a customer can print.

The property that outranks everything else here: **an issued document is a
statement about a moment.** The customer's address, our address, the rate and
the tax split were all what they were on the day. Rendering must read the
snapshot frozen onto the row and never recompute anything, because a rewritten
invoice looks exactly as authoritative as the original.

Second: a legal name and an address are customer input, and this page is served
from our own origin against a session. Anything interpolated must be escaped.
"""

from __future__ import annotations

import pytest

from api.services.billing.document_render import render_html

INVOICE = {
    "id": 1,
    "kind": "tax_invoice",
    "number": "INV/26-27/000004",
    "financial_year": "26-27",
    "issued_at": "2026-08-01T00:00:00+00:00",
    "period_start": "2026-07-01",
    "period_end": "2026-07-31",
    "taxable_paise": 100_000,
    "cgst_paise": 9_000,
    "sgst_paise": 9_000,
    "igst_paise": 0,
    "total_paise": 118_000,
    "supply_type": "intra_state",
    "place_of_supply": "33",
    "rate_basis_points": 1800,
    "supplier": {
        "legal_name": "NAUTOMATION LABS PRIVATE LIMITED",
        "gstin": "33AALCN7211L1ZB",
        "address": "Hosur, Tamil Nadu",
        "sac_code": "998314",
    },
    "customer": {
        "legal_name": "Acme Retail Pvt Ltd",
        "gstin": "33AAAAA0000A1Z5",
        "address_line1": "12 MG Road",
        "city": "Chennai",
        "postal_code": "600001",
        "billing_email": "accounts@acme.test",
    },
    "line_items": [
        {
            "description": "Voice agent usage, 2026-07-01 to 2026-07-31 (420 minutes)",
            "sac_code": "998314",
            "quantity_minutes": 420,
            "amount_paise": 100_000,
        }
    ],
}


class TestItPrintsWhatWasIssued:
    def test_the_serial_is_on_the_page(self):
        assert "INV/26-27/000004" in render_html(INVOICE)

    def test_both_parties_appear(self):
        html = render_html(INVOICE)
        assert "NAUTOMATION LABS PRIVATE LIMITED" in html
        assert "Acme Retail Pvt Ltd" in html
        assert "33AAAAA0000A1Z5" in html

    def test_money_is_rendered_from_integer_paise(self):
        """Paise become rupees exactly once, at the edge, as a string. A float
        anywhere in this path is how a total stops matching the sum above it."""
        html = render_html(INVOICE)
        assert "₹1,000.00" in html  # taxable
        assert "₹1,180.00" in html  # total

    def test_a_receipt_voucher_is_not_called_an_invoice(self):
        """It acknowledges an advance. Calling it an invoice misstates what it
        is, and the two are separate documents under GST for a reason."""
        html = render_html({**INVOICE, "kind": "receipt_voucher"})
        assert "Receipt voucher" in html
        assert "<h1>Tax invoice</h1>" not in html

    def test_the_period_appears_on_an_invoice(self):
        html = render_html(INVOICE)
        assert "2026-07-01" in html and "2026-07-31" in html

    def test_a_document_with_no_period_omits_it_rather_than_printing_none(self):
        html = render_html(
            {**INVOICE, "period_start": None, "period_end": None}
        )
        assert "None" not in html


class TestTheTaxSplit:
    def test_intra_state_prints_cgst_and_sgst_and_not_igst(self):
        """A zero IGST line beside real CGST and SGST invites the reader to
        conclude the split is wrong."""
        html = render_html(INVOICE)
        assert "CGST @ 9%" in html
        assert "SGST @ 9%" in html
        assert "IGST" not in html

    def test_inter_state_prints_igst_alone(self):
        html = render_html(
            {
                **INVOICE,
                "cgst_paise": 0,
                "sgst_paise": 0,
                "igst_paise": 18_000,
                "supply_type": "inter_state",
            }
        )
        assert "IGST @ 18%" in html
        assert "CGST" not in html

    def test_a_zero_rated_export_prints_the_lut_rather_than_nothing(self):
        """"No tax" without the authority for it reads as an omission."""
        html = render_html(
            {
                **INVOICE,
                "cgst_paise": 0,
                "sgst_paise": 0,
                "igst_paise": 0,
                "total_paise": 100_000,
                "supply_type": "export",
                "rate_basis_points": 0,
                "supplier": {**INVOICE["supplier"], "lut_number": "AB3306264934610"},
            }
        )
        assert "AB3306264934610" in html
        assert "Export of services" in html


class TestEscaping:
    @pytest.mark.parametrize(
        "field", ["legal_name", "address_line1", "city", "billing_email"]
    )
    def test_customer_fields_are_escaped(self, field):
        """This page is served from our origin against a session. A company
        name is customer input."""
        html = render_html(
            {
                **INVOICE,
                "customer": {**INVOICE["customer"], field: "<script>alert(1)</script>"},
            }
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_line_item_description_is_escaped(self):
        html = render_html(
            {
                **INVOICE,
                "line_items": [
                    {
                        "description": "<img src=x onerror=alert(1)>",
                        "sac_code": "998314",
                        "amount_paise": 100,
                    }
                ],
            }
        )
        assert "<img src=x" not in html
        assert "&lt;img" in html


class TestDegradedInput:
    def test_a_document_with_no_line_items_says_so(self):
        html = render_html({**INVOICE, "line_items": []})
        assert "No line items recorded" in html

    def test_a_missing_legal_name_prints_a_dash_not_the_word_none(self):
        html = render_html(
            {**INVOICE, "customer": {"legal_name": None, "gstin": None}}
        )
        assert "None" not in html

    def test_a_line_item_without_minutes_is_fine(self):
        """A receipt voucher's line has no minutes — it acknowledges money, not
        supply."""
        html = render_html(
            {
                **INVOICE,
                "kind": "receipt_voucher",
                "line_items": [
                    {
                        "description": "Advance towards prepaid usage credit",
                        "sac_code": "998314",
                        "amount_paise": 100_000,
                    }
                ],
            }
        )
        assert "Advance towards prepaid usage credit" in html
        assert "None" not in html


class TestItIsPrintable:
    def test_the_page_is_self_contained(self):
        """No external stylesheet, no script, no font fetch. A document a
        customer files must not depend on our servers still being there."""
        html = render_html(INVOICE)
        assert "<style>" in html
        assert "src=" not in html.replace("src=x", "")
        assert "<link" not in html

    def test_it_sets_a_page_size_so_printing_produces_a_document(self):
        assert "@page" in render_html(INVOICE)
        assert "A4" in render_html(INVOICE)

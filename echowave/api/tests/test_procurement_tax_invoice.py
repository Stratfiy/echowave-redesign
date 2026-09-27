"""A tax invoice, local or export under LUT, in the invoice's currency
(KAN-159, slice 3).

Rule 46 of the CGST Rules says what an invoice must carry; an export under a
Letter of Undertaking adds the endorsement, the LUT reference, the country
of destination and the INR equivalent of a foreign-currency value. The
engine does not invent a layout for those -- the template decides where
they print -- but it derives what can be derived, refuses what is wrong,
and tells the person which particulars their template has no place for.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from api.services.documents import formats, money, tools


class TestMoneyInACurrency:
    def test_inr_is_unchanged(self):
        assert money.format_amount("222171.2", "INR") == "2,22,171.20"
        assert (
            money.amount_in_words("316", "INR") == "Rupees Three Hundred Sixteen Only"
        )

    def test_dollars_group_in_thousands_and_say_dollars(self):
        assert money.format_amount("1234567.5", "USD") == "1,234,567.50"
        assert (
            money.amount_in_words("316", "USD") == "Dollars Three Hundred Sixteen Only"
        )
        assert (
            money.amount_in_words("316.25", "USD")
            == "Dollars Three Hundred Sixteen and Twenty-Five Cents Only"
        )

    def test_pounds_and_euros_have_their_own_minor_units(self):
        assert money.amount_in_words("1.05", "GBP") == "Pounds One and Five Pence Only"
        assert money.amount_in_words("2", "EUR") == "Euros Two Only"

    def test_an_unknown_currency_is_refused_by_name(self):
        with pytest.raises(money.MoneyError) as exc:
            money.format_amount("1", "XYZ")
        assert "XYZ" in str(exc.value) and "USD" in str(exc.value)


def _export_template() -> dict:
    return {
        "fields": [
            "supplier_name",
            "supplier_address",
            "supplier_gstin",
            "signatory_name",
            "recipient_name",
            "recipient_address",
            "country_of_destination",
            "place_of_supply",
            "document_number",
            "document_date",
            "lut_arn",
            "export_declaration",
            "subtotal",
            "igst",
            "total",
            "amount_in_words",
            "exchange_rate",
            "inr_equivalent",
        ],
        "item_columns": ["description", "hsn_sac", "qty", "rate", "amount"],
    }


def _export_values(**extra) -> dict:
    return {
        "supplier_name": "Nautomation Labs Private Limited",
        "supplier_address": "No.86/16 Papanna Thottam, Hosur 635109",
        "supplier_gstin": "33AALCN7211L1ZB",
        "signatory_name": "Nithish Kalyan",
        "recipient_name": "Copy Hero LLC",
        "recipient_address": "16192 Coastal Hgw, Lewes, DE 19958",
        "country_of_destination": "United States",
        "lut_arn": "AD330726040304D",
        "currency": "USD",
        "exchange_rate": "83.50",
        "supply": "export_lut",
        **extra,
    }


def _lines() -> list[dict]:
    return [
        {"description": "Service fee", "hsn_sac": "998314", "qty": 1, "rate": 250},
        {
            "description": "Hostinger - 1 month",
            "hsn_sac": "998315",
            "qty": 3,
            "rate": 22,
        },
    ]


class TestAnExportUnderLut:
    def test_no_tax_the_endorsement_the_place_and_the_inr_equivalent(self):
        prepared = tools.prepare(
            "tax_invoice", _export_template(), _export_values(), _lines()
        )
        assert prepared.errors == [] and prepared.missing == [], prepared
        v = prepared.values
        assert v["subtotal"] == "316.00"
        assert v["igst"] == "0.00"
        assert v["total"] == "316.00"
        assert v["amount_in_words"] == "Dollars Three Hundred Sixteen Only"
        assert v["export_declaration"] == formats.EXPORT_DECLARATION
        assert v["place_of_supply"] == "Outside India"
        assert v["exchange_rate"] == "83.50"
        assert v["inr_equivalent"] == "26,386.00"
        assert prepared.total == Decimal("316.00")
        assert prepared.currency == "USD"
        assert prepared.inr_total == Decimal("26386.00")
        assert [i["amount"] for i in prepared.items] == ["250.00", "66.00"]

    def test_a_foreign_currency_needs_the_rate(self):
        values = _export_values()
        values.pop("exchange_rate")
        prepared = tools.prepare("tax_invoice", _export_template(), values, _lines())
        assert [m["field"] for m in prepared.missing] == ["exchange_rate"]
        assert "rate" in prepared.missing[0]["question"].lower()

    def test_a_gst_rate_on_an_export_line_is_ignored_with_a_note(self):
        lines = _lines()
        lines[0]["gst_rate"] = 18
        prepared = tools.prepare(
            "tax_invoice", _export_template(), _export_values(), lines
        )
        assert prepared.values["igst"] == "0.00"
        assert any("without payment" in n.lower() for n in prepared.notes)

    def test_an_unknown_supply_mode_is_an_error(self):
        prepared = tools.prepare(
            "tax_invoice",
            _export_template(),
            _export_values(supply="abroad"),
            _lines(),
        )
        assert any("supply" in e for e in prepared.errors)


class TestALocalInvoice:
    def test_tax_splits_by_state_as_before(self):
        template = {
            "fields": [
                "supplier_gstin",
                "recipient_gstin",
                "cgst",
                "sgst",
                "igst",
                "total",
            ],
            "item_columns": ["description", "qty", "rate", "gst_rate", "amount"],
        }
        values = {
            "supplier_gstin": "33AALCN7211L1ZB",
            "recipient_gstin": money.with_checksum("33AAACR5055K1Z"),
        }
        prepared = tools.prepare(
            "tax_invoice",
            template,
            values,
            [{"description": "x", "qty": 1, "rate": 100, "gst_rate": 18}],
        )
        assert prepared.errors == [], prepared.errors
        assert (
            prepared.values["cgst"],
            prepared.values["sgst"],
            prepared.values["igst"],
        ) == (
            "9.00",
            "9.00",
            "0.00",
        )
        assert prepared.values["total"] == "118.00"
        assert prepared.currency == "INR"

    def test_a_purchase_order_is_untouched_by_the_new_fields(self):
        prepared = tools.prepare(
            "purchase_order",
            {
                "fields": ["vendor_name", "total"],
                "item_columns": ["description", "qty", "rate"],
            },
            {"vendor_name": "X"},
            [{"description": "x", "qty": 2, "rate": 10}],
        )
        assert prepared.values["total"] == "20.00"
        assert prepared.currency == "INR"
        assert "export_declaration" not in prepared.values


class TestWhatTheTemplateHasNoPlaceFor:
    def test_an_export_template_missing_particulars_is_told_which(self):
        template = _export_template()
        for gone in ("lut_arn", "country_of_destination", "inr_equivalent"):
            template["fields"].remove(gone)
        gaps = tools.compliance_gaps("tax_invoice", template, supply="export_lut")
        assert gaps == ["country_of_destination", "inr_equivalent", "lut_arn"]

    def test_a_local_template_is_measured_against_rule_46(self):
        gaps = tools.compliance_gaps(
            "tax_invoice",
            {"fields": ["supplier_gstin", "total"], "item_columns": ["description"]},
            supply="local",
        )
        assert "hsn_sac" in gaps and "place_of_supply" in gaps and "tax" in gaps
        assert "lut_arn" not in gaps
        assert "supplier_name" not in gaps  # letterhead, not a field

    def test_a_sheet_with_formulas_is_trusted_for_its_own_arithmetic(self):
        gaps = tools.compliance_gaps(
            "tax_invoice",
            {
                "fields": [
                    "recipient_name",
                    "recipient_address",
                    "place_of_supply",
                    "document_number",
                    "document_date",
                ],
                "item_columns": ["description", "hsn_sac", "qty", "rate"],
                "has_formulas": True,
            },
            supply="local",
        )
        assert gaps == []

    def test_other_kinds_have_no_rule_to_measure_against(self):
        assert (
            tools.compliance_gaps("purchase_order", {"fields": []}, supply="local")
            == []
        )

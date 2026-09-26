"""Money on a purchase order: paise, the GST split, and the words.

A PO is a legal document the vendor invoices against. A total that is a
paisa off, CGST and SGST charged on an inter-state supply, or an amount in
words that disagrees with the figure are each a letter back from the vendor
and a PO re-issued -- so every figure here is Decimal, rounded half-up to the
paisa, and tested against the numbers a person would work out by hand.
"""

from decimal import Decimal

import pytest

from api.services.documents import money

# Two well-formed GSTINs with valid checksums: Karnataka (29) and Maharashtra (27).
KA_BUYER = money.with_checksum("29AABCT1332L1Z")
KA_VENDOR = money.with_checksum("29AAGCB7383J1Z")
MH_VENDOR = money.with_checksum("27AAPFU0939F1Z")


class TestRounding:
    def test_half_a_paisa_rounds_up(self):
        assert money.to_paise(Decimal("10.005")) == Decimal("10.01")
        assert money.to_paise(Decimal("10.004")) == Decimal("10.00")

    def test_banker_rounding_is_not_used(self):
        # ROUND_HALF_EVEN would give 0.02 here; a PO rounds half up.
        assert money.to_paise(Decimal("0.025")) == Decimal("0.03")

    def test_strings_and_numbers_parse(self):
        assert money.dec("1,23,456.50") == Decimal("123456.50")
        assert money.dec("₹ 1,000") == Decimal(1000)
        assert money.dec(12.5) == Decimal("12.5")

    def test_nonsense_does_not_parse(self):
        with pytest.raises(money.MoneyError):
            money.dec("twelve")


class TestIndianGrouping:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0", "0.00"),
            ("5", "5.00"),
            ("999", "999.00"),
            ("1000", "1,000.00"),
            ("99999.5", "99,999.50"),
            ("100000", "1,00,000.00"),
            ("1234567", "12,34,567.00"),
            ("123456789.456", "12,34,56,789.46"),
            ("-1234.5", "-1,234.50"),
        ],
    )
    def test_lakh_and_crore_grouping(self, value, expected):
        assert money.format_inr(Decimal(value)) == expected


class TestAmountInWords:
    def test_the_worked_example(self):
        assert money.amount_in_words(Decimal("12345678.50")) == (
            "Rupees One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred "
            "Seventy-Eight and Fifty Paise Only"
        )

    def test_whole_rupees_have_no_paise_clause(self):
        assert money.amount_in_words(Decimal(100)) == "Rupees One Hundred Only"

    def test_small_and_teen_numbers(self):
        assert money.amount_in_words(Decimal(15)) == "Rupees Fifteen Only"
        assert money.amount_in_words(Decimal("0.05")) == (
            "Rupees Zero and Five Paise Only"
        )

    def test_exact_lakh_and_crore(self):
        assert money.amount_in_words(Decimal(100000)) == "Rupees One Lakh Only"
        assert money.amount_in_words(Decimal(20000000)) == "Rupees Two Crore Only"

    def test_hundreds_of_crores(self):
        assert money.amount_in_words(Decimal(1500000000)) == (
            "Rupees One Hundred Fifty Crore Only"
        )

    def test_paise_are_rounded_first(self):
        assert money.amount_in_words(Decimal("1.999")) == "Rupees Two Only"


class TestGstin:
    def test_a_valid_gstin_passes(self):
        assert money.validate_gstin(KA_BUYER) == KA_BUYER
        # Lower case and spaces are a person typing, not a different GSTIN.
        assert money.validate_gstin(" " + KA_BUYER.lower() + " ") == KA_BUYER

    def test_a_published_example_has_the_right_checksum(self):
        assert money.validate_gstin("27AAPFU0939F1ZV") == "27AAPFU0939F1ZV"

    def test_a_wrong_checksum_is_refused(self):
        wrong = KA_BUYER[:-1] + ("A" if KA_BUYER[-1] != "A" else "B")
        with pytest.raises(money.MoneyError, match="check"):
            money.validate_gstin(wrong)

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "29AABCT1332L1Z",
            "29AABCT1332L1ZZZ",
            "AAAABCT1332L1Z5",
            "29AABCT1332L1X5",
        ],
    )
    def test_a_malformed_gstin_is_refused(self, bad):
        with pytest.raises(money.MoneyError):
            money.validate_gstin(bad)

    def test_an_unknown_state_code_is_refused(self):
        with pytest.raises(money.MoneyError, match="state"):
            money.validate_gstin(money.with_checksum("45AABCT1332L1Z"))

    def test_state_code(self):
        assert money.state_code(KA_BUYER) == "29"
        assert money.state_code(MH_VENDOR) == "27"

    def test_pan(self):
        assert money.validate_pan("aagcb7383j") == "AAGCB7383J"
        with pytest.raises(money.MoneyError):
            money.validate_pan("AAGCB73831")


def _items():
    return [
        {"description": "A", "qty": 10, "rate": "100", "gst_rate": 18},
        {
            "description": "B",
            "qty": "3",
            "rate": "333.33",
            "discount": 10,
            "gst_rate": 12,
        },
    ]


class TestTheSplit:
    def test_same_state_is_cgst_and_sgst(self):
        result = money.compute(_items(), buyer_gstin=KA_BUYER, vendor_gstin=KA_VENDOR)
        assert result.supply == "intra_state"
        # A: 1000.00 taxable, 90 + 90. B: 999.99 less 10% = 899.99 (899.991),
        # 6% each = 54.00 (53.9994).
        a, b = result.lines
        assert a.taxable_value == Decimal("1000.00")
        assert (a.cgst, a.sgst, a.igst) == (
            Decimal("90.00"),
            Decimal("90.00"),
            Decimal("0.00"),
        )
        assert b.taxable_value == Decimal("899.99")
        assert b.cgst == Decimal("54.00") and b.sgst == Decimal("54.00")
        assert result.subtotal == Decimal("1899.99")
        assert result.cgst == Decimal("144.00")
        assert result.sgst == Decimal("144.00")
        assert result.igst == Decimal("0.00")
        assert result.total == Decimal("2187.99")

    def test_different_states_is_igst(self):
        result = money.compute(_items(), buyer_gstin=KA_BUYER, vendor_gstin=MH_VENDOR)
        assert result.supply == "inter_state"
        a, b = result.lines
        assert a.igst == Decimal("180.00") and a.cgst == Decimal("0.00")
        assert b.igst == Decimal("108.00")
        assert result.igst == Decimal("288.00")
        assert result.cgst == result.sgst == Decimal("0.00")
        assert result.total == Decimal("2187.99")

    def test_every_line_adds_up(self):
        result = money.compute(_items(), buyer_gstin=KA_BUYER, vendor_gstin=MH_VENDOR)
        for line in result.lines:
            assert line.amount == line.taxable_value + line.gst_amount
        assert result.total == result.subtotal + result.cgst + result.sgst + result.igst

    def test_an_unregistered_vendor_takes_the_stated_state(self):
        result = money.compute(
            _items(),
            buyer_gstin=KA_BUYER,
            vendor_gstin=None,
            vendor_state_code="27",
        )
        assert result.supply == "inter_state"

    def test_negative_quantity_or_rate_is_refused(self):
        with pytest.raises(money.MoneyError, match="negative"):
            money.compute(
                [{"qty": -1, "rate": 10, "gst_rate": 18}],
                buyer_gstin=KA_BUYER,
                vendor_gstin=KA_VENDOR,
            )
        with pytest.raises(money.MoneyError, match="negative"):
            money.compute(
                [{"qty": 1, "rate": "-10", "gst_rate": 18}],
                buyer_gstin=KA_BUYER,
                vendor_gstin=KA_VENDOR,
            )

    def test_a_discount_over_a_hundred_percent_is_refused(self):
        with pytest.raises(money.MoneyError, match="discount"):
            money.compute(
                [{"qty": 1, "rate": 10, "gst_rate": 18, "discount": 120}],
                buyer_gstin=KA_BUYER,
                vendor_gstin=KA_VENDOR,
            )

    def test_totals_in_paise(self):
        result = money.compute(_items(), buyer_gstin=KA_BUYER, vendor_gstin=KA_VENDOR)
        assert result.total_paise == 218799

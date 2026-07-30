"""GST arithmetic and place of supply.

Two kinds of failure are worth testing for here. The loud one is charging the
wrong total — 18% missing from a top-up is most of the margin on a $0.02 minute.
The quiet one is an invoice whose CGST and SGST do not sum to its own tax line,
which nobody notices until a tax officer does.
"""

import pytest

from api.services.billing import tax
from api.services.billing.tax import TaxError, compute_tax

# Karnataka, which is what the tests treat as "our" state.
OUR_STATE = "29"


@pytest.fixture(autouse=True)
def supplier_in_karnataka(monkeypatch):
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(tax, "GST_RATE_BASIS_POINTS", 1800)
    monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)


class TestPlaceOfSupply:
    def test_a_customer_in_our_state_pays_cgst_and_sgst(self):
        """Two taxes to two governments, not one tax by another name."""
        result = compute_tax(
            taxable_paise=100_000, country_code="IN", state_code=OUR_STATE
        )

        assert result.supply_type == "intra_state"
        assert result.cgst_paise == 9_000
        assert result.sgst_paise == 9_000
        assert result.igst_paise == 0
        assert result.total_paise == 118_000

    def test_a_customer_in_another_state_pays_igst(self):
        result = compute_tax(
            taxable_paise=100_000,
            country_code="IN",
            state_code="27",  # Maharashtra
        )

        assert result.supply_type == "inter_state"
        assert result.igst_paise == 18_000
        assert result.cgst_paise == 0
        assert result.sgst_paise == 0
        assert result.total_paise == 118_000

    def test_both_domestic_cases_cost_the_customer_the_same(self):
        """Only the split differs. A customer must never be able to tell which
        state we are registered in from their total."""
        ours = compute_tax(
            taxable_paise=73_331, country_code="IN", state_code=OUR_STATE
        )
        theirs = compute_tax(taxable_paise=73_331, country_code="IN", state_code="07")

        assert ours.total_paise == theirs.total_paise

    def test_a_foreign_customer_is_zero_rated(self):
        result = compute_tax(taxable_paise=100_000, country_code="US", state_code=None)

        assert result.supply_type == "export"
        assert result.is_zero_rated
        assert result.tax_paise == 0
        assert result.total_paise == 100_000
        # Recorded as 0%, not as 18% that happened not to apply — the invoice
        # has to state the rate it was issued under.
        assert result.rate_basis_points == 0

    def test_a_missing_country_is_treated_as_domestic(self):
        """The conservative direction. Charging GST where none was due is a
        refund; failing to charge it where it was due is our liability."""
        result = compute_tax(taxable_paise=100_000, country_code=None, state_code=None)

        assert result.supply_type == "intra_state"
        assert result.tax_paise == 18_000

    def test_a_missing_state_falls_back_to_our_own(self):
        result = compute_tax(taxable_paise=100_000, country_code="IN", state_code=None)

        assert result.supply_type == "intra_state"
        assert result.place_of_supply == OUR_STATE
        assert result.total_paise == 118_000


class TestExportsNeedAnLut:
    def test_an_export_without_an_lut_is_refused(self, monkeypatch):
        """Zero-rating without an LUT understates a liability that is ours, not
        the customer's. Fixed by filing the LUT, not by code."""
        monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", False)

        with pytest.raises(TaxError, match="LUT"):
            compute_tax(taxable_paise=100_000, country_code="US", state_code=None)

    def test_domestic_supply_is_unaffected_by_the_lut(self, monkeypatch):
        monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", False)

        result = compute_tax(
            taxable_paise=100_000, country_code="IN", state_code=OUR_STATE
        )

        assert result.total_paise == 118_000


class TestTheArithmeticAddsUp:
    @pytest.mark.parametrize(
        "taxable",
        [1, 7, 99, 100, 333, 1_001, 12_345, 99_999, 100_000, 4_999_999],
    )
    def test_components_always_sum_to_the_total(self, taxable):
        """An invoice whose parts do not sum to its total is one somebody will
        ask about. Includes odd amounts, where halving the tax is lossy."""
        for state in (OUR_STATE, "27"):
            result = compute_tax(
                taxable_paise=taxable, country_code="IN", state_code=state
            )
            assert result.taxable_paise + result.tax_paise == result.total_paise, (
                f"{taxable} in state {state}"
            )
            assert (
                result.cgst_paise + result.sgst_paise + result.igst_paise
                == result.tax_paise
            )

    def test_an_odd_tax_is_split_without_losing_a_paise(self):
        """₹3.33 attracts 59.94 paise of tax, which does not halve evenly."""
        result = compute_tax(taxable_paise=333, country_code="IN", state_code=OUR_STATE)

        assert result.tax_paise == 60
        assert result.cgst_paise == 30
        assert result.sgst_paise == 30
        assert result.total_paise == 393

    def test_a_tax_that_halves_unevenly_still_reconciles(self):
        """The two halves must sum to the tax exactly, even when it is odd."""
        result = compute_tax(taxable_paise=5, country_code="IN", state_code=OUR_STATE)

        assert result.cgst_paise + result.sgst_paise == result.tax_paise

    def test_the_split_is_never_more_than_a_paise_apart(self):
        for taxable in range(1, 500):
            result = compute_tax(
                taxable_paise=taxable, country_code="IN", state_code=OUR_STATE
            )
            assert abs(result.cgst_paise - result.sgst_paise) <= 1

    def test_zero_is_taxed_as_zero(self):
        result = compute_tax(taxable_paise=0, country_code="IN", state_code=OUR_STATE)

        assert result.total_paise == 0
        assert result.tax_paise == 0

    def test_a_negative_amount_is_refused(self):
        with pytest.raises(TaxError):
            compute_tax(taxable_paise=-1, country_code="IN", state_code=OUR_STATE)


class TestGrossingUpAPayment:
    def test_a_thousand_rupee_top_up_charges_eleven_eighty(self):
        """The number the customer sees on their card statement."""
        assert (
            tax.gross_up(taxable_paise=100_000, country_code="IN", state_code="27")
            == 118_000
        )

    def test_an_export_top_up_charges_the_face_value(self):
        assert (
            tax.gross_up(taxable_paise=100_000, country_code="US", state_code=None)
            == 100_000
        )

    def test_grossing_up_agrees_with_the_breakdown(self):
        for taxable in (10_000, 50_000, 123_457):
            breakdown = compute_tax(
                taxable_paise=taxable, country_code="IN", state_code=OUR_STATE
            )
            assert (
                tax.gross_up(
                    taxable_paise=taxable, country_code="IN", state_code=OUR_STATE
                )
                == breakdown.total_paise
            )


class TestGstin:
    def test_a_well_formed_gstin_is_accepted_and_normalised(self):
        assert tax.validate_gstin(" 29abcde1234f1z5 ") == "29ABCDE1234F1Z5"

    def test_the_state_code_is_read_off_the_gstin(self):
        assert tax.state_code_from_gstin("29ABCDE1234F1Z5") == "29"
        assert tax.state_code_from_gstin("not-a-gstin") is None

    def test_a_gstin_disagreeing_with_the_address_is_refused(self):
        """A wrong state code silently changes which tax we charge, so this is
        the one inconsistency worth blocking on."""
        with pytest.raises(TaxError, match="state"):
            tax.validate_gstin("29ABCDE1234F1Z5", state_code="27")

    def test_a_gstin_agreeing_with_the_address_is_accepted(self):
        assert (
            tax.validate_gstin("29ABCDE1234F1Z5", state_code="29") == "29ABCDE1234F1Z5"
        )

    def test_a_short_gstin_is_refused(self):
        with pytest.raises(TaxError):
            tax.validate_gstin("29ABCDE")

    def test_no_gstin_is_allowed(self):
        """Unregistered customers exist and are still billed, just without one
        on the invoice."""
        assert tax.validate_gstin(None) is None
        assert tax.validate_gstin("") is None

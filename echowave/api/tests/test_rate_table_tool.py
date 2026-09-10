"""Quoting a price from a card, rather than reading it off a prompt.

The defect this replaces is not hypothetical and not a small-model problem. Two
different models, an hour apart, quoted the same rate card wrong in the same
way: they found the right row and slid a column. One priced a parcel to Dubai
from the Zone 4 column when Dubai is Zone 1; the other did it on the documents
table and quoted 1,202 where the card says 901. Neither transcript looks wrong.
A caller would have paid it.

So the tests that matter here are not "does it find a number". They are:

* **never a nearby cell.** A destination the card does not list, a weight past
  the last row, a column that is missing -- each refuses. A near miss is the
  failure mode being removed, so returning one would be worse than nothing.
* **never rounds down.** Rounding down under-quotes, and an under-quote is the
  error a customer only discovers on the invoice.
* **the card's own crossover rules are data, not memory.** "Documents over 2 kg
  price as parcels" lived in a prompt and every model had to remember it on
  every call.

The grid below is invented. A real card belongs in an operator's own tool
configuration, not in this repository.
"""

import pytest

from api.services.workflow.tools.rate_table import (
    RateLookupError,
    get_rate_table_tools,
    lookup_rate,
    normalise,
    resolve_series,
)

CARD = {
    "currency": "INR",
    "default_variant": "parcel",
    "grids": {
        # Deliberately different numbers in every cell: a test whose grid
        # repeats values cannot tell a column slip from a correct answer.
        "document": {
            "0.5": {"1": 100, "2": 200, "3": 300},
            "1.0": {"1": 110, "2": 210, "3": 310},
            "2.0": {"1": 120, "2": 220, "3": 320},
        },
        "parcel": {
            "0.5": {"1": 500, "2": 600, "3": 700},
            "1.0": {"1": 510, "2": 610, "3": 710},
            "2.5": {"1": 525, "2": 625, "3": 725},
            "10.0": {"1": 590, "2": 690, "3": 790},
        },
    },
    "crossovers": {"document": {"above_band": 2.0, "use": "parcel"}},
    "overflow": {
        "from": 10.0,
        "tiers": [
            {"upto": 50, "per_unit": {"1": 20, "2": 30, "3": 40}},
            {"upto": None, "per_unit": {"1": 15, "2": 25, "3": 35}},
        ],
    },
    "aliases": {
        "United Arab Emirates": "1",
        "Dubai": "1",
        "UAE": "1",
        "Singapore": "2",
        "United Kingdom": "3",
    },
    "labels": {"function_name": "lookup_shipping_rate"},
}


class TestItReadsTheCellItWasAskedFor:
    @pytest.mark.parametrize(
        ("destination", "band", "variant", "expected"),
        [
            ("Dubai", 0.5, "document", 100),
            ("Singapore", 0.5, "document", 200),
            ("United Kingdom", 0.5, "document", 300),
            ("Dubai", 1.0, "parcel", 510),
            ("Singapore", 10.0, "parcel", 690),
        ],
    )
    def test_every_column_is_its_own_number(self, destination, band, variant, expected):
        """One assertion per column, because a column slip is the whole bug."""
        assert (
            lookup_rate(
                CARD, destination=destination, band=band, variant=variant
            ).amount
            == expected
        )

    def test_the_answer_carries_where_it_came_from(self):
        """Provenance, so a wrong quote can be traced without the card."""
        result = lookup_rate(CARD, destination="Dubai", band=0.5, variant="document")

        assert result.series == "1"
        assert result.band == 0.5
        assert result.variant == "document"
        assert result.currency == "INR"


class TestItNeverRoundsDown:
    @pytest.mark.parametrize(
        ("band", "expected_band", "expected_amount"),
        [(0.6, 1.0, 510), (1.01, 2.5, 525), (2.5, 2.5, 525), (9.9, 10.0, 590)],
    )
    def test_a_weight_between_rows_takes_the_higher_row(
        self, band, expected_band, expected_amount
    ):
        result = lookup_rate(CARD, destination="Dubai", band=band, variant="parcel")

        assert result.band == expected_band
        assert result.amount == expected_amount

    def test_rounding_up_is_said_out_loud(self):
        """The caller hears a price for a weight they did not give; the reason
        has to be available to the agent saying it."""
        result = lookup_rate(CARD, destination="Dubai", band=0.7, variant="parcel")

        assert any("Rounded up" in note for note in result.notes)

    def test_an_exact_band_says_nothing_about_rounding(self):
        assert (
            lookup_rate(CARD, destination="Dubai", band=1.0, variant="parcel").notes
            == ()
        )


class TestTheCardsOwnRulesAreData:
    def test_a_document_over_the_crossover_prices_as_a_parcel(self):
        """The rule every model had to remember on every call."""
        result = lookup_rate(CARD, destination="Dubai", band=2.5, variant="document")

        assert result.variant == "parcel"
        assert result.amount == 525
        assert any("priced as parcel" in note for note in result.notes)

    def test_under_the_crossover_the_document_grid_still_wins(self):
        assert (
            lookup_rate(CARD, destination="Dubai", band=2.0, variant="document").variant
            == "document"
        )

    def test_past_the_last_row_it_meters_per_unit(self):
        result = lookup_rate(CARD, destination="Dubai", band=20.0, variant="parcel")

        assert result.metered is True
        assert result.per_unit == 20
        assert result.amount == 400
        assert any("per unit" in note for note in result.notes)

    def test_the_right_tier_of_the_meter(self):
        assert (
            lookup_rate(
                CARD, destination="Dubai", band=100.0, variant="parcel"
            ).per_unit
            == 15
        )

    def test_no_variant_given_uses_the_stated_default(self):
        assert lookup_rate(CARD, destination="Dubai", band=0.5).variant == "parcel"


class TestItRefusesRatherThanGuessing:
    def test_a_destination_not_on_the_card(self):
        with pytest.raises(RateLookupError, match="not on this rate card"):
            lookup_rate(CARD, destination="Atlantis", band=1.0, variant="parcel")

    def test_no_destination_at_all(self):
        with pytest.raises(RateLookupError, match="No destination"):
            lookup_rate(CARD, destination="", band=1.0, variant="parcel")

    @pytest.mark.parametrize("band", [0, -1, "heavy", None])
    def test_a_weight_that_is_not_a_positive_number(self, band):
        with pytest.raises(RateLookupError):
            lookup_rate(CARD, destination="Dubai", band=band, variant="parcel")

    def test_a_kind_the_card_does_not_price(self):
        with pytest.raises(RateLookupError, match="not a kind"):
            lookup_rate(CARD, destination="Dubai", band=1.0, variant="livestock")

    def test_past_every_tier_when_there_is_no_meter(self):
        card = {**CARD}
        card.pop("overflow")
        with pytest.raises(RateLookupError, match="above the heaviest row"):
            lookup_rate(card, destination="Dubai", band=999.0, variant="parcel")

    def test_a_column_missing_from_the_row(self):
        card = {
            **CARD,
            "grids": {"parcel": {"1.0": {"1": 510}}},
            "aliases": {"Singapore": "2", "Dubai": "1"},
        }
        with pytest.raises(RateLookupError, match="no column"):
            lookup_rate(card, destination="Singapore", band=1.0, variant="parcel")


class TestItMatchesHowCallersSayThings:
    @pytest.mark.parametrize(
        "spoken", ["Dubai", "dubai", " DUBAI ", "UAE", "uae", "U.A.E.", "U A E"]
    )
    def test_one_destination_however_it_is_spelled(self, spoken):
        """Speech recognition renders an acronym half a dozen ways and a card
        nobody can hit from speech is a card nobody uses."""
        assert resolve_series(CARD, spoken) == "1"

    def test_a_bare_column_name_works_too(self):
        assert resolve_series(CARD, "2") == "2"

    def test_normalise_folds_punctuation_and_case(self):
        assert normalise("  United-Arab  Emirates! ") == "united arab emirates"


class TestTheFunctionTheModelSees:
    def test_it_is_named_by_the_operator(self):
        schema = get_rate_table_tools(CARD)[0]["function"]

        assert schema["name"] == "lookup_shipping_rate"

    def test_it_lists_the_kinds_this_card_prices(self):
        """A model inventing a variant name gets a refusal it could have
        avoided."""
        description = get_rate_table_tools(CARD)[0]["function"]["parameters"][
            "properties"
        ]["variant"]["description"]

        assert "document" in description
        assert "parcel" in description

    def test_destination_and_band_are_required_and_variant_is_not(self):
        schema = get_rate_table_tools(CARD)[0]["function"]

        assert schema["parameters"]["required"] == ["destination", "band"]

    def test_it_tells_the_model_not_to_quote_from_memory(self):
        assert "memory" in get_rate_table_tools(CARD)[0]["function"]["description"]

"""Stream `reach`: comparison across connected apps, and checked bills.

Comparison only ever uses the apps this person connected, says which were
compared and which were not (and why), and when. An app's bill becomes a
card only if it adds up.
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.services.reach.ordering import normalise
from api.services.workflow import decibyl
from api.tests.support.reach_fixtures import (  # noqa: F401
    acting_as,
    call,
    connect_zomato,
    fake_servers,
    people,
    reach_on,
)


@pytest.mark.asyncio
class TestCompare:
    async def test_it_says_which_apps_and_when(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        with acting_as(people.a.id):
            result = await decibyl._tool(
                people.org,
                call("compare_prices", items=["paneer tikka", "toned milk"]),
                people.a.id,
            )
        assert result["untrusted"] is True
        assert result["say"].startswith("Compared Zomato at ")
        assert (
            "Not compared: Swiggy (Swiggy needs Builders Club access" in result["say"]
        )
        assert "Only Zomato is connected" in result["say"]
        assert "Paneer Tikka" in result["data"] and "SAVE50" in result["data"]
        rows = await db_client.agent_events(
            organization_id=people.org,
            kinds=["reach_comparison"],
            limit=2,
            assistant_thread=True,
        )
        table = rows[0].payload
        assert [c["app"] for c in table["compared"]] == ["Zomato"]
        assert table["compared"][0]["listed_total_paise"] == 22000 + 5400
        assert table["not_compared"][0]["app"] == "Swiggy"
        assert table["at"]

    async def test_nothing_connected_compares_nothing(self, reach_on, people):
        with acting_as(people.b.id):
            result = await decibyl._tool(
                people.org, call("compare_prices", items=["milk"]), people.b.id
            )
        assert result["status"] == "not_available"
        assert "Zomato" in result["reason"] and "Swiggy" in result["reason"]

    async def test_a_colleagues_connection_is_never_compared(self, reach_on, people):
        await connect_zomato(people.org, people.a.id)
        with acting_as(people.b.id):
            result = await decibyl._tool(
                people.org, call("compare_prices", items=["paneer"]), people.b.id
            )
        assert result["status"] == "not_available"


def _bill(**overrides):
    bill = {
        "cart_id": "c1",
        "store": {"id": "r1", "name": "Spice Route"},
        "items": [
            {
                "item_id": "i1",
                "name": "Paneer",
                "quantity": 2,
                "unit_price": 220,
                "line_total": 440,
            }
        ],
        "charges": [{"label": "Delivery", "amount": 30}],
        "discount": 0,
        "total": 470,
        "address": {"id": "a1", "label": "Home", "line": "12 MG Road"},
        "payment": {"method": "upi", "label": "UPI"},
    }
    bill.update(overrides)
    return bill


ASKED = [{"item_id": "i1", "quantity": 2}]


class TestBills:
    def test_a_bill_that_adds_up_is_read_in_paise(self):
        quote = normalise.quote(_bill(), ASKED)
        assert quote["total_paise"] == 47000 and quote["subtotal_paise"] == 44000

    @pytest.mark.parametrize(
        "overrides, reason",
        [
            ({"total": 1}, "does not add up to its total"),
            (
                {
                    "items": [
                        {
                            "item_id": "i1",
                            "quantity": 2,
                            "unit_price": 220,
                            "line_total": 100,
                        }
                    ]
                },
                "does not add up",
            ),
            (
                {
                    "items": [
                        {
                            "item_id": "i1",
                            "quantity": 3,
                            "unit_price": 220,
                            "line_total": 660,
                        }
                    ],
                    "total": 690,
                },
                "does not match",
            ),
            (
                {
                    "items": [
                        {
                            "item_id": "i9",
                            "quantity": 2,
                            "unit_price": 220,
                            "line_total": 440,
                        }
                    ]
                },
                "does not match",
            ),
            ({"discount": -10, "total": 480}, "not one a card can show"),
            ({"address": {}}, "where it will be delivered"),
            ({"payment": {}}, "how it will be paid"),
            ({"items": []}, "empty cart"),
            (
                {"total": 60000, "charges": [{"label": "Delivery", "amount": 59560}]},
                "not one a card can show",
            ),
        ],
    )
    def test_a_bill_that_does_not_is_refused(self, overrides, reason):
        with pytest.raises(normalise.BadQuote, match=reason):
            normalise.quote(_bill(**overrides), ASKED)

    def test_a_card_number_in_a_payment_label_is_masked(self):
        quote = normalise.quote(
            _bill(payment={"method": "card", "label": "Card 4111111111111111"}), ASKED
        )
        assert "4111111111111111" not in quote["payment"]["label"]
        assert quote["payment"]["label"].endswith("1111")

    def test_rupees(self):
        assert normalise.rupees(55500) == "₹555"
        assert normalise.rupees(1234505) == "₹12,345.05"

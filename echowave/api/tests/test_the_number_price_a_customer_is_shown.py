"""The price on the buy-a-number screen has to come from the biller.

The screen carried its own figure — ₹349, in three places — while every
account was charged `NUMBER_RENTAL_PRICE_PAISE`, ₹559. A customer read ₹349,
authorised a standing instruction, and was billed ₹559 a month. The screen even
disagreed with itself: the authorised-mandate line rendered the real
`price_paise` off the API a few hundred pixels below the ₹349 copy.

`GET /billing/mandate` now serves `number_price_paise`, and it must serve it
from `rentals.next_number_price_paise` — the same resolver
`provisioning`/`mandates` price the actual charge with, and the only one that
knows a plan can sell extra numbers at its own rate
(`test_plan_entitlement_and_floor.py::test_the_extra_number_is_priced_by_the_plan`
covers that behaviour; this file covers the wire to the screen).

**This reads the handler's source, deliberately**, following
`test_money_that_moves_is_announced.py`. The route opens its own session through
`db_client`, so a fixture-scoped session cannot observe it, and a test that
stubbed the resolver would assert only that the stub was called. What actually
has to hold is narrow and structural: the handler reaches for the resolver, and
does not reach for the constant. A source check states exactly that.
"""

from __future__ import annotations

import inspect


def _handler_source() -> str:
    from api.routes import payments as payments_route

    return inspect.getsource(payments_route.get_mandate)


def _handler_code() -> str:
    """The handler with comments stripped.

    The comment in that handler names the old ₹559 constant while explaining
    the bug, and prose is not what these guard against.
    """
    return "\n".join(line.split("#", 1)[0] for line in _handler_source().splitlines())


class TestTheQuoteComesFromTheResolver:
    def test_the_handler_asks_the_resolver(self):
        assert "next_number_price_paise" in _handler_source()

    def test_the_response_carries_the_price(self):
        assert "number_price_paise" in _handler_source()

    def test_the_handler_does_not_read_the_constant_directly(self):
        """A plan can price extra numbers at its own rate.

        Reading `NUMBER_RENTAL_PRICE_PAISE` here would quote the platform price
        to an account whose plan sells the number cheaper — the same class of
        bug as the hardcoded ₹349, just one layer down and harder to see.
        """
        assert "NUMBER_RENTAL_PRICE_PAISE" not in _handler_code()

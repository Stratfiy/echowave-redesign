"""There is no checkout.

The founder decided on 9 Oct 2026 that no pricing is shown to users, and you
cannot charge someone you have not shown a price. So the ways of taking money
are closed, on the server, not just hidden in the app: plans, top-ups,
auto top-up, promo codes and autopay mandates answer ``410 Gone``, and the
services that would call Razorpay for an order, a subscription or a saved-card
charge refuse before they do.

What stays open, on purpose:

* the Razorpay webhook (``POST /billing/razorpay/webhook``), because refunds
  and events for anything already in flight still arrive;
* cancelling a mandate that exists (money stops, it never starts);
* the record of what was already paid: tax documents, credit notes, receipts
  and payment history (the "Documents" page, kept for legal retention).

``constants.CHECKOUT_OPEN`` is read at call time, so a test can open it
(``tests/conftest`` does, for the suite that exercises the old money paths)
and the guard test can assert it is closed.
"""

from __future__ import annotations

from fastapi import HTTPException

from api import constants

GONE_MESSAGE = (
    "Decibyl is provided without charge for now, so there is nothing to buy. "
    "Pricing will be announced with notice before any charge."
)


class CheckoutClosed(RuntimeError):
    """Something tried to take or arrange a payment while there is no checkout."""


def is_open() -> bool:
    return bool(constants.CHECKOUT_OPEN)


def assert_open(what: str) -> None:
    """Refuse, in a service, before any call to Razorpay is made."""
    if not is_open():
        raise CheckoutClosed(f"{what}: there is no checkout.")


async def gone() -> None:
    """Route dependency: 410 Gone for every checkout route.

    Runs before the handler's own auth and body validation, so a stale client
    or a bookmarked URL gets the same plain answer whoever it is.
    """
    if not is_open():
        raise HTTPException(status_code=410, detail=GONE_MESSAGE)

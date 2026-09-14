"""The credit: what a customer sees, and the one place its size is written.

One credit is fifty paise of composed (marked-up) cost. Decided 14 Sept
2026 (KAN-47, KAN-52). The ledger stays in paise -- every settlement,
reservation and refund is exact money -- and credits are derived at two
points only:

* **A charge is rounded up to whole credits, per event.** A call that
  composes to 1,730 paise is charged 1,750 paise, 35 credits. Up, so a
  balance never shows a fraction and the house never gives the rounding
  away; per event, never per month, so two customers with the same calls
  see the same deductions.
* **A balance is shown rounded down.** 1,749 paise of balance is 34
  credits on the screen: what the customer can spend, not what they nearly
  have.

Nothing else converts. A screen that wants credits asks these two
functions; a screen that wants rupees (the invoice, the receipt, every tax
document) reads paise and formats it. A third unit with its own arithmetic
would be a third rounding, which is how two halves of a bill stop agreeing.

The earlier display peg of one credit to one rupee (``ui/src/lib/billing/
format.ts``, before this) is retired with this module; the app reads
``PAISE_PER_CREDIT`` from the balance response so the two cannot drift.
"""

from __future__ import annotations

PAISE_PER_CREDIT = 50


def credits_for_charge(paise: int) -> int:
    """Whole credits for a charge of ``paise``, rounded up. Zero stays zero."""
    if paise <= 0:
        return 0
    return -(-int(paise) // PAISE_PER_CREDIT)


def round_up_to_credits(paise: int) -> int:
    """``paise`` lifted to the next whole credit, so a ledger row is always a
    whole number of credits."""
    return credits_for_charge(paise) * PAISE_PER_CREDIT


def credits_of_balance(paise: int) -> int:
    """Whole credits a balance of ``paise`` buys, rounded toward zero. A
    negative balance reads as negative credits, rounded the same way."""
    value = int(paise)
    if value >= 0:
        return value // PAISE_PER_CREDIT
    return -((-value) // PAISE_PER_CREDIT)


def paise_for_credits(credits: int) -> int:
    return int(credits) * PAISE_PER_CREDIT

"""Integer money arithmetic for Decibyl billing.

Two rules govern every number in this package:

1. **Money is stored as integer paise.** Never floats. Column names end in
   ``_paise``. Rounding happens once, here, and never at display time.
2. **Unit rates are stored as integer millipaise** (``_mpaise``). Provider
   costs routinely work out to fractions of a paise per unit — a Deepgram
   audio-minute or a thousand Sarvam characters — so quoting rates in paise
   would force a rounding error into the rate itself, which then compounds
   across every call that uses it.

The compounding problem is avoided by never rounding an intermediate: a line
item's cost is computed as one exact integer ratio and rounded exactly once.

The invoice total is defined as the **sum of the rounded line items**, not a
separately-rounded total. That is what makes ``sum(line_items) == total``
exact by construction rather than by luck, no matter how many calls are
aggregated. ``test_cost_engine.py`` asserts this over 10,000 synthetic calls.
"""

from __future__ import annotations

from api.enums import RateUnit

# ₹1 = 100 paise; 1 paise = 1000 millipaise.
PAISE_PER_RUPEE = 100
MPAISE_PER_PAISE = 1000

# Global default platform rate: ₹2.00 per billable minute.
# ₹2.00 = 200 paise = 200_000 millipaise.
DEFAULT_PLATFORM_RATE_MPAISE = 200_000

# How many raw units make up one unit of the quoted rate.
#   MINUTE          → quantity is supplied in seconds
#   1k_chars        → quantity is supplied in characters
#   1k_tokens       → quantity is supplied in tokens
_QUANTITY_PER_RATE_UNIT: dict[RateUnit, int] = {
    RateUnit.MINUTE: 60,
    RateUnit.THOUSAND_CHARS: 1000,
    RateUnit.THOUSAND_TOKENS: 1000,
}


def round_half_up_div(numerator: int, denominator: int) -> int:
    """Divide two integers, rounding halves away from zero, with no float step.

    Using ``round()`` here would apply banker's rounding, and using floats
    would make the result depend on binary representation — both are wrong for
    money that has to reconcile exactly.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator >= 0:
        return (2 * numerator + denominator) // (2 * denominator)
    return -((-2 * numerator + denominator) // (2 * denominator))


def quantity_per_rate_unit(unit: RateUnit) -> int:
    try:
        return _QUANTITY_PER_RATE_UNIT[RateUnit(unit)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unsupported rate unit: {unit!r}") from exc


def cost_paise(*, quantity: int, rate_mpaise: int, unit: RateUnit) -> int:
    """Cost in paise of ``quantity`` raw units at ``rate_mpaise`` per ``unit``.

    ``quantity`` is in the raw unit the pipeline actually measures — seconds
    for per-minute rates, characters for per-1k-character rates, tokens for
    per-1k-token rates — so callers never pre-divide and never introduce a
    rounding step of their own.
    """
    if quantity < 0:
        raise ValueError("quantity must not be negative")
    return round_half_up_div(
        quantity * rate_mpaise,
        quantity_per_rate_unit(unit) * MPAISE_PER_PAISE,
    )


def platform_fee_paise(*, billable_minutes: int, rate_mpaise: int) -> int:
    """Platform fee in paise for already-rounded billable minutes.

    The platform rate is quoted per *billable* minute, and billable minutes are
    themselves a ceiling (see :func:`billable_minutes`), so this takes whole
    minutes rather than seconds.
    """
    if billable_minutes < 0:
        raise ValueError("billable_minutes must not be negative")
    return round_half_up_div(billable_minutes * rate_mpaise, MPAISE_PER_PAISE)


def billable_minutes(billable_seconds: int) -> int:
    """``ceil(billable_seconds / 60)`` — the metric definition in DASHBOARD.md.

    A zero-second call bills zero minutes; any started second bills a minute.
    """
    if billable_seconds < 0:
        raise ValueError("billable_seconds must not be negative")
    return -(-billable_seconds // 60)


def format_paise(paise: int) -> str:
    """Render paise as rupees for display only. Never persist this."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}₹{whole:,}.{frac:02d}"

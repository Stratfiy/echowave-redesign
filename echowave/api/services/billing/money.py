"""Integer money arithmetic for Decibyl billing.

Three rules govern every number in this package:

1. **Money is stored as integer paise.** Never floats. Column names end in
   ``_paise``. Rounding happens once, here, and never at display time.
2. **Unit rates are stored as integer millipaise** (``_mpaise``). Provider
   costs routinely work out to fractions of a paise per unit — a Deepgram
   audio-minute or a thousand Sarvam characters — so quoting rates in paise
   would force a rounding error into the rate itself, which then compounds
   across every call that uses it.
3. **The list price is quoted in USD, and settled in INR.** Voice-AI platforms
   compete on a dollar figure, so ours is fixed in dollars — $0.02 a minute —
   and converted to rupees at the FX rate in force when the call happened. If
   the rate card were fixed in rupees instead, our price against Bolna or Vapi
   would drift every time the rupee moved.

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

# $1 = 1,000,000 micro-dollars. Micro-dollars rather than cents because
# $0.02/min divided into a 15-second pulse is $0.005, and per-second internal
# figures go smaller still — cents would round the rate itself to zero.
MICROS_PER_USD = 1_000_000

# Global default platform rate: the **uncommitted** price, $0.0365/min — ₹3.50
# at the fallback exchange rate below.
#
# The committed price is $0.02, which is what Bolna charges and therefore the
# number the market reads as the platform fee. Accounts reach it one of two
# ways, both of which already exist and neither of which is code: a volume tier
# once their minutes in the period cross a threshold, or an account override
# written when they sign a commitment. See services/billing/rates.py for the
# resolution order.
#
# This constant is the floor those two fall back to, so it is deliberately the
# *higher* of the two prices. It applies when the rate card has no tier row at
# all — a fresh install, or a deployment where nobody has configured pricing
# yet — and a default that silently charged the committed rate would give every
# unconfigured account the discounted price and nobody would notice, because
# an under-charge produces no error and no complaint.
DEFAULT_PLATFORM_RATE_MICROS_USD = 36_500

#: The committed rate, quoted in marketing as "$0.02/min". Not used by the
#: resolver — it is here so the number in the pricing page and the number an
#: operator types into the rate card have one source, and so the gap between
#: committed and uncommitted is visible in one place.
COMMITTED_PLATFORM_RATE_MICROS_USD = 20_000

#: Fallback USD→INR rate, in paise per dollar (₹96.00), used only when the
#: history table has no row covering the call. It is a floor against total
#: failure, not a rate we intend to bill at — a stale fallback silently
#: under-bills as the rupee weakens, so :func:`resolve_usd_inr` logs when it is
#: used and the KPI screen shows how old the newest real rate is.
DEFAULT_USD_INR_PAISE = 9_600

#: The billing granularity. Time is rounded up to a whole number of these.
#:
#: This is the commercial differentiator. Competitors bill whole minutes, so a
#: 65-second call bills two. At 15 seconds it bills five pulses — 75 seconds.
#: The saving is roughly half a pulse per call on average: ~7.5 seconds, which
#: is ~17% of a 45-second call and ~3% of a four-minute one. Short, high-volume
#: traffic is where it shows up, which is exactly the traffic Indian voice
#: agents run.
DEFAULT_PULSE_SECONDS = 15

# Retained for the legacy INR-native path: an account whose contract is written
# in rupees keeps an mpaise rate rather than tracking the dollar. ₹2.00/min.
DEFAULT_PLATFORM_RATE_MPAISE = 200_000

# How many raw units make up one unit of the quoted rate.
#   MINUTE          → quantity is supplied in seconds
#   1k_chars        → quantity is supplied in characters
#   1k_tokens       → quantity is supplied in tokens
#: How many raw measured units make up one billing unit.
#:
#: ``RateUnit.MONTH`` is deliberately absent. A recurring rental is not derived
#: from anything a call measures, so there is no quantity to divide — and
#: ``quantity_per_rate_unit`` raising for it is the guard that stops a monthly
#: rate ever being multiplied by seconds and landing on a call receipt.
#: Rentals are priced in ``api/services/billing/rentals.py`` instead.
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
    """``ceil(billable_seconds / 60)``.

    Kept as a reporting figure — "minutes used" is what a customer asks about —
    and as the input to volume-tier matching. It is **not** what the platform
    fee is computed from any more; see :func:`billed_seconds`.
    """
    if billable_seconds < 0:
        raise ValueError("billable_seconds must not be negative")
    return -(-billable_seconds // 60)


def billed_seconds(actual_seconds: int, pulse_seconds: int) -> int:
    """Connected time rounded up to a whole pulse — what we actually charge for.

    A 62-second call on a 15-second pulse bills 75 seconds, not 120. This is
    the whole point of the pulse: the customer pays for the granularity they
    used rather than for the rounding convention of whoever billed them.

    A zero-second call bills nothing. Any started second bills one pulse.
    """
    if actual_seconds < 0:
        raise ValueError("actual_seconds must not be negative")
    if pulse_seconds <= 0:
        raise ValueError("pulse_seconds must be positive")
    return -(-actual_seconds // pulse_seconds) * pulse_seconds


def usd_to_mpaise(*, micros_usd: int, usd_inr_paise: int) -> int:
    """Convert a USD rate to the INR rate we will actually bill, in millipaise.

    ``usd_inr_paise`` is the exchange rate in paise per dollar — ₹96.00 is
    9600 — so it is itself an integer and the whole conversion stays exact
    until the single rounding at the end.
    """
    if micros_usd < 0:
        raise ValueError("micros_usd must not be negative")
    if usd_inr_paise <= 0:
        raise ValueError("usd_inr_paise must be positive")
    return round_half_up_div(
        micros_usd * usd_inr_paise * MPAISE_PER_PAISE, MICROS_PER_USD
    )


def mpaise_to_micros_usd(*, mpaise: int, usd_inr_paise: int) -> int:
    """The inverse, for reporting an INR-native figure in dollars.

    Reporting only. Never bill from a round-trip through this — converting
    back and forth loses the exactness that billing from the original side
    guarantees.
    """
    if mpaise < 0:
        raise ValueError("mpaise must not be negative")
    if usd_inr_paise <= 0:
        raise ValueError("usd_inr_paise must be positive")
    return round_half_up_div(mpaise * MICROS_PER_USD, usd_inr_paise * MPAISE_PER_PAISE)


def format_micros_usd(micros_usd: int) -> str:
    """Render micro-dollars for display only. Never persist this."""
    sign = "-" if micros_usd < 0 else ""
    return f"{sign}${abs(micros_usd) / MICROS_PER_USD:,.4f}".rstrip("0").rstrip(".")


def format_paise(paise: int) -> str:
    """Render paise as rupees for display only. Never persist this."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}₹{whole:,}.{frac:02d}"

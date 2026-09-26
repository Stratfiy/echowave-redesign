"""Money on a procurement document: paise, the GST split, and the words.

Every figure is a ``Decimal`` rounded half up to the paisa -- never a float,
and never banker's rounding, because the vendor's accountant works a PO out
by hand and a figure a paisa off is a PO sent back.

**The split.** A supply between two parties in the same state is taxed as
CGST plus SGST, half the rate each; between states it is IGST at the full
rate. The state is the first two digits of each party's GSTIN. Each half is
rounded on its own line, so CGST always equals SGST on the page, which is
what a reader checks first.

**Line maths.** ``taxable = qty x rate less discount%``, rounded to the
paisa; GST on the rounded taxable value; ``amount = taxable + GST``. The
totals are sums of the printed lines, so the table always adds up.

Nothing here computes what the template engine prints: it produces the
values, and :mod:`templates` only places them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)

INTRA_STATE = "intra_state"
INTER_STATE = "inter_state"


class MoneyError(ValueError):
    """A figure or an identifier that cannot go on a document, in words a
    person can act on."""


# ---------------------------------------------------------------------------
# Parsing and rounding


def dec(value: Any) -> Decimal:
    """A Decimal from what a person or a model wrote: ``1,23,456.50``,
    ``₹ 1,000``, ``Rs. 12``, ``12.5``. Floats go through ``str`` so 0.1 stays
    0.1."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise MoneyError(f"{value!r} is not an amount")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value or "").strip()
    text = re.sub(r"(?i)^(rs\.?|inr|₹)\s*", "", text).replace(",", "").strip()
    text = text.replace("₹", "").strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"{value!r} is not an amount") from exc


def to_paise(value: Any) -> Decimal:
    """Rounded half up to two places."""
    return dec(value).quantize(PAISA, rounding=ROUND_HALF_UP)


def paise_int(value: Any) -> int:
    return int((to_paise(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Indian digit grouping: 12,34,567.00


def format_inr(value: Any) -> str:
    amount = to_paise(value)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join(pairs + [tail])
    return f"{sign}{whole}.{fraction}"


# ---------------------------------------------------------------------------
# Amount in words, the Indian way: lakh and crore


_ONES = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = [
    "_",
    "_",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
]


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def number_in_words(n: int) -> str:
    """Whole numbers in the Indian system. Crores above ninety-nine are
    themselves said in words: "One Hundred Fifty Crore"."""
    if n < 0:
        return "Minus " + number_in_words(-n)
    if n == 0:
        return "Zero"
    parts: list[str] = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    if crore:
        parts.append(f"{number_in_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(value: Any) -> str:
    """``Rupees One Crore ... and Fifty Paise Only``."""
    amount = to_paise(value)
    rupees = int(amount)
    paise = int(((abs(amount) - abs(Decimal(rupees))) * 100).to_integral_value())
    words = f"Rupees {number_in_words(rupees)}"
    if paise:
        words += f" and {_below_hundred(paise)} Paise"
    return words + " Only"


# ---------------------------------------------------------------------------
# GSTIN and PAN

_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: State and UT codes in use (01-38), other territory (97) and Centre
#: jurisdiction (99).
VALID_STATE_CODES = frozenset({f"{n:02d}" for n in range(1, 39)} | {"97", "99"})
#: What a person writes for a vendor with no GSTIN.
UNREGISTERED = frozenset({"URP", "UNREGISTERED", "NA", "N/A", "NONE", "NIL", "-"})


def gstin_check_character(first_fourteen: str) -> str:
    """The fifteenth character: a mod-36 Luhn over the first fourteen."""
    total = 0
    for index, char in enumerate(first_fourteen.upper()):
        product = _ALPHABET.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def with_checksum(first_fourteen: str) -> str:
    """A GSTIN from its first fourteen characters. For tests and samples."""
    return first_fourteen.upper() + gstin_check_character(first_fourteen)


def is_unregistered(value: Any) -> bool:
    return str(value or "").strip().upper() in UNREGISTERED


def validate_gstin(value: Any) -> str:
    """The GSTIN, normalised, or a :class:`MoneyError` saying what is wrong."""
    cleaned = re.sub(r"\s+", "", str(value or "")).upper()
    if len(cleaned) != 15:
        raise MoneyError(
            f"A GSTIN is 15 characters; {str(value or '').strip()!r} has "
            f"{len(cleaned)}."
        )
    if not _GSTIN.match(cleaned):
        raise MoneyError(
            f"{cleaned} is not in GSTIN form (2-digit state, 10-character PAN, "
            "entity number, Z, check character)."
        )
    if cleaned[:2] not in VALID_STATE_CODES:
        raise MoneyError(
            f"{cleaned} starts with {cleaned[:2]}, which is no state code."
        )
    if gstin_check_character(cleaned[:14]) != cleaned[14]:
        raise MoneyError(
            f"{cleaned} fails the GSTIN check character; check for a typo."
        )
    return cleaned


def validate_pan(value: Any) -> str:
    cleaned = re.sub(r"\s+", "", str(value or "")).upper()
    if not _PAN.match(cleaned):
        raise MoneyError(
            f"{str(value or '').strip()!r} is not a PAN (e.g. AAGCB7383J)."
        )
    return cleaned


def state_code(gstin: Any) -> str | None:
    cleaned = re.sub(r"\s+", "", str(gstin or "")).upper()
    return cleaned[:2] if len(cleaned) >= 2 and cleaned[:2].isdigit() else None


# ---------------------------------------------------------------------------
# Lines and totals


@dataclass
class Line:
    qty: Decimal
    rate: Decimal
    discount: Decimal
    gst_rate: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def gst_amount(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def amount(self) -> Decimal:
        return self.taxable_value + self.gst_amount


@dataclass
class Totals:
    supply: str
    lines: list[Line] = field(default_factory=list)

    @property
    def subtotal(self) -> Decimal:
        return sum((line.taxable_value for line in self.lines), ZERO)

    @property
    def cgst(self) -> Decimal:
        return sum((line.cgst for line in self.lines), ZERO)

    @property
    def sgst(self) -> Decimal:
        return sum((line.sgst for line in self.lines), ZERO)

    @property
    def igst(self) -> Decimal:
        return sum((line.igst for line in self.lines), ZERO)

    @property
    def gst_total(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.gst_total

    @property
    def total_paise(self) -> int:
        return paise_int(self.total)


def supply_type(
    *,
    buyer_gstin: Any,
    vendor_gstin: Any,
    buyer_state_code: Any = None,
    vendor_state_code: Any = None,
) -> str:
    """Intra-state when both parties are in one state. A party with no GSTIN
    is placed by its stated state code; with neither, it is taken to be in
    the other party's state -- the common case of a local unregistered
    supplier -- and the caller says so."""
    buyer = (None if is_unregistered(buyer_gstin) else state_code(buyer_gstin)) or (
        str(buyer_state_code).zfill(2) if buyer_state_code else None
    )
    vendor = (None if is_unregistered(vendor_gstin) else state_code(vendor_gstin)) or (
        str(vendor_state_code).zfill(2) if vendor_state_code else None
    )
    if buyer and vendor and buyer != vendor:
        return INTER_STATE
    return INTRA_STATE


def _field(item: dict[str, Any], name: str, default: Any = None) -> Decimal:
    value = item.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise MoneyError(f"{name} is missing")
        return dec(default)
    return dec(value)


def compute(
    items: Iterable[dict[str, Any]],
    *,
    buyer_gstin: Any,
    vendor_gstin: Any,
    buyer_state_code: Any = None,
    vendor_state_code: Any = None,
) -> Totals:
    """Every line's taxable value and tax, and the totals.

    ``discount`` is a percentage of ``qty x rate``; ``gst_rate`` is a
    percentage (18 for 18%). Negative quantities and rates are refused: a
    return is a debit note, not a PO line.
    """
    supply = supply_type(
        buyer_gstin=buyer_gstin,
        vendor_gstin=vendor_gstin,
        buyer_state_code=buyer_state_code,
        vendor_state_code=vendor_state_code,
    )
    totals = Totals(supply=supply)
    for number, item in enumerate(items, start=1):
        qty = _field(item, "qty")
        rate = _field(item, "rate")
        discount = _field(item, "discount", 0)
        gst_rate = _field(item, "gst_rate", 0)
        if qty < 0 or rate < 0:
            raise MoneyError(f"Item {number}: quantity and rate cannot be negative.")
        if gst_rate < 0 or discount < 0:
            raise MoneyError(
                f"Item {number}: GST rate and discount cannot be negative."
            )
        if discount > HUNDRED:
            raise MoneyError(f"Item {number}: a discount is a percentage, at most 100.")
        taxable = to_paise(qty * rate * (HUNDRED - discount) / HUNDRED)
        if supply == INTRA_STATE:
            half = to_paise(taxable * gst_rate / 200)
            cgst, sgst, igst = half, half, ZERO
        else:
            cgst, sgst, igst = ZERO, ZERO, to_paise(taxable * gst_rate / HUNDRED)
        totals.lines.append(
            Line(
                qty=qty,
                rate=rate,
                discount=discount,
                gst_rate=gst_rate,
                taxable_value=taxable,
                cgst=cgst,
                sgst=sgst,
                igst=igst,
            )
        )
    return totals


def plain_number(value: Decimal) -> str:
    """A quantity or a rate as a person writes it: 10, 12.5, 18."""
    normalised = value.normalize()
    text = f"{normalised:f}"
    return text


__all__ = [
    "INTER_STATE",
    "INTRA_STATE",
    "Line",
    "MoneyError",
    "Totals",
    "amount_in_words",
    "compute",
    "dec",
    "format_inr",
    "gstin_check_character",
    "is_unregistered",
    "number_in_words",
    "paise_int",
    "plain_number",
    "state_code",
    "supply_type",
    "to_paise",
    "validate_gstin",
    "validate_pan",
    "with_checksum",
]

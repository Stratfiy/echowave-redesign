"""GST on what Decibyl sells.

Kept in one module and away from everything else in this package, because tax
and price are different things and conflating them is how a rate card ends up
quietly 18% wrong. The rule that keeps them apart:

    **The credit ledger is entirely GST-exclusive.**

Credit is bought net of tax, usage debits net of tax, the rate card is quoted
net of tax. GST touches exactly two things — the amount charged at the payment
gateway, and the documents issued. Nothing in ``cost_engine``, ``rates``,
``reservations`` or ``costing`` needs to know this module exists.

That is also what makes the arithmetic survivable. A customer paying ₹1,180 is
credited ₹1,000; consuming ₹500 of that credit produces a tax invoice for
₹500 + ₹90, and the ₹90 was already collected inside the ₹180. Nothing is taxed
twice because only one of those two numbers is ever money in the ledger.

Place of supply decides which tax applies, and the three cases are genuinely
different rather than three names for 18%:

* customer in **our own state** — CGST 9% + SGST 9%, two taxes to two
  governments;
* customer in **another Indian state** — IGST 18%, one tax;
* customer **outside India** — zero-rated export. Zero-rated is not exempt: we
  still supply under an LUT, still report it, and still claim input credit.
  Getting this wrong in the safe-looking direction — charging GST to a foreign
  customer — overcharges them and cannot be fixed by a credit note alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.constants import (
    GST_RATE_BASIS_POINTS,
    SUPPLIER_HAS_LUT,
    SUPPLIER_STATE_CODE,
)
from api.services.billing.money import round_half_up_div

#: Percentages are held in basis points so an 18% rate is an exact integer and
#: a future 18.5% needs no code change.
BASIS_POINTS = 10_000

INDIA = "IN"

#: GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity number,
#: a fixed 'Z', and a checksum. The first two are the only part this module
#: needs — they are the customer's state, and they must agree with the state
#: they told us or the place of supply is decided on a lie.
GSTIN_LENGTH = 15


class TaxError(ValueError):
    """A supply could not be taxed."""


@dataclass(frozen=True)
class TaxBreakdown:
    """One supply's tax, split the way an invoice has to show it.

    Every field is paise. ``taxable_paise`` is the price net of tax and is the
    figure the ledger deals in; ``total_paise`` is what the customer pays.
    """

    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    #: "intra_state" | "inter_state" | "export"
    supply_type: str
    #: The rate actually applied, in basis points. Stored on the document so a
    #: rate change never rewrites an issued invoice.
    rate_basis_points: int
    place_of_supply: str

    @property
    def tax_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise

    @property
    def is_zero_rated(self) -> bool:
        return self.supply_type == "export"


def _split_half(amount: int) -> tuple[int, int]:
    """Halve a tax between CGST and SGST without losing or inventing a paise.

    The two must sum to the total exactly. Rounding each half independently
    would drop a paise on any odd amount, and an invoice whose components do
    not sum to its total is one a tax officer will ask about.
    """
    first = round_half_up_div(amount, 2)
    return first, amount - first


def is_export(country_code: str | None) -> bool:
    """Whether a customer is outside India.

    A missing country is treated as India rather than as an export. The
    conservative direction: charging GST where none was due is a refund, while
    failing to charge it where it was due is our liability plus interest.
    """
    if not country_code:
        return False
    return country_code.strip().upper() != INDIA


def state_code_from_gstin(gstin: str | None) -> str | None:
    """The state a GSTIN belongs to, or None if it does not look like one."""
    if not gstin:
        return None
    cleaned = gstin.strip().upper()
    if len(cleaned) != GSTIN_LENGTH or not cleaned[:2].isdigit():
        return None
    return cleaned[:2]


def validate_gstin(gstin: str | None, *, state_code: str | None = None) -> str | None:
    """Normalise a GSTIN, checking it agrees with the stated state.

    Deliberately does not verify the checksum or call the GST portal. A format
    check catches the typo that matters here — a wrong state code silently
    changes which tax we charge, and an unverifiable-but-well-formed GSTIN
    still produces a correct invoice.
    """
    if not gstin:
        return None

    cleaned = gstin.strip().upper().replace(" ", "")
    if len(cleaned) != GSTIN_LENGTH or not cleaned.isalnum():
        raise TaxError("A GSTIN is 15 letters and digits.")

    embedded = state_code_from_gstin(cleaned)
    if embedded is None:
        raise TaxError("That GSTIN does not start with a valid state code.")
    if state_code and embedded != state_code.strip().zfill(2):
        raise TaxError(
            f"That GSTIN belongs to state {embedded}, which does not match the "
            "state on the address."
        )
    return cleaned


def resolve_place_of_supply(
    *, country_code: str | None, state_code: str | None
) -> tuple[str, str]:
    """Where a supply lands, and therefore which tax applies.

    Returns (supply_type, place_of_supply).
    """
    if is_export(country_code):
        return "export", (country_code or "").strip().upper()

    customer_state = (state_code or "").strip().zfill(2) if state_code else None
    if not customer_state:
        # No state on file. Billed as if in our own state, which is the higher
        # compliance burden for us and never overcharges the customer — the
        # total is 18% either way, only the split differs.
        return "intra_state", SUPPLIER_STATE_CODE

    if customer_state == SUPPLIER_STATE_CODE:
        return "intra_state", customer_state
    return "inter_state", customer_state


def compute_tax(
    *,
    taxable_paise: int,
    country_code: str | None,
    state_code: str | None,
    rate_basis_points: int | None = None,
) -> TaxBreakdown:
    """Tax one supply of ``taxable_paise``, net of tax.

    The input is always the net figure. Nothing in this codebase ever holds a
    tax-inclusive price, so there is no inclusive path here to pick wrongly.
    """
    if taxable_paise < 0:
        raise TaxError("Cannot tax a negative amount.")

    rate = GST_RATE_BASIS_POINTS if rate_basis_points is None else rate_basis_points
    supply_type, place = resolve_place_of_supply(
        country_code=country_code, state_code=state_code
    )

    if supply_type == "export":
        if not SUPPLIER_HAS_LUT:
            # Without an LUT an export is not zero-rated: IGST is payable and
            # reclaimed as a refund. Refusing is deliberate — silently invoicing
            # it at zero would understate a liability that is ours, not the
            # customer's, and it is fixed by filing the LUT rather than by code.
            raise TaxError(
                "Exports cannot be zero-rated without an LUT on file. Set "
                "SUPPLIER_HAS_LUT once the LUT is filed, or bill this account "
                "as domestic."
            )
        return TaxBreakdown(
            taxable_paise=taxable_paise,
            cgst_paise=0,
            sgst_paise=0,
            igst_paise=0,
            total_paise=taxable_paise,
            supply_type=supply_type,
            rate_basis_points=0,
            place_of_supply=place,
        )

    # Rounded once, on the whole tax, then split. Computing CGST and SGST
    # separately from the taxable value rounds twice and can put the invoice a
    # paise away from 18% of itself.
    total_tax = round_half_up_div(taxable_paise * rate, BASIS_POINTS)

    if supply_type == "intra_state":
        cgst, sgst = _split_half(total_tax)
        igst = 0
    else:
        cgst = sgst = 0
        igst = total_tax

    return TaxBreakdown(
        taxable_paise=taxable_paise,
        cgst_paise=cgst,
        sgst_paise=sgst,
        igst_paise=igst,
        total_paise=taxable_paise + total_tax,
        supply_type=supply_type,
        rate_basis_points=rate,
        place_of_supply=place,
    )


def net_of(
    *, gross_paise: int, country_code: str | None, state_code: str | None
) -> int:
    """The taxable value inside a tax-inclusive amount. The inverse of
    :func:`gross_up`.

    Needed in exactly one situation: money arrived and only the gross is known.
    A bank collecting under an autopay mandate reports what it took, and what
    it took is the gross, because that is what the mandate was registered for.
    Everything downstream — the ledger, the margin, the document's taxable
    line — is net, so the split has to be recovered rather than assumed.

    Integer division makes the naive inverse off by a paise for some values, so
    the result is **verified by re-grossing it** and nudged until it round
    trips. On an amount that cannot round trip at all — possible only if the
    rate changed between charging and recording — the closest value is returned
    and the caller reconciles against the gross it already holds. A paise is not
    worth raising over; a silently wrong tax split on a filed document is.
    """
    if gross_paise < 0:
        raise TaxError("Cannot untax a negative amount.")

    rate = compute_tax(
        taxable_paise=0, country_code=country_code, state_code=state_code
    ).rate_basis_points
    if rate == 0:
        return gross_paise

    candidate = round_half_up_div(gross_paise * BASIS_POINTS, BASIS_POINTS + rate)
    # Two steps either way is generous: the error from one integer division of
    # this shape is at most one paise.
    for delta in (0, -1, 1, -2, 2):
        value = candidate + delta
        if value < 0:
            continue
        if (
            gross_up(
                taxable_paise=value,
                country_code=country_code,
                state_code=state_code,
            )
            == gross_paise
        ):
            return value
    return candidate


def gross_up(
    *, taxable_paise: int, country_code: str | None, state_code: str | None
) -> int:
    """What to charge the card for a top-up crediting ``taxable_paise``.

    The one function the payment path needs. Kept separate from
    :func:`compute_tax` so a caller cannot accidentally charge the taxable value
    and credit the total.
    """
    return compute_tax(
        taxable_paise=taxable_paise,
        country_code=country_code,
        state_code=state_code,
    ).total_paise

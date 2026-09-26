"""Invoice against order against receipt: the three-way match, as arithmetic.

The agent reads the invoice and the GRN; this compares them with the order
the register holds. It is here rather than in a prompt for the reason the
totals on a PO are: a model comparing 1,98,450.00 with 1,98,540.00, or
working out whether 381.90 is within 0.5% of 380, gets it right most of
the time, and "most of the time" is the wrong standard for what gets paid.

Pure: no database, no files. ``tools.match_invoice`` loads the order.

Checks, per order line:

- received is no more than ordered (within the quantity tolerance);
- invoiced is no more than received (within the quantity tolerance) --
  with no receipt given, nothing is matched: an invoice is never passed
  against the order alone;
- the invoiced rate, net of discount, is the order's (within the price
  tolerance);
- the GST rate is the order's.

And for the invoice as a whole: the vendor's GSTIN and ours are the ones on
the order, a line on the invoice is on the order, and the invoice's stated
taxable value, GST and total are what its own lines come to (a rupee of
round-off is allowed and said).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from api.services.documents import money

HUNDRED = Decimal(100)
#: What an invoice's stated total may differ from its lines by: the
#: round-off to the rupee that most invoices print.
ROUND_OFF = Decimal("1.00")


class MatchError(ValueError):
    """Input the match cannot run on, in words a person can act on."""


def _words(text: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _num(value: Any, what: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return money.dec(value)
    except money.MoneyError as exc:
        raise MatchError(f"{what}: {value!r} is not a number.") from exc


def _pct(value: Any, what: str) -> Decimal:
    number = _num(value, what)
    if number is None:
        return Decimal(0)
    if number < 0:
        raise MatchError(f"{what} cannot be negative.")
    return number


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else money.plain_number(value)


@dataclass
class _OrderLine:
    number: int
    description: str
    unit: str
    qty: Decimal
    rate: Decimal | None
    discount: Decimal
    gst_rate: Decimal | None
    received: Decimal | None = None
    invoiced: Decimal | None = None
    invoice_rates: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    invoice_gst: list[Decimal] = field(default_factory=list)

    @property
    def net_rate(self) -> Decimal | None:
        if self.rate is None:
            return None
        return self.rate * (HUNDRED - self.discount) / HUNDRED


def _order_lines(items: list[dict[str, Any]]) -> list[_OrderLine]:
    out = []
    for number, item in enumerate(items, start=1):
        qty = _num(item.get("qty"), f"order line {number} qty")
        if qty is None:
            raise MatchError(f"Order line {number} has no quantity.")
        out.append(
            _OrderLine(
                number=number,
                description=str(item.get("description") or "").strip(),
                unit=str(item.get("unit") or "").strip(),
                qty=qty,
                rate=_num(item.get("rate"), f"order line {number} rate"),
                discount=_num(item.get("discount"), f"order line {number} discount")
                or Decimal(0),
                gst_rate=_num(item.get("gst_rate"), f"order line {number} gst_rate"),
            )
        )
    return out


def _find(lines: list[_OrderLine], item: dict[str, Any], index: int, same_count: bool):
    """The order line an invoice or GRN line is for: by its line number, then
    its description, then -- only when both lists are the same length -- its
    position. None when nothing fits; a guess would be a wrong match."""
    given = item.get("line") or item.get("sl")
    if given not in (None, ""):
        try:
            wanted = int(str(given).strip())
        except ValueError:
            wanted = 0
        for line in lines:
            if line.number == wanted:
                return line
        return None
    words = _words(item.get("description"))
    if words:
        for line in lines:
            if _words(line.description) == words:
                return line
        partial = [
            line
            for line in lines
            if _words(line.description)
            and (words in _words(line.description) or _words(line.description) in words)
        ]
        if len(partial) == 1:
            return partial[0]
    if same_count and index < len(lines):
        return lines[index]
    return None


def match(
    order_items: list[dict[str, Any]],
    *,
    received: list[dict[str, Any]] | None = None,
    invoice: dict[str, Any] | None = None,
    order_vendor_gstin: str | None = None,
    order_buyer_gstin: str | None = None,
    price_tolerance_pct: Any = 0,
    quantity_tolerance_pct: Any = 0,
) -> dict[str, Any]:
    """The line-by-line comparison and a verdict.

    ``received`` is every GRN line so far (the same item on two GRNs is
    added up here); ``invoice`` is ``{vendor_gstin, buyer_gstin, lines,
    taxable_value?, gst_total?, total?}``. Without an invoice the answer is
    about the receipt alone: complete, short or over.
    """
    if not order_items:
        raise MatchError("The order has no lines to match against.")
    price_pct = _pct(price_tolerance_pct, "price tolerance")
    qty_pct = _pct(quantity_tolerance_pct, "quantity tolerance")
    lines = _order_lines(order_items)
    problems: list[str] = []

    def over(actual: Decimal, limit: Decimal) -> bool:
        return actual > limit * (HUNDRED + qty_pct) / HUNDRED

    if received is not None:
        rows = [r for r in received if isinstance(r, dict)]
        for index, row in enumerate(rows):
            qty = _num(row.get("qty"), f"received line {index + 1} qty")
            line = _find(lines, row, index, len(rows) == len(lines))
            if line is None:
                problems.append(
                    f"Received {row.get('description') or f'line {index + 1}'!s} "
                    "is not on the order."
                )
                continue
            if qty is None:
                continue
            line.received = (line.received or Decimal(0)) + qty
        for line in lines:
            if line.received is None:
                line.received = Decimal(0)

    invoice_lines: list[dict[str, Any]] = []
    if invoice is not None:
        invoice_lines = [
            i for i in list(invoice.get("lines") or []) if isinstance(i, dict)
        ]
        if not invoice_lines:
            raise MatchError("The invoice has no lines; give each line it bills.")
        for index, row in enumerate(invoice_lines):
            label = row.get("description") or f"line {index + 1}"
            line = _find(lines, row, index, len(invoice_lines) == len(lines))
            if line is None:
                problems.append(f"Invoice {label} is not on the order.")
                continue
            qty = _num(row.get("qty"), f"invoice line {index + 1} qty")
            rate = _num(row.get("rate"), f"invoice line {index + 1} rate")
            discount = _num(row.get("discount"), f"invoice line {index + 1} discount")
            gst = _num(row.get("gst_rate"), f"invoice line {index + 1} gst_rate")
            if qty is not None:
                line.invoiced = (line.invoiced or Decimal(0)) + qty
            if rate is not None:
                line.invoice_rates.append(
                    (rate, rate * (HUNDRED - (discount or Decimal(0))) / HUNDRED)
                )
            if gst is not None:
                line.invoice_gst.append(gst)

        # Identifiers against the order.
        for who, given, expected in (
            ("vendor", invoice.get("vendor_gstin"), order_vendor_gstin),
            ("our", invoice.get("buyer_gstin"), order_buyer_gstin),
        ):
            if not given:
                problems.append(f"The invoice does not show {who} GSTIN.")
                continue
            try:
                printed = money.validate_gstin(given)
            except money.MoneyError as exc:
                problems.append(f"The invoice's {who} GSTIN is not valid: {exc}")
                continue
            if expected and printed != str(expected).strip().upper():
                problems.append(
                    f"The invoice's {who} GSTIN is {printed}; the order has {expected}."
                )
        if received is None:
            problems.append(
                "No goods receipt was given, so nothing can be passed: an invoice "
                "is matched against what was received, not against the order alone."
            )

    report = []
    for line in lines:
        issues: list[str] = []
        if line.received is not None and over(line.received, line.qty):
            issues.append(
                f"received {_plain(line.received)} but ordered {_plain(line.qty)}"
            )
        if invoice is not None:
            if line.invoiced is not None and line.received is not None:
                if over(line.invoiced, line.received):
                    issues.append(
                        f"invoiced {_plain(line.invoiced)} but received "
                        f"{_plain(line.received)}"
                    )
            for rate, net in line.invoice_rates:
                ordered = line.net_rate
                if ordered is None:
                    issues.append("the order has no rate to check against")
                    continue
                allowed = ordered * price_pct / HUNDRED
                if abs(net - ordered) > allowed:
                    issues.append(
                        f"rate {money.format_inr(net)} against the order's "
                        f"{money.format_inr(ordered)}"
                    )
            for gst in line.invoice_gst:
                if line.gst_rate is not None and gst != line.gst_rate:
                    issues.append(
                        f"GST {_plain(gst)}% against the order's {_plain(line.gst_rate)}%"
                    )
        short = line.received is not None and line.received < line.qty
        report.append(
            {
                "line": line.number,
                "description": line.description,
                "unit": line.unit,
                "ordered": _plain(line.qty),
                "received": _plain(line.received),
                "short_by": _plain(line.qty - line.received) if short else None,
                "invoiced": _plain(line.invoiced),
                "order_rate": None
                if line.net_rate is None
                else money.format_inr(line.net_rate),
                "invoice_rate": ", ".join(
                    money.format_inr(net) for _, net in line.invoice_rates
                )
                or None,
                "order_gst_rate": _plain(line.gst_rate),
                "invoice_gst_rate": ", ".join(_plain(g) or "" for g in line.invoice_gst)
                or None,
                "ok": not issues,
                "problems": issues,
            }
        )
        problems.extend(
            f"Line {line.number} ({line.description}): {i}." for i in issues
        )

    out: dict[str, Any] = {"lines": report, "problems": problems}

    if invoice is not None:
        # The invoice's own arithmetic, from the lines it bills.
        try:
            billed = money.compute(
                [
                    {
                        "qty": row.get("qty"),
                        "rate": row.get("rate"),
                        "discount": row.get("discount") or 0,
                        "gst_rate": row.get("gst_rate") or 0,
                    }
                    for row in invoice_lines
                ],
                buyer_gstin=invoice.get("buyer_gstin") or order_buyer_gstin,
                vendor_gstin=invoice.get("vendor_gstin") or order_vendor_gstin,
            )
        except money.MoneyError as exc:
            problems.append(f"The invoice lines cannot be added up: {exc}")
            billed = None
        notes: list[str] = []
        if billed is not None:
            out["computed"] = {
                "taxable_value": money.format_inr(billed.subtotal),
                "gst_total": money.format_inr(billed.gst_total),
                "total": money.format_inr(billed.total),
            }
            for name, computed in (
                ("taxable_value", billed.subtotal),
                ("gst_total", billed.gst_total),
                ("total", billed.total),
            ):
                stated = _num(invoice.get(name), f"invoice {name}")
                if stated is None:
                    continue
                gap = abs(stated - computed)
                if gap == 0:
                    continue
                label = name.replace("_", " ")
                if name == "total" and gap <= ROUND_OFF:
                    notes.append(
                        f"The total differs from its lines by ₹{money.format_inr(gap)}: "
                        "round-off."
                    )
                    continue
                problems.append(
                    f"The invoice's {label} is ₹{money.format_inr(stated)}; its lines "
                    f"come to ₹{money.format_inr(computed)}."
                )
        if notes:
            out["notes"] = notes
        out["status"] = "mismatched" if problems else "matched"
    else:
        if problems or any(r["problems"] for r in report):
            out["status"] = "over"
        elif any(r["short_by"] for r in report):
            out["status"] = "short"
        else:
            out["status"] = "complete"
    return out


__all__ = ["MatchError", "match"]

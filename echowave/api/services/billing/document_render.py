"""Rendering an issued document as a page a customer can print.

**Every figure comes from the frozen snapshot on the row, never recomputed.**
An issued document is a statement about a moment: the customer's address, the
supplier's address, the rate and the tax split were all what they were on the
day. Deriving any of it from current data would silently rewrite history the
first time a customer moves office or a rate changes — and the rewritten
version would look exactly as authoritative as the original.

HTML rather than a server-side PDF, deliberately. A real PDF needs a rendering
dependency (reportlab, weasyprint) added to the image, and what it buys over
this is a file extension: every browser prints this page to PDF, with the page
size and margins set below so the output is A4 with sane borders. When a PDF
*byte stream* is genuinely needed — emailing one as an attachment, say — that is
the point to add the dependency, and this template is what it should render.

Everything interpolated is escaped. A legal name and an address are customer
input, and this page is served from our own origin against a session; an
unescaped `<script>` in a company name would run there.
"""

from __future__ import annotations

from html import escape
from typing import Any

#: Printed on a document that is not a tax invoice. A receipt voucher
#: acknowledges an advance; calling it an invoice would misstate what it is.
_TITLES = {
    "tax_invoice": "Tax invoice",
    "receipt_voucher": "Receipt voucher",
}

_SUPPLY_TYPES = {
    "intra_state": "Intra-state supply",
    "inter_state": "Inter-state supply",
    "export": "Export of services",
}


def _rupees(paise: Any) -> str:
    """Paise to rupees, at the edge and nowhere else.

    Money is integer paise everywhere in this codebase; this is the only place
    it becomes a decimal, and it becomes a *string* rather than a float.
    """
    try:
        value = int(paise or 0)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}₹{value // 100:,}.{value % 100:02d}"


def _address_lines(party: dict) -> list[str]:
    """A party's address as printed lines.

    Handles both shapes on purpose: the supplier snapshot holds one `address`
    string, the customer snapshot holds separate fields. Both are frozen on the
    row and neither can be normalised retrospectively.
    """
    if party.get("address"):
        return [str(party["address"])]
    lines = [
        party.get("address_line1"),
        party.get("address_line2"),
        ", ".join(
            part
            for part in (party.get("city"), party.get("postal_code"))
            if part
        )
        or None,
    ]
    return [str(line) for line in lines if line]


def _party_block(heading: str, party: dict) -> str:
    rows = [f"<p class='name'>{escape(str(party.get('legal_name') or '—'))}</p>"]
    for line in _address_lines(party):
        rows.append(f"<p>{escape(line)}</p>")
    if party.get("gstin"):
        rows.append(f"<p>GSTIN: {escape(str(party['gstin']))}</p>")
    if party.get("pan"):
        rows.append(f"<p>PAN: {escape(str(party['pan']))}</p>")
    if party.get("billing_email"):
        rows.append(f"<p>{escape(str(party['billing_email']))}</p>")
    return (
        f"<section class='party'><h2>{escape(heading)}</h2>"
        + "".join(rows)
        + "</section>"
    )


def _line_rows(line_items: list[dict]) -> str:
    if not line_items:
        return "<tr><td colspan='4' class='muted'>No line items recorded.</td></tr>"
    rows = []
    for item in line_items:
        quantity = item.get("quantity_minutes")
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('description') or '—'))}</td>"
            f"<td class='num'>{escape(str(item.get('sac_code') or '—'))}</td>"
            f"<td class='num'>{escape(str(quantity)) if quantity is not None else '—'}</td>"
            f"<td class='num'>{_rupees(item.get('amount_paise'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _tax_rows(document: dict) -> str:
    """Only the taxes that actually apply.

    Printing a zero CGST line beside a real IGST line invites the reader to
    conclude the split is wrong. A zero-rated export prints its LUT instead,
    because "no tax" without the authority for it looks like an omission.
    """
    rate = int(document.get("rate_basis_points") or 0) / 100
    rows = []
    for key, label in (
        ("cgst_paise", f"CGST @ {rate / 2:g}%"),
        ("sgst_paise", f"SGST @ {rate / 2:g}%"),
        ("igst_paise", f"IGST @ {rate:g}%"),
    ):
        amount = int(document.get(key) or 0)
        if amount:
            rows.append(
                f"<tr><th>{escape(label)}</th><td class='num'>{_rupees(amount)}</td></tr>"
            )
    if not rows:
        supplier = document.get("supplier") or {}
        note = (
            f"Zero-rated export under LUT {escape(str(supplier['lut_number']))}"
            if supplier.get("lut_number")
            else "No tax charged"
        )
        rows.append(f"<tr><th>Tax</th><td class='num'>{note}</td></tr>")
    return "".join(rows)


_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px;
  font: 13px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #16181d; background: #f6f6f4;
}
.sheet {
  max-width: 800px; margin: 0 auto; padding: 40px;
  background: #fff; border: 1px solid #e3e2dd; border-radius: 4px;
}
header { display: flex; justify-content: space-between; gap: 24px;
  align-items: flex-start; border-bottom: 2px solid #16181d; padding-bottom: 16px; }
h1 { margin: 0; font-size: 20px; letter-spacing: -0.02em; }
.serial { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 15px; }
.meta { text-align: right; font-size: 12px; color: #5f5f58; }
.meta strong { color: #16181d; }
.parties { display: flex; gap: 32px; margin: 24px 0; }
.party { flex: 1; }
.party h2 { margin: 0 0 6px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.08em; color: #5f5f58; font-weight: 600; }
.party p { margin: 0 0 2px; }
.party .name { font-weight: 600; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e3e2dd; }
thead th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: #5f5f58; border-bottom: 1px solid #c9c8c1; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.totals { margin-left: auto; width: 300px; margin-top: 16px; }
.totals th { font-weight: 400; color: #5f5f58; border: none; padding: 4px 10px; }
.totals td { border: none; padding: 4px 10px; }
.totals .grand th, .totals .grand td { border-top: 2px solid #16181d;
  font-weight: 700; font-size: 15px; padding-top: 10px; color: #16181d; }
.muted { color: #5f5f58; }
footer { margin-top: 28px; padding-top: 14px; border-top: 1px solid #e3e2dd;
  font-size: 11px; color: #5f5f58; }
.actions { max-width: 800px; margin: 0 auto 16px; text-align: right; }
button { font: inherit; padding: 7px 14px; border-radius: 6px;
  border: 1px solid #c9c8c1; background: #fff; cursor: pointer; }
/* A4 with real margins, so "print to PDF" produces a document rather than a
   screenshot of a web page. */
@page { size: A4; margin: 14mm; }
@media print {
  body { padding: 0; background: #fff; }
  .sheet { border: none; border-radius: 0; padding: 0; max-width: none; }
  .actions { display: none; }
}
"""


def render_html(document: dict) -> str:
    """One issued document as a self-contained, printable page.

    Takes the same dict the API returns for a document, so there is one
    description of what a document *is* and this cannot drift from it.
    """
    kind = str(document.get("kind") or "")
    title = _TITLES.get(kind, "Document")
    supplier = document.get("supplier") or {}
    customer = document.get("customer") or {}

    issued_at = str(document.get("issued_at") or "")[:10]
    period = ""
    if document.get("period_start") and document.get("period_end"):
        period = (
            f"<div>Period <strong>{escape(str(document['period_start']))}"
            f" – {escape(str(document['period_end']))}</strong></div>"
        )

    supply = _SUPPLY_TYPES.get(
        str(document.get("supply_type") or ""), str(document.get("supply_type") or "")
    )
    place = document.get("place_of_supply")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} {escape(str(document.get("number") or ""))}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="actions">
  <button onclick="window.print()">Print or save as PDF</button>
</div>
<div class="sheet">
  <header>
    <div>
      <h1>{escape(title)}</h1>
      <p class="serial">{escape(str(document.get("number") or "—"))}</p>
    </div>
    <div class="meta">
      <div>Issued <strong>{escape(issued_at)}</strong></div>
      {period}
      <div>{escape(supply)}</div>
      {f"<div>Place of supply {escape(str(place))}</div>" if place else ""}
    </div>
  </header>

  <div class="parties">
    {_party_block("Supplier", supplier)}
    {_party_block("Billed to", customer)}
  </div>

  <table>
    <thead>
      <tr>
        <th>Description</th>
        <th class="num">SAC</th>
        <th class="num">Minutes</th>
        <th class="num">Amount</th>
      </tr>
    </thead>
    <tbody>{_line_rows(document.get("line_items") or [])}</tbody>
  </table>

  <table class="totals">
    <tbody>
      <tr>
        <th>Taxable value</th>
        <td class="num">{_rupees(document.get("taxable_paise"))}</td>
      </tr>
      {_tax_rows(document)}
      <tr class="grand">
        <th>Total</th>
        <td class="num">{_rupees(document.get("total_paise"))}</td>
      </tr>
    </tbody>
  </table>

  <footer>
    <p>This is a computer-generated document and needs no signature.</p>
    <p>Amounts are in Indian rupees.</p>
  </footer>
</div>
</body>
</html>"""

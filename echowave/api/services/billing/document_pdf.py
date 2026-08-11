"""PDF rendering for tax documents.

reportlab, not weasyprint or a headless browser: pure Python wheel, nothing to
add to the Docker image, and a receipt or invoice is a title, two address
blocks and a table -- platypus's flowables are enough, there is no HTML/CSS
worth reusing for that.

Renders straight from the fields already frozen onto :class:`TaxDocumentModel`
at issue (``supplier_snapshot``, ``customer_snapshot``, ``line_items``) rather
than re-deriving anything, so the PDF can never disagree with the numbers the
API already returns for the same document.
"""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from api.db.models import TaxDocumentModel

_KIND_TITLES = {
    "receipt_voucher": "Receipt Voucher",
    "tax_invoice": "Tax Invoice",
}

_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle(
    "DocumentTitle", parent=_STYLES["Heading1"], fontSize=18, spaceAfter=1 * mm
)
_SUBTITLE = ParagraphStyle(
    "DocumentSubtitle", parent=_STYLES["Normal"], fontSize=10, textColor=colors.grey
)
_HEADING = ParagraphStyle(
    "PartyHeading",
    parent=_STYLES["Normal"],
    fontSize=8,
    textColor=colors.grey,
    spaceAfter=1 * mm,
)
_BODY = ParagraphStyle("PartyBody", parent=_STYLES["Normal"], fontSize=10, leading=13)


def _rupees(paise: int) -> str:
    return f"Rs. {paise / 100:,.2f}"


def _party_lines(snapshot: dict, *, gstin_label: str) -> str:
    lines = [snapshot.get("legal_name") or "-"]

    address = snapshot.get("address")
    if address is None:
        parts = [
            snapshot.get("address_line1"),
            snapshot.get("address_line2"),
            snapshot.get("city"),
            snapshot.get("state_code"),
            snapshot.get("postal_code"),
        ]
        address = ", ".join(p for p in parts if p)
    if address:
        lines.append(str(address))

    gstin = snapshot.get("gstin")
    if gstin:
        lines.append(f"{gstin_label}: {gstin}")

    pan = snapshot.get("pan")
    if pan:
        lines.append(f"PAN: {pan}")

    email = snapshot.get("billing_email")
    if email:
        lines.append(email)

    return "<br/>".join(lines)


def _line_items_table(line_items: list[dict]) -> Table:
    header = ["Description", "Qty", "Amount"]
    rows = [header]
    for item in line_items:
        qty = item.get("quantity_minutes")
        rows.append(
            [
                Paragraph(str(item.get("description") or ""), _BODY),
                f"{qty} min" if qty is not None else "-",
                _rupees(int(item.get("amount_paise") or 0)),
            ]
        )

    table = Table(rows, colWidths=[110 * mm, 25 * mm, 35 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    return table


def _totals_table(document: TaxDocumentModel) -> Table:
    rows = [["Taxable value", _rupees(int(document.taxable_paise))]]
    if document.cgst_paise:
        rows.append(["CGST", _rupees(int(document.cgst_paise))])
    if document.sgst_paise:
        rows.append(["SGST", _rupees(int(document.sgst_paise))])
    if document.igst_paise:
        rows.append(["IGST", _rupees(int(document.igst_paise))])
    rows.append(["Total", _rupees(int(document.total_paise))])

    table = Table(rows, colWidths=[135 * mm, 35 * mm])
    style = [
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
    ]
    table.setStyle(TableStyle(style))
    return table


def render_document_pdf(document: TaxDocumentModel) -> bytes:
    """A tax document as a downloadable PDF, in original currency (INR)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        title=f"{_KIND_TITLES.get(document.kind, document.kind)} {document.number}",
    )

    story: list = [
        Paragraph(_KIND_TITLES.get(document.kind, document.kind), _TITLE),
        Paragraph(
            f"No. {document.number} &nbsp;&bull;&nbsp; "
            f"Issued {document.issued_at.strftime('%d %b %Y') if document.issued_at else '-'}",
            _SUBTITLE,
        ),
        Spacer(1, 8 * mm),
    ]

    if document.period_start and document.period_end:
        story.append(
            Paragraph(
                f"Period: {document.period_start.isoformat()} to "
                f"{document.period_end.isoformat()}",
                _SUBTITLE,
            )
        )
        story.append(Spacer(1, 4 * mm))

    parties = Table(
        [
            [
                Paragraph("SUPPLIER", _HEADING),
                Paragraph("BILLED TO", _HEADING),
            ],
            [
                Paragraph(
                    _party_lines(document.supplier_snapshot or {}, gstin_label="GSTIN"),
                    _BODY,
                ),
                Paragraph(
                    _party_lines(document.customer_snapshot or {}, gstin_label="GSTIN"),
                    _BODY,
                ),
            ],
        ],
        colWidths=[85 * mm, 85 * mm],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(parties)
    story.append(Spacer(1, 8 * mm))

    story.append(_line_items_table(document.line_items or []))
    story.append(Spacer(1, 4 * mm))
    story.append(_totals_table(document))

    if document.place_of_supply:
        story.append(Spacer(1, 6 * mm))
        story.append(
            Paragraph(f"Place of supply: {document.place_of_supply}", _SUBTITLE)
        )
    if document.supply_type == "export" and (document.supplier_snapshot or {}).get(
        "lut_number"
    ):
        story.append(
            Paragraph(
                "Supply meant for export under LUT without payment of IGST "
                f"(LUT {document.supplier_snapshot['lut_number']}).",
                _SUBTITLE,
            )
        )

    doc.build(story)
    return buffer.getvalue()

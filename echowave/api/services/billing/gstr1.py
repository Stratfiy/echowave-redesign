"""The month's GSTR-1, from the documents we issued, reconciled to the money.

KAN-80. GSTR-1 is the outward-supplies return: every tax document issued in
a month, grouped the way the return wants them — B2B (the customer has a
GSTIN), B2C (they do not), EXP (exports under LUT, zero-rated), and credit
notes against earlier documents. The rows come from ``tax_documents``,
which is the record of what was invoiced; the reconciliation beside them
compares that to what Razorpay actually captured in the month, so a gap is
seen before the return is filed rather than after.

Exports carry the FIRC/FIRA reference recorded against each payment
(``payments.firc_reference``); an export payment without one is listed, since
that reference is what proves the export at filing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PaymentModel, TaxDocumentModel
from api.services.billing.documents import CREDIT_NOTE, RECEIPT_VOUCHER


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )
    return start, end


def parse_month(value: str) -> tuple[int, int]:
    """``YYYY-MM`` to (year, month), or ValueError."""
    year_s, _, month_s = (value or "").strip().partition("-")
    year, month = int(year_s), int(month_s)
    if not 1 <= month <= 12:
        raise ValueError(f"{value!r} is not a month")
    return year, month


@dataclass(frozen=True)
class Row:
    section: str  # b2b | b2c | exp | cdnr
    kind: str
    number: str
    issued_on: date
    customer_name: str
    customer_gstin: str | None
    place_of_supply: str
    supply_type: str
    rate_basis_points: int
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    against_number: str | None = None
    firc_reference: str | None = None

    def as_dict(self) -> dict:
        return {
            "section": self.section,
            "kind": self.kind,
            "number": self.number,
            "issued_on": self.issued_on.isoformat(),
            "customer_name": self.customer_name,
            "customer_gstin": self.customer_gstin,
            "place_of_supply": self.place_of_supply,
            "supply_type": self.supply_type,
            "rate_percent": self.rate_basis_points / 100,
            "taxable_paise": self.taxable_paise,
            "cgst_paise": self.cgst_paise,
            "sgst_paise": self.sgst_paise,
            "igst_paise": self.igst_paise,
            "total_paise": self.total_paise,
            "against_number": self.against_number,
            "firc_reference": self.firc_reference,
        }


@dataclass(frozen=True)
class Report:
    month: str
    rows: list[Row]
    #: Receipt vouchers issued in the month, gross (what the customer paid).
    documents_gross_paise: int
    #: Payments Razorpay captured in the month, gross.
    payments_gross_paise: int
    #: Export payments in the month with no FIRC/FIRA reference yet.
    exports_without_firc: list[dict] = field(default_factory=list)

    @property
    def difference_paise(self) -> int:
        return self.documents_gross_paise - self.payments_gross_paise

    def totals(self) -> dict:
        out: dict[str, dict[str, int]] = {}
        for row in self.rows:
            bucket = out.setdefault(
                row.section,
                {
                    "count": 0,
                    "taxable_paise": 0,
                    "cgst_paise": 0,
                    "sgst_paise": 0,
                    "igst_paise": 0,
                    "total_paise": 0,
                },
            )
            bucket["count"] += 1
            for key in (
                "taxable_paise",
                "cgst_paise",
                "sgst_paise",
                "igst_paise",
                "total_paise",
            ):
                bucket[key] += getattr(row, key)
        return out

    def as_dict(self) -> dict:
        return {
            "month": self.month,
            "rows": [r.as_dict() for r in self.rows],
            "totals": self.totals(),
            "reconciliation": {
                "documents_gross_paise": self.documents_gross_paise,
                "payments_gross_paise": self.payments_gross_paise,
                "difference_paise": self.difference_paise,
                "reconciles": self.difference_paise == 0,
                "exports_without_firc": self.exports_without_firc,
            },
        }


def _section(document: TaxDocumentModel) -> str:
    if document.kind == CREDIT_NOTE:
        return "cdnr"
    if document.supply_type == "export":
        return "exp"
    gstin = (document.customer_snapshot or {}).get("gstin")
    return "b2b" if gstin else "b2c"


async def build(session: AsyncSession, *, year: int, month: int) -> Report:
    start, end = month_bounds(year, month)
    documents = (
        await session.scalars(
            select(TaxDocumentModel)
            .where(
                TaxDocumentModel.issued_at >= start, TaxDocumentModel.issued_at < end
            )
            .order_by(TaxDocumentModel.issued_at, TaxDocumentModel.id)
        )
    ).all()
    payments_by_id = {}
    payment_ids = [d.payment_id for d in documents if d.payment_id is not None]
    if payment_ids:
        for p in (
            await session.scalars(
                select(PaymentModel).where(PaymentModel.id.in_(payment_ids))
            )
        ).all():
            payments_by_id[p.id] = p

    rows: list[Row] = []
    for d in documents:
        customer = d.customer_snapshot or {}
        against = None
        for item in d.line_items or []:
            if isinstance(item, dict) and item.get("against_number"):
                against = item["against_number"]
        payment = payments_by_id.get(d.payment_id) if d.payment_id else None
        rows.append(
            Row(
                section=_section(d),
                kind=d.kind,
                number=d.number,
                issued_on=(d.issued_at or start).date(),
                customer_name=customer.get("legal_name") or "",
                customer_gstin=customer.get("gstin") or None,
                place_of_supply=d.place_of_supply or "",
                supply_type=d.supply_type,
                rate_basis_points=int(d.rate_basis_points or 0),
                taxable_paise=int(d.taxable_paise),
                cgst_paise=int(d.cgst_paise or 0),
                sgst_paise=int(d.sgst_paise or 0),
                igst_paise=int(d.igst_paise or 0),
                total_paise=int(d.total_paise),
                against_number=against,
                firc_reference=getattr(payment, "firc_reference", None)
                if payment
                else None,
            )
        )

    documents_gross = sum(
        int(d.total_paise) for d in documents if d.kind == RECEIPT_VOUCHER
    )
    payments_gross = int(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentModel.gross_paise), 0)).where(
                PaymentModel.status == "paid",
                PaymentModel.paid_at >= start,
                PaymentModel.paid_at < end,
            )
        )
        or 0
    )
    exports_without_firc = [
        {
            "payment_id": p.id,
            "organization_id": p.organization_id,
            "provider_payment_id": p.payment_id,
            "gross_paise": int(p.gross_paise or p.amount_paise),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in (
            await session.scalars(
                select(PaymentModel).where(
                    PaymentModel.status == "paid",
                    PaymentModel.paid_at >= start,
                    PaymentModel.paid_at < end,
                    PaymentModel.igst_paise == 0,
                    PaymentModel.cgst_paise == 0,
                    PaymentModel.sgst_paise == 0,
                    PaymentModel.gross_paise == PaymentModel.amount_paise,
                    PaymentModel.firc_reference.is_(None),
                )
            )
        ).all()
        # A zero-tax payment at face value is an export (or a pre-GST legacy
        # row); a domestic payment always carries tax. Listed for a person to
        # confirm, never asserted.
    ]
    return Report(
        month=f"{year:04d}-{month:02d}",
        rows=rows,
        documents_gross_paise=documents_gross,
        payments_gross_paise=payments_gross,
        exports_without_firc=exports_without_firc,
    )


CSV_COLUMNS = (
    "section",
    "kind",
    "number",
    "issued_on",
    "customer_name",
    "customer_gstin",
    "place_of_supply",
    "supply_type",
    "rate_percent",
    "taxable_paise",
    "cgst_paise",
    "sgst_paise",
    "igst_paise",
    "total_paise",
    "against_number",
    "firc_reference",
)


def to_csv(report: Report) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in report.rows:
        writer.writerow({k: row.as_dict().get(k) for k in CSV_COLUMNS})
    return out.getvalue()

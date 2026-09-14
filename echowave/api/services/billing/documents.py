"""Receipt vouchers and tax invoices.

Two documents, because GST requires two. A prepaid top-up is an advance, and
for services the time of supply is the earlier of invoice or payment — so tax
falls due the moment the money arrives, evidenced by a **receipt voucher**. The
**tax invoice** follows when the service is actually supplied: monthly, against
measured usage, adjusting the advance already taxed.

Nothing is taxed twice, and the reason is worth stating because the arithmetic
looks like it should be. A customer pays ₹1,180 and is credited ₹1,000. Consume
₹500 of that credit and the invoice reads ₹500 + ₹90 — but the ₹90 was already
collected inside the ₹180, and no money moves against the invoice. Only one of
those two numbers is ever money in the ledger, and it is the net one.

The numbering is the part with a hard external requirement. GST wants a
consecutive serial, unique within a financial year, at most 16 characters, with
no gaps. A gap has to be explained; two documents sharing a number is worse. So
a number is allocated from a locked row rather than from a count of existing
documents, which races, or from a database sequence, which is explicitly allowed
to skip on rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.constants import (
    CREDIT_NOTE_NUMBER_PREFIX,
    INVOICE_NUMBER_PREFIX,
    RECEIPT_NUMBER_PREFIX,
    SUPPLIER_ADDRESS,
    SUPPLIER_GSTIN,
    SUPPLIER_HAS_LUT,
    SUPPLIER_LEGAL_NAME,
    SUPPLIER_LUT_NUMBER,
    SUPPLIER_PAN,
    SUPPLIER_SAC_CODE,
    SUPPLIER_STATE_CODE,
)
from api.db.models import (
    DocumentSequenceModel,
    PaymentModel,
    TaxDocumentModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing.billing_profile import get_profile
from api.services.billing.money import round_half_up_div
from api.services.billing.tax import TaxBreakdown, compute_tax, net_of

RECEIPT_VOUCHER = "receipt_voucher"
TAX_INVOICE = "tax_invoice"
#: Issued against an earlier document when money goes back: a refund, a
#: correction. Its own series, its own sequence, referencing what it reverses.
CREDIT_NOTE = "credit_note"

#: One series per kind. A KeyError here is the right failure: a new document
#: kind without a series would otherwise be numbered as a receipt, silently.
_NUMBER_PREFIXES = {
    TAX_INVOICE: INVOICE_NUMBER_PREFIX,
    RECEIPT_VOUCHER: RECEIPT_NUMBER_PREFIX,
    CREDIT_NOTE: CREDIT_NOTE_NUMBER_PREFIX,
}

#: Indian financial year starts in April.
FINANCIAL_YEAR_START_MONTH = 4

#: Zero-padded serial width. Six digits is a million documents a year, and the
#: whole number has to fit in 16 characters: "INV/26-27/000001".
SERIAL_WIDTH = 6


class DocumentError(RuntimeError):
    """A document could not be issued."""


@dataclass(frozen=True)
class IssuedDocument:
    """A document as a listing shows it."""

    id: int
    kind: str
    number: str
    issued_at: str | None
    period_start: str | None
    period_end: str | None
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    supply_type: str


def financial_year(at: date | datetime) -> str:
    """The Indian financial year containing ``at``, as "26-27".

    April to March. A January invoice belongs to the year that started the
    previous April, which is the off-by-one every naive implementation gets
    wrong and which then puts two financial years' serials in one sequence.
    """
    day = at.date() if isinstance(at, datetime) else at
    start_year = day.year if day.month >= FINANCIAL_YEAR_START_MONTH else day.year - 1
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def supplier_snapshot() -> dict:
    """Us, as of issue."""
    return {
        "legal_name": SUPPLIER_LEGAL_NAME,
        "gstin": SUPPLIER_GSTIN,
        "state_code": SUPPLIER_STATE_CODE,
        "address": SUPPLIER_ADDRESS,
        "pan": SUPPLIER_PAN,
        "sac_code": SUPPLIER_SAC_CODE,
        "lut_number": SUPPLIER_LUT_NUMBER if SUPPLIER_HAS_LUT else None,
        "lut_valid_until": (
            (constants.SUPPLIER_LUT_VALID_UNTIL or None) if SUPPLIER_HAS_LUT else None
        ),
        "udyam_number": constants.SUPPLIER_UDYAM_NUMBER or None,
    }


def supplier_is_configured() -> bool:
    """Whether we can issue anything at all.

    A document without our legal name and GSTIN is not a tax invoice, so this
    is checked before issuing rather than producing a plausible-looking document
    with blanks where the statutory fields go.
    """
    return bool(SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN)


async def _next_number(session: AsyncSession, *, kind: str, year: str) -> str:
    """Allocate the next serial in a series, gaplessly.

    The row lock is what makes it gapless under concurrency: two payments
    captured in the same instant would otherwise read the same last number and
    both take it. A unique constraint on the number would then reject one of
    them — turning a numbering race into a failed payment webhook.
    """
    # Create the series row if this is the first document of the financial year.
    # ON CONFLICT DO NOTHING rather than a check-then-insert: two payments
    # captured in the same instant on 1 April would both miss the check, and a
    # plain insert would raise a unique violation on the loser — poisoning its
    # transaction and turning a numbering race into a failed payment webhook.
    await session.execute(
        pg_insert(DocumentSequenceModel)
        .values(kind=kind, financial_year=year, last_number=0)
        .on_conflict_do_nothing(index_elements=["kind", "financial_year"])
    )

    row = await session.scalar(
        select(DocumentSequenceModel)
        .where(
            DocumentSequenceModel.kind == kind,
            DocumentSequenceModel.financial_year == year,
        )
        .with_for_update()
    )
    if row is None:
        raise DocumentError(f"Could not allocate a {kind} number for {year}.")

    row.last_number = int(row.last_number or 0) + 1
    await session.flush()

    prefix = _NUMBER_PREFIXES[kind]
    return f"{prefix}/{year}/{row.last_number:0{SERIAL_WIDTH}d}"


def _view(row: TaxDocumentModel) -> IssuedDocument:
    return IssuedDocument(
        id=row.id,
        kind=row.kind,
        number=row.number,
        issued_at=row.issued_at.isoformat() if row.issued_at else None,
        period_start=row.period_start.isoformat() if row.period_start else None,
        period_end=row.period_end.isoformat() if row.period_end else None,
        taxable_paise=int(row.taxable_paise),
        cgst_paise=int(row.cgst_paise or 0),
        sgst_paise=int(row.sgst_paise or 0),
        igst_paise=int(row.igst_paise or 0),
        total_paise=int(row.total_paise),
        supply_type=row.supply_type,
    )


async def _issue(
    session: AsyncSession,
    *,
    organization_id: int,
    kind: str,
    breakdown: TaxBreakdown,
    line_items: list[dict],
    customer: dict,
    issued_at: datetime,
    period_start: date | None = None,
    period_end: date | None = None,
    payment_id: int | None = None,
    provider_payment_id: str | None = None,
) -> TaxDocumentModel:
    year = financial_year(issued_at)
    number = await _next_number(session, kind=kind, year=year)

    document = TaxDocumentModel(
        organization_id=organization_id,
        kind=kind,
        number=number,
        financial_year=year,
        issued_at=issued_at,
        period_start=period_start,
        period_end=period_end,
        taxable_paise=breakdown.taxable_paise,
        cgst_paise=breakdown.cgst_paise,
        sgst_paise=breakdown.sgst_paise,
        igst_paise=breakdown.igst_paise,
        total_paise=breakdown.total_paise,
        supply_type=breakdown.supply_type,
        place_of_supply=breakdown.place_of_supply,
        rate_basis_points=breakdown.rate_basis_points,
        supplier_snapshot=supplier_snapshot(),
        customer_snapshot=customer,
        line_items=line_items,
        payment_id=payment_id,
        provider_payment_id=provider_payment_id,
    )
    session.add(document)
    await session.flush()

    logger.info(
        "Issued {} {} for org {}: {} paise taxable, {} total",
        kind,
        number,
        organization_id,
        breakdown.taxable_paise,
        breakdown.total_paise,
    )
    return document


async def issue_receipt_voucher(
    session: AsyncSession, *, payment: PaymentModel, issued_at: datetime | None = None
) -> IssuedDocument | None:
    """Acknowledge an advance, and the tax that fell due with it.

    ``issued_at`` defaults to when the payment was captured; it is a
    parameter so a voucher issued late lands in the month the money did.

    Idempotent on the payment: a replayed webhook must not issue a second
    voucher, and a partial unique index on ``payment_id`` backs that up.

    Returns None rather than raising when we are not configured to issue. A
    payment that succeeded must not be rolled back because the supplier GSTIN
    is missing from the environment — the money is real either way, and the
    document can be issued later.
    """
    existing = await session.scalar(
        select(TaxDocumentModel).where(
            TaxDocumentModel.kind == RECEIPT_VOUCHER,
            TaxDocumentModel.payment_id == payment.id,
        )
    )
    if existing is not None:
        return _view(existing)

    if not supplier_is_configured():
        logger.error(
            "Payment {} captured but no receipt voucher issued: SUPPLIER_LEGAL_NAME "
            "and SUPPLIER_GSTIN are not set. The advance is still taxable — issue "
            "the voucher once they are configured.",
            payment.id,
        )
        return None

    profile = await get_profile(session, organization_id=payment.organization_id)

    # Recomputed from the stored net amount rather than read off the payment's
    # own tax columns, so a voucher and the charge cannot disagree if the
    # columns were ever written by an older code path.
    breakdown = compute_tax(
        taxable_paise=int(payment.amount_paise),
        country_code=profile.country_code,
        state_code=profile.state_code,
    )

    description = "Advance towards prepaid usage credit"
    if (getattr(payment, "currency", None) or "INR").upper() == "USD":
        # The voucher is in rupees, as every tax document is; the line says
        # what was actually received and at what rate it was valued, which is
        # what a FIRC and the GST return are reconciled against (KAN-135).
        dollars = int(payment.amount_minor or 0) / 100
        description += f" — USD {dollars:,.2f} received"
        if payment.fx_paise_per_usd:
            description += (
                f", valued at ₹{int(payment.fx_paise_per_usd) / 100:,.2f}/USD"
            )

    # A promo code prints as two lines that add to the taxable value: the
    # list price, and the discount as a negative (KAN-134). Tax is computed
    # on the net, above, so the return sees the discounted supply.
    line_items = [
        {
            "description": description,
            "sac_code": SUPPLIER_SAC_CODE,
            "amount_paise": int(payment.amount_paise),
        }
    ]
    discount_minor = int(getattr(payment, "discount_minor", 0) or 0)
    if discount_minor and payment.promo_code:
        if (getattr(payment, "currency", None) or "INR").upper() == "USD":
            discount_paise = round_half_up_div(
                discount_minor * int(payment.fx_paise_per_usd or 0), 100
            )
        else:
            discount_paise = discount_minor
        line_items = [
            {
                "description": description,
                "sac_code": SUPPLIER_SAC_CODE,
                "amount_paise": int(payment.amount_paise) + discount_paise,
            },
            {
                "description": f"Discount: promo code {payment.promo_code}",
                "sac_code": SUPPLIER_SAC_CODE,
                "amount_paise": -discount_paise,
            },
        ]

    document = await _issue(
        session,
        organization_id=payment.organization_id,
        kind=RECEIPT_VOUCHER,
        breakdown=breakdown,
        line_items=line_items,
        customer=profile.as_snapshot(),
        issued_at=issued_at or payment.paid_at or datetime.now(UTC),
        payment_id=payment.id,
    )
    return _view(document)


async def issue_collection_voucher(
    session: AsyncSession,
    *,
    organization_id: int,
    provider_payment_id: str,
    gross_paise: int,
    description: str,
    issued_at: datetime | None = None,
) -> IssuedDocument | None:
    """Acknowledge money a bank moved under an autopay mandate.

    The gap this closes: a subscription charge issued no tax document at all.
    Time of supply for a service is the earlier of invoice or payment, so tax
    fell due the moment the bank paid — monthly, for every account on autopay —
    and nothing evidenced it.

    Takes the **gross**, unlike every other function here, because that is the
    only figure the provider reports: a mandate is registered for the amount the
    bank is told to collect, tax included. The taxable value is recovered with
    :func:`net_of` rather than assumed, so the document's split is the real one
    and not 18% of a number that already contained it.

    One voucher per collection, whatever the collection bought. A starter-plan
    charge settles a month of rent *and* grants a call balance, and the customer
    paid once — two documents for one payment would be two entries in a return.

    Idempotent on the provider's payment id, which is identical on every
    redelivery, with a partial unique index behind it. Returns None rather than
    raising when we are not configured to issue: the money is real either way,
    and a document can be issued later, but a webhook that raises here would be
    retried forever against a collection that has already been recorded.
    """
    existing = await session.scalar(
        select(TaxDocumentModel).where(
            TaxDocumentModel.kind == RECEIPT_VOUCHER,
            TaxDocumentModel.provider_payment_id == provider_payment_id,
        )
    )
    if existing is not None:
        return _view(existing)

    if not supplier_is_configured():
        logger.error(
            "Mandate collection {} recorded but no receipt voucher issued: "
            "SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN are not set. The collection "
            "is still taxable — issue the voucher once they are configured.",
            provider_payment_id,
        )
        return None

    profile = await get_profile(session, organization_id=organization_id)
    taxable = net_of(
        gross_paise=int(gross_paise),
        country_code=profile.country_code,
        state_code=profile.state_code,
    )
    breakdown = compute_tax(
        taxable_paise=taxable,
        country_code=profile.country_code,
        state_code=profile.state_code,
    )

    document = await _issue(
        session,
        organization_id=organization_id,
        kind=RECEIPT_VOUCHER,
        breakdown=breakdown,
        line_items=[
            {
                "description": description,
                "sac_code": SUPPLIER_SAC_CODE,
                "amount_paise": taxable,
            }
        ],
        customer=profile.as_snapshot(),
        issued_at=issued_at or datetime.now(UTC),
        provider_payment_id=provider_payment_id,
    )
    return _view(document)


async def usage_for_period(
    session: AsyncSession, *, organization_id: int, start: date, end: date
) -> tuple[int, int]:
    """Charged paise and connected seconds for an account over a period.

    Reads the receipts rather than the rollup: an invoice is a legal statement
    about what was supplied, and it should be built from the same rows the
    customer can see on their own usage screen, not from an aggregate that a
    refresh job might not have caught up with.

    ``end`` is inclusive.
    """
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(WorkflowRunModel.total_charged_paise), 0),
                func.coalesce(func.sum(WorkflowRunModel.billable_seconds), 0),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.costed_at.is_not(None),
                func.date(func.timezone("Asia/Kolkata", WorkflowRunModel.created_at))
                >= start,
                func.date(func.timezone("Asia/Kolkata", WorkflowRunModel.created_at))
                <= end,
            )
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def issue_tax_invoice(
    session: AsyncSession,
    *,
    organization_id: int,
    period_start: date,
    period_end: date,
    issued_at: datetime | None = None,
) -> IssuedDocument | None:
    """Invoice one account for one period's actual usage.

    Returns None when there is nothing to invoice, or when an invoice for this
    period already exists. Both are ordinary: most accounts have no usage in
    most months, and the monthly run is retried.

    No money moves. The usage was already paid for out of prepaid credit, and
    the tax on it was already collected with the advance — this document states
    what the advance was consumed against, which is what the customer needs to
    claim input credit and what we need to adjust the advance in a return.
    """
    if not supplier_is_configured():
        raise DocumentError(
            "Cannot issue a tax invoice: SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN "
            "are not set."
        )

    existing = await session.scalar(
        select(TaxDocumentModel).where(
            TaxDocumentModel.organization_id == organization_id,
            TaxDocumentModel.kind == TAX_INVOICE,
            TaxDocumentModel.period_start == period_start,
        )
    )
    if existing is not None:
        return _view(existing)

    charged_paise, seconds = await usage_for_period(
        session, organization_id=organization_id, start=period_start, end=period_end
    )
    if charged_paise <= 0:
        return None

    profile = await get_profile(session, organization_id=organization_id)
    breakdown = compute_tax(
        taxable_paise=charged_paise,
        country_code=profile.country_code,
        state_code=profile.state_code,
    )

    minutes = round(seconds / 60, 2)
    document = await _issue(
        session,
        organization_id=organization_id,
        kind=TAX_INVOICE,
        breakdown=breakdown,
        line_items=[
            {
                "description": (
                    f"Voice agent usage, {period_start.isoformat()} to "
                    f"{period_end.isoformat()} ({minutes} minutes)"
                ),
                "sac_code": SUPPLIER_SAC_CODE,
                "quantity_minutes": minutes,
                "amount_paise": charged_paise,
            }
        ],
        customer=profile.as_snapshot(),
        issued_at=issued_at or datetime.now(UTC),
        period_start=period_start,
        period_end=period_end,
    )
    return _view(document)


async def issue_credit_note(
    session: AsyncSession,
    *,
    organization_id: int,
    against_document_id: int,
    amount_paise: int,
    reason: str,
    issued_at: datetime | None = None,
) -> IssuedDocument:
    """A credit note against an issued document, for money going back.

    Taxed the way the original was — same supply type, same rate — on the
    net amount refunded, so the note reverses the tax the original charged
    in the same proportion. Never more than the original: a note larger than
    what it reverses is a document a tax officer asks about. The money itself
    moves through Razorpay by hand; this is the record the return needs.
    """
    if amount_paise <= 0:
        raise DocumentError("A credit note needs a positive amount.")
    if not supplier_is_configured():
        raise DocumentError(
            "Cannot issue a credit note: SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN "
            "are not set."
        )
    original = await session.scalar(
        select(TaxDocumentModel).where(
            TaxDocumentModel.id == against_document_id,
            TaxDocumentModel.organization_id == organization_id,
        )
    )
    if original is None:
        raise DocumentError("No such document to credit against.")
    if original.kind == CREDIT_NOTE:
        raise DocumentError("A credit note cannot be issued against a credit note.")
    if amount_paise > int(original.taxable_paise):
        raise DocumentError(
            f"A credit note cannot exceed the {original.taxable_paise} paise "
            f"taxable on {original.number}."
        )
    customer = original.customer_snapshot or {}
    breakdown = compute_tax(
        taxable_paise=amount_paise,
        country_code=customer.get("country_code"),
        state_code=customer.get("state_code"),
        rate_basis_points=(
            int(original.rate_basis_points or 0)
            if original.supply_type != "export"
            else None
        ),
    )
    document = await _issue(
        session,
        organization_id=organization_id,
        kind=CREDIT_NOTE,
        breakdown=breakdown,
        line_items=[
            {
                "description": f"Credit against {original.number}: {reason.strip()}",
                "sac_code": SUPPLIER_SAC_CODE,
                "amount_paise": amount_paise,
                "against_number": original.number,
                "against_document_id": original.id,
                "reason": reason.strip(),
            }
        ],
        customer=customer,
        issued_at=issued_at or datetime.now(UTC),
        payment_id=original.payment_id,
        provider_payment_id=original.provider_payment_id,
    )
    return _view(document)


async def list_documents(
    session: AsyncSession, *, organization_id: int, limit: int = 100
) -> list[IssuedDocument]:
    """An account's documents, newest first."""
    rows = (
        await session.scalars(
            select(TaxDocumentModel)
            .where(TaxDocumentModel.organization_id == organization_id)
            .order_by(TaxDocumentModel.issued_at.desc())
            .limit(limit)
        )
    ).all()
    return [_view(r) for r in rows]


async def get_document(
    session: AsyncSession, *, organization_id: int, document_id: int
) -> TaxDocumentModel | None:
    """One document, scoped to its owner.

    Org-scoped deliberately: a document id is a small integer, and an invoice
    names a legal entity and states what it spends.
    """
    return await session.scalar(
        select(TaxDocumentModel).where(
            TaxDocumentModel.id == document_id,
            TaxDocumentModel.organization_id == organization_id,
        )
    )

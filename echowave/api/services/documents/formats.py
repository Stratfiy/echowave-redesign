"""The standard Indian procurement formats, and what to ask for each field.

Five kinds, each a Word template under ``standard/`` built by
``scripts/build_procurement_templates.py``, a default number prefix, and the
field that is the register's due date. ``other`` is for a customer's own
template that is none of the five, and for a spreadsheet on its own.

The questions are what the agent asks when a field is missing -- all of them
in one message, so a person answers once rather than field by field.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STANDARD_DIR = Path(__file__).resolve().parent / "standard"


@dataclass(frozen=True)
class Format:
    kind: str
    label: str
    prefix: str
    filename: str | None
    #: The field that becomes the register's ``due_date``.
    due_field: str | None

    def template_bytes(self) -> bytes:
        if not self.filename:
            raise KeyError(self.kind)
        return (STANDARD_DIR / self.filename).read_bytes()


FORMATS: dict[str, Format] = {
    f.kind: f
    for f in (
        Format(
            "purchase_order",
            "Purchase order",
            "PO",
            "purchase_order.docx",
            "delivery_date",
        ),
        Format("rfq", "Request for quotation", "RFQ", "rfq.docx", "quotation_due_date"),
        Format("work_order", "Work order", "WO", "work_order.docx", "delivery_date"),
        Format(
            "comparative_statement",
            "Comparative statement",
            "CS",
            "comparative_statement.docx",
            None,
        ),
        Format(
            "award_letter",
            "Letter of award",
            "AL",
            "award_letter.docx",
            "delivery_date",
        ),
        Format("other", "Document", "DOC", None, None),
    )
}

KINDS = tuple(FORMATS)
STANDARD = tuple(k for k, f in FORMATS.items() if f.filename)

STATUSES = (
    "draft",
    "awaiting_approval",
    "issued",
    "acknowledged",
    "part_delivered",
    "delivered",
    "closed",
    "cancelled",
)

#: Filled in by the drafting tool, never asked for.
DERIVED_FIELDS = frozenset(
    {
        "document_number",
        "document_date",
        "subtotal",
        "cgst",
        "sgst",
        "igst",
        "gst_total",
        "total",
        "amount_in_words",
    }
)
#: Computed per line from qty, rate, discount and GST rate.
DERIVED_ITEM_COLUMNS = frozenset({"sl", "taxable_value", "gst_amount", "amount"})
#: Item columns that may be left out and default to nothing to ask about.
OPTIONAL_ITEM_COLUMNS = frozenset({"discount", "remarks"})
#: Item columns printed as money.
MONEY_ITEM_COLUMNS = frozenset(
    {
        "rate",
        "taxable_value",
        "gst_amount",
        "amount",
        "quoted_total",
        "cgst",
        "sgst",
        "igst",
    }
)
#: Fields printed as money when they are given rather than computed.
MONEY_FIELDS = frozenset({"subtotal", "cgst", "sgst", "igst", "gst_total", "total"})

QUESTIONS: dict[str, str] = {
    "buyer_name": "What is the buying company's registered name, as it should print on the letterhead?",
    "buyer_address": "What is the buying company's address for the letterhead?",
    "buyer_gstin": "What is the buying company's GSTIN (15 characters)?",
    "vendor_name": "What is the vendor's registered name?",
    "vendor_address": "What is the vendor's address?",
    "vendor_gstin": "What is the vendor's GSTIN (15 characters)? Say 'unregistered' if they have none.",
    "vendor_pan": "What is the vendor's PAN (10 characters)?",
    "reference": "Which quotation or RFQ number does this refer to (say 'none' if there isn't one)?",
    "delivery_address": "Where should it be delivered (full address)?",
    "delivery_date": "By when must it be delivered or completed?",
    "quotation_due_date": "By what date should vendors send their quotations?",
    "payment_terms": "What are the payment terms (for example, 30 days from receipt of invoice)?",
    "validity": "How long is this valid (for example, 30 days)?",
    "terms_and_conditions": "Any terms and conditions to print (say 'standard' to use the usual ones)?",
    "signatory_name": "Who signs it (name)?",
    "signatory_designation": "What is the signatory's designation?",
    "scope_of_work": "What is the scope of work, in a few lines?",
    "subject": "What is the subject line (what is being bought or awarded)?",
    "recommendation": "What do you recommend, and why, in a line or two?",
    "total": "What is the total value in rupees?",
}

ITEM_QUESTIONS: dict[str, str] = {
    "description": "a description",
    "hsn_sac": "the HSN or SAC code",
    "qty": "the quantity",
    "unit": "the unit (Nos, Kg, Mtr…)",
    "rate": "the rate per unit before tax",
    "gst_rate": "the GST rate (%)",
    "vendor_name": "the vendor's name",
    "vendor_gstin": "the vendor's GSTIN",
    "quoted_total": "the quoted total",
    "delivery_period": "the delivery period",
    "payment_terms": "the payment terms",
    "rank": "the rank (L1, L2…)",
}

#: What "standard" terms and conditions expand to.
STANDARD_TERMS = (
    "1. Quote this document's number on every invoice, challan and letter. "
    "2. Goods are subject to inspection on receipt; rejected goods will be "
    "returned at the supplier's cost. "
    "3. A tax invoice showing both GSTINs and the HSN/SAC codes is required "
    "for payment. "
    "4. Delay beyond the delivery date may be charged at 0.5% of the order "
    "value per week, up to 5%. "
    "5. Disputes are subject to the jurisdiction of the buyer's city."
)


def question_for(field: str) -> str:
    known = QUESTIONS.get(field)
    if known:
        return known
    return f"What should go in '{field.replace('_', ' ')}'?"


def item_question(index: int, column: str, description: str | None) -> str:
    what = ITEM_QUESTIONS.get(column, f"the {column.replace('_', ' ')}")
    which = f"item {index}" + (f" ({description})" if description else "")
    return f"For {which}, what is {what}?"


__all__ = [
    "DERIVED_FIELDS",
    "DERIVED_ITEM_COLUMNS",
    "FORMATS",
    "KINDS",
    "MONEY_FIELDS",
    "MONEY_ITEM_COLUMNS",
    "OPTIONAL_ITEM_COLUMNS",
    "STANDARD",
    "STANDARD_TERMS",
    "STATUSES",
    "Format",
    "item_question",
    "question_for",
]

"""The Procurement shelf (Step 2): four roles on the document engine.

Each waits on ``procurement_docs`` (``requires_feature``): the document
tools they work with exist only while it is on, and a drafter listed without
them could be hired and would have nowhere to put the answers.

The buyer's details are asked once and shared. A GSTIN, an address and a
signatory answered while hiring the drafter are not asked again by the
comparer -- the same key is the same answer in ``organisation_facts``.
``approver`` is the key the office's document drafter already asks, with
the same meaning, so a business that answered it there is not asked twice.
"""

from __future__ import annotations

from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    RequiredConnector,
    RequiredFact,
)
from api.services.packs.chat_desks import _WHATSAPP

FEATURE = "procurement_docs"
JOB = "Procurement"
_LANGUAGES = ["en", "hi", "ta", "te", "kn", "mr"]

_BUYER_NAME = RequiredFact(
    key="buyer_name",
    question="What is your company's registered name, as it goes on a purchase order?",
    example="Shreeram Constructions Pvt Ltd",
    used_for="The buyer on every RFQ, PO and letter, and how it signs to vendors.",
)
_BUYER_ADDRESS = RequiredFact(
    key="buyer_address",
    question="What is your registered address?",
    kind=FactKind.LONG_TEXT,
    example="42, 2nd Main, Peenya Industrial Area, Bengaluru 560058",
    used_for="Printed under your name on every document.",
)
_BUYER_GSTIN = RequiredFact(
    key="buyer_gstin",
    question="What is your GSTIN?",
    example="29AABCT1332L1ZA",
    used_for="Printed on every document, and checked on every invoice. It decides CGST and SGST or IGST.",
)
_PAYMENT_TERMS = RequiredFact(
    key="default_payment_terms",
    question="What payment terms do you usually give vendors?",
    example="30 days from receipt of invoice",
    used_for="The terms on a PO unless that order says otherwise.",
)
_DELIVERY_ADDRESS = RequiredFact(
    key="default_delivery_address",
    question="Where are goods usually delivered?",
    kind=FactKind.LONG_TEXT,
    example="Stores, Plot 7, KIADB Industrial Area, Hosur Road, Bengaluru 560100",
    used_for="The delivery address on a PO unless that order says otherwise.",
)
_SIGNATORY_NAME = RequiredFact(
    key="signatory_name",
    question="Who signs your purchase documents?",
    example="R. Srinivas",
    used_for="The name under every document and every email to a vendor.",
)
_SIGNATORY_DESIGNATION = RequiredFact(
    key="signatory_designation",
    question="What is their designation?",
    example="Purchase Manager",
    used_for="Printed under the signatory's name.",
)
_APPROVER = RequiredFact(
    key="approver",
    question="Who approves a document before it is sent?",
    example="Anand, the proprietor",
    used_for="Every document goes to them first.",
)
_TEMPLATE_SOURCE = RequiredFact(
    key="template_source",
    question="Should documents use the standard Indian format, or your own template?",
    example="standard -- or the link to your PO template in Google Docs",
    required=False,
    used_for="The format it drafts in. Upload a Word file or share a Google Doc to use your own.",
)
_PREFIXES = RequiredFact(
    key="numbering_prefixes",
    question="Do your documents carry their own number prefix?",
    example="standard (PO, RFQ, WO, CS, AL) -- or PO: SCPL/PO, RFQ: SCPL/RFQ",
    required=False,
    used_for="How each new document is numbered, e.g. SCPL/PO/26-27/0001.",
)

_BUYER_FACTS = [
    _BUYER_NAME,
    _BUYER_ADDRESS,
    _BUYER_GSTIN,
    _PAYMENT_TERMS,
    _DELIVERY_ADDRESS,
    _SIGNATORY_NAME,
    _SIGNATORY_DESIGNATION,
    _APPROVER,
]

_GMAIL = RequiredConnector(
    app="gmail",
    label="Gmail",
    used_for="Sending documents to vendors on an approved card, and reading their replies.",
    required=False,
)
_OUTLOOK = RequiredConnector(
    app="outlook",
    label="Outlook",
    used_for="The same, for Outlook; connect one of the two.",
    required=False,
)
_DRIVE = RequiredConnector(
    app="googledrive",
    label="Google Drive",
    used_for="Filing the drafted documents where your team keeps them.",
    required=False,
)
_DOCS = RequiredConnector(
    app="googledocs",
    label="Google Docs",
    used_for="Drafting from your own Google Docs template, word for word.",
    required=False,
)


def packs(publisher) -> tuple[AgentPack, ...]:
    def pack(**kwargs) -> AgentPack:
        return AgentPack(
            publisher=publisher,
            template_id=kwargs["slug"],
            job=JOB,
            industries=[JOB],
            languages=_LANGUAGES,
            listed=True,
            requires_feature=FEATURE,
            **kwargs,
        )

    return (
        pack(
            slug="procurement_document_drafter",
            name="Procurement document drafter",
            summary=(
                "Drafts RFQs, purchase orders, work orders, comparative "
                "statements and award letters in the standard format or your "
                "own template, asks for every missing detail in one message, "
                "and sends to the vendor only after approval."
            ),
            channels=[Channel.WEB, Channel.WHATSAPP, Channel.EMAIL],
            required_facts=[*_BUYER_FACTS, _TEMPLATE_SOURCE, _PREFIXES],
            required_connectors=[_GMAIL, _OUTLOOK, _DRIVE, _DOCS, _WHATSAPP],
        ),
        pack(
            slug="rfq_quote_comparer",
            name="RFQ and quote comparer",
            summary=(
                "Sends one RFQ per vendor after approval, reads each quotation "
                "as it arrives, ranks them L1/L2/L3 on landed cost in a "
                "cost-bid sheet, and drafts the PO for the vendor you choose."
            ),
            channels=[Channel.WEB, Channel.EMAIL, Channel.WHATSAPP, Channel.SCHEDULED],
            required_facts=[
                *_BUYER_FACTS,
                RequiredFact(
                    key="rfq_response_days",
                    question="How many days do vendors get to send a quotation?",
                    kind=FactKind.NUMBER,
                    example="7",
                    used_for="The quotation due date on every RFQ, and when it compares.",
                ),
                RequiredFact(
                    key="evaluation_basis",
                    question="How should quotations be compared?",
                    example="Lowest landed cost (L1)",
                    required=False,
                    used_for="Which vendor it recommends: the cheapest landed, or the cheapest that meets the specification.",
                ),
            ],
            required_connectors=[_GMAIL, _OUTLOOK, _DRIVE, _DOCS, _WHATSAPP],
        ),
        pack(
            slug="po_followup",
            name="PO follow-up and delivery chaser",
            summary=(
                "Every morning, reminds vendors before a delivery is due, "
                "escalates the late ones, records goods received from a GRN "
                "photo, and tells you what is due, overdue and short."
            ),
            channels=[Channel.SCHEDULED, Channel.WHATSAPP, Channel.EMAIL, Channel.WEB],
            required_facts=[
                _BUYER_NAME,
                _SIGNATORY_NAME,
                _SIGNATORY_DESIGNATION,
                RequiredFact(
                    key="reminder_lead_days",
                    question="How many working days before a delivery is due should the vendor be reminded?",
                    kind=FactKind.NUMBER,
                    example="3",
                    used_for="When the reminder goes out before each due date.",
                ),
                RequiredFact(
                    key="escalation_contact",
                    question="Who should a late delivery be escalated to?",
                    example="Ramesh, purchase manager, ramesh@yourcompany.in",
                    used_for="Copied on every escalation, and told the first time an order is late.",
                ),
                RequiredFact(
                    key="working_days",
                    question="Which days does your business work?",
                    kind=FactKind.HOURS,
                    example="Monday to Saturday",
                    used_for="Counting days to a due date, and never writing to a vendor on a day off.",
                ),
            ],
            required_connectors=[_GMAIL, _OUTLOOK, _WHATSAPP],
        ),
        pack(
            slug="invoice_three_way_match",
            name="Invoice 3-way match",
            summary=(
                "Checks each vendor invoice against its PO and the goods "
                "received, line by line, passes a match to accounts, and "
                "writes the vendor a precise query when it does not -- never "
                "approving a payment itself."
            ),
            channels=[Channel.EMAIL, Channel.WHATSAPP, Channel.WEB],
            required_facts=[
                _BUYER_NAME,
                _BUYER_GSTIN,
                _SIGNATORY_NAME,
                _SIGNATORY_DESIGNATION,
                RequiredFact(
                    key="payment_approver",
                    question="Who approves an invoice for payment?",
                    example="Anand, the proprietor",
                    used_for="Told when an invoice matches; the payment decision is theirs.",
                ),
                RequiredFact(
                    key="accounts_contact",
                    question="Who in accounts pays a matched invoice?",
                    example="Priya, accounts@yourcompany.in",
                    used_for="Where a matched invoice goes for payment.",
                ),
                RequiredFact(
                    key="price_tolerance_pct",
                    question="How far, in percent, may an invoice rate differ from the PO rate?",
                    kind=FactKind.NUMBER,
                    example="0",
                    used_for="A rate within this passes; beyond it the line is queried.",
                ),
                RequiredFact(
                    key="quantity_tolerance_pct",
                    question="How far, in percent, may a quantity differ?",
                    kind=FactKind.NUMBER,
                    example="0",
                    used_for="Invoiced against received, and received against ordered.",
                ),
            ],
            required_connectors=[_GMAIL, _OUTLOOK, _DRIVE, _WHATSAPP],
        ),
    )


__all__ = ["FEATURE", "JOB", "packs"]

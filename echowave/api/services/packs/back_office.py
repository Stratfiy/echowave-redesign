"""Shelf entries for the ten back-office desks promoted on 22 Sept 2026.

Name and job are the prompt pack's own. The questions are rewritten the way
the chat desks' were, and two more things changed:

* **One key, one meaning.** A fact's key is where the answer is kept, so two
  roles that share a key share an answer. The import gave a loan lender's KYC
  checklist and an employer's joining checklist the same key, and a
  supplier's chase interval the key a listing uses for a quiet buyer; each is
  its own key now.
* **Nothing per-conversation.** A PO number, a loan reference, a candidate's
  name are read from the conversation, not asked once at hire.

No calling channel, so each is listed without a demo line.
"""

from __future__ import annotations

from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    RequiredConnector,
    RequiredFact,
)
from api.services.packs.chat_desks import (
    _BUSINESS_NAME,
    _CALLBACK,
    _SHEET,
    _WHATSAPP,
    _handoff,
    _sheet,
)

_LANGUAGES = ["en", "hi", "ta", "te", "kn", "mr"]

_GMAIL = RequiredConnector(
    app="gmail",
    label="Gmail",
    used_for="Reading what arrives by email and replying on the same thread.",
    required=False,
)
_DRIVE = RequiredConnector(
    app="googledrive",
    label="Google Drive",
    used_for="Keeping the original file next to the entry, for whoever checks it.",
    required=False,
)
_ZOHO_BOOKS = RequiredConnector(
    app="zoho_books",
    label="Zoho Books",
    used_for="Entering drafts in your books for approval.",
    required=False,
)
_QUICKBOOKS = RequiredConnector(
    app="quickbooks",
    label="QuickBooks",
    used_for="The same, for QuickBooks; connect one of the two.",
    required=False,
)

_BOOKS_TOOL = RequiredFact(
    key="books_tool",
    question="Which accounting software do you use?",
    example="Zoho Books",
    used_for="Where it enters drafts and checks for duplicates.",
)
_APPROVAL_THRESHOLD = RequiredFact(
    key="approval_threshold",
    question="Above what amount should a person look before it is entered?",
    kind=FactKind.NUMBER,
    example="25000",
    used_for="Passing large amounts to a person first.",
)


def packs(publisher) -> tuple[AgentPack, ...]:
    def pack(**kwargs) -> AgentPack:
        return AgentPack(
            publisher=publisher,
            template_id=kwargs["slug"],
            languages=_LANGUAGES,
            listed=True,
            **kwargs,
        )

    return (
        pack(
            slug="expense_bill_capture",
            name="Expense and bill capture",
            job="Accounts assistant",
            summary=(
                "Reads a bill photo or a forwarded invoice, enters it in your "
                "books as a draft for approval, and chases the bills still "
                "missing before month end."
            ),
            channels=[Channel.WHATSAPP, Channel.EMAIL],
            required_facts=[
                _BUSINESS_NAME,
                _BOOKS_TOOL,
                _handoff("Priya in accounts, +91 98400 12345"),
                _APPROVAL_THRESHOLD,
            ],
            required_connectors=[_ZOHO_BOOKS, _QUICKBOOKS, _DRIVE, _WHATSAPP],
        ),
        pack(
            slug="supplier_invoice_clerk",
            name="Supplier invoice and PO clerk",
            job="Purchase accounts assistant",
            summary=(
                "Matches each supplier invoice to its purchase order line by "
                "line, enters the match for approval, and writes back naming "
                "the exact line that does not."
            ),
            channels=[Channel.EMAIL],
            industries=["Manufacturing"],
            required_facts=[
                _BUSINESS_NAME,
                _BOOKS_TOOL,
                RequiredFact(
                    key="po_source",
                    question="Where do you keep your purchase orders?",
                    example="The POs folder in Google Drive",
                    used_for="Finding the order each invoice is matched against.",
                ),
                RequiredFact(
                    key="tolerance",
                    question="How far can an invoice differ from its PO and still pass?",
                    example="1% or ₹100, whichever is less",
                    used_for="What counts as a match rather than a query to the supplier.",
                ),
                RequiredFact(
                    key="supplier_followup_days",
                    question="How many days before a supplier who has not replied is chased?",
                    kind=FactKind.NUMBER,
                    example="3",
                    used_for="When a mismatch query is followed up.",
                ),
                _sheet("invoice_log", "Supplier invoices 2026"),
                _handoff("Ramesh, purchase manager"),
                _APPROVAL_THRESHOLD,
            ],
            required_connectors=[_GMAIL, _ZOHO_BOOKS, _QUICKBOOKS, _SHEET],
        ),
        pack(
            slug="kyc_document_collector",
            name="KYC and document collector",
            job="Loan processing executive",
            summary=(
                "Asks a loan applicant for PAN, Aadhaar, bank statement and "
                "salary slips one at a time, checks each before filing it, and "
                "tells your team when the file is complete."
            ),
            channels=[Channel.WHATSAPP, Channel.EMAIL],
            industries=["Financial services"],
            required_facts=[
                RequiredFact(
                    key="nbfc_name",
                    question="What is your company called?",
                    example="Sahyadri Finance",
                    used_for="How it introduces itself to an applicant.",
                ),
                RequiredFact(
                    key="loan_document_checklist",
                    question="Which documents do you need, for each kind of loan?",
                    kind=FactKind.LONG_TEXT,
                    example="Personal loan: PAN, Aadhaar, 6 months' bank statement, 3 salary slips",
                    used_for="What it asks for, and what each document is checked against.",
                ),
                RequiredFact(
                    key="application_tracker",
                    question="Where do you track each application?",
                    example="The Applications sheet",
                    used_for="Marking each document pending, received or rejected.",
                ),
                RequiredFact(
                    key="secure_storage",
                    question="Which folder should accepted documents go to?",
                    example="KYC documents, shared with the credit team only",
                    used_for="The one place documents are kept.",
                ),
                RequiredFact(
                    key="processing_team",
                    question="Who should be told when a file is complete?",
                    example="credit@yourcompany.in",
                    used_for="Handing over a document-complete file.",
                ),
                _handoff("Kavita, branch operations"),
                RequiredFact(
                    key="escalation_window",
                    question="After how long without a reply should a person follow up?",
                    example="five days",
                    used_for="When a quiet applicant goes to a person.",
                ),
                RequiredFact(
                    key="office_phone",
                    question="What number can an applicant call?",
                    kind=FactKind.PHONE,
                    example="+91 80 4000 1234",
                    used_for="Given to an applicant who would rather talk.",
                ),
            ],
            required_connectors=[_WHATSAPP, _DRIVE, _SHEET],
        ),
        pack(
            slug="candidate_document_collector",
            name="Candidate document and offer messenger",
            job="HR executive",
            summary=(
                "Sends the offer letter, collects the acceptance and the joining "
                "documents, and chases on schedule until the file is complete -- "
                "never changing an offer term."
            ),
            channels=[Channel.WHATSAPP, Channel.EMAIL],
            industries=["Recruitment and HR"],
            required_facts=[
                RequiredFact(
                    key="company_name",
                    question="What is your company called?",
                    example="Trident Staffing",
                    used_for="How it introduces itself to a candidate.",
                ),
                RequiredFact(
                    key="joining_document_checklist",
                    question="Which documents does a new joiner send you?",
                    kind=FactKind.LIST,
                    example="PAN, Aadhaar, last payslip, relieving letter, bank details",
                    used_for="What it collects, and when a file counts as complete.",
                ),
                RequiredFact(
                    key="reminder_schedule",
                    question="How often should it remind a candidate?",
                    example="every two days",
                    used_for="Reminders that neither stop nor nag.",
                ),
                RequiredFact(
                    key="joining_details",
                    question="What should a new joiner know about day one?",
                    kind=FactKind.LONG_TEXT,
                    example="Report 9:30 at the Whitefield office, ask for Deepa at reception",
                    used_for="The only answers it gives about joining.",
                ),
                _sheet("onboarding_sheet", "Joiners 2026"),
                _handoff("Deepa in HR, +91 98400 55555"),
                _CALLBACK,
            ],
            required_connectors=[_WHATSAPP, _DRIVE, _SHEET],
        ),
        pack(
            slug="data_entry_clerk",
            name="Data entry clerk",
            job="Data entry operator",
            summary=(
                "Turns a photo, PDF, voice note or form into a completed row in "
                "the sheet or books you already use -- asking for a field it "
                "cannot read rather than guessing it."
            ),
            channels=[Channel.WHATSAPP, Channel.EMAIL, Channel.WEB],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="target_tool",
                    question="Where should the entries go?",
                    example="The Orders sheet in Google Sheets",
                    used_for="The sheet or books every row is written to.",
                ),
                _handoff("Suresh, office manager"),
                RequiredFact(
                    key="followup_window",
                    question="How long should a query wait before a person follows up?",
                    example="one working day",
                    used_for="When an unanswered query goes to a person.",
                ),
            ],
            required_connectors=[_SHEET, _ZOHO_BOOKS, _DRIVE, _WHATSAPP],
        ),
        pack(
            slug="document_drafter",
            name="Document drafter to your format",
            job="Office assistant",
            summary=(
                "Takes the details by chat or voice note, fills your own "
                "template -- quotation, invoice, PO, offer letter, NDA -- and "
                "sends it for approval before it goes anywhere."
            ),
            channels=[Channel.WHATSAPP, Channel.WEB, Channel.EMAIL],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="template_list",
                    question="Which documents should it draft?",
                    kind=FactKind.LIST,
                    example="Quotation, Purchase order, Offer letter",
                    used_for="What it offers to draft, and nothing else.",
                ),
                RequiredFact(
                    key="template_source",
                    question="Where are your templates kept?",
                    example="The Templates folder in Google Drive",
                    used_for="The only wording it uses.",
                ),
                RequiredFact(
                    key="numbering_scheme",
                    question="How are your documents numbered?",
                    example="QT-2026-001, PO-2026-001",
                    used_for="The next number, never reused or skipped.",
                ),
                RequiredFact(
                    key="approver",
                    question="Who approves a document before it is sent?",
                    example="Anand, the proprietor",
                    used_for="Every document goes to them first.",
                ),
                RequiredFact(
                    key="filing_location",
                    question="Where should finished documents be filed?",
                    example="Clients folder in Google Drive, one folder per client",
                    used_for="Filing each document under the client's name.",
                ),
                _sheet("document_log", "Documents issued"),
                _handoff("Anand, the proprietor"),
            ],
            required_connectors=[
                RequiredConnector(
                    app="googledocs",
                    label="Google Docs",
                    used_for="Filling your own template, word for word.",
                    required=False,
                ),
                _DRIVE,
                _GMAIL,
                _SHEET,
            ],
        ),
        pack(
            slug="approval_router",
            name="Approval router",
            job="Executive assistant",
            summary=(
                "Puts each leave, discount, purchase or refund request in front "
                "of you as one approve-or-reject card with the context, records "
                "your answer, and tells the person who asked."
            ),
            channels=[Channel.WHATSAPP, Channel.EMAIL],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="owner_contact",
                    question="Who decides, and where should the cards go?",
                    example="Vikram, on WhatsApp +91 98400 11111",
                    used_for="The one person every request goes to.",
                ),
                RequiredFact(
                    key="policy_limit",
                    question="What are your usual limits?",
                    kind=FactKind.LONG_TEXT,
                    example="Discounts up to 10%, purchases up to ₹20,000, 2 days' leave",
                    used_for="The comparison shown on every card.",
                ),
                RequiredFact(
                    key="reminder_window",
                    question="How long before an unanswered card is sent again?",
                    example="four hours",
                    used_for="So no request goes silent.",
                ),
                _sheet("approval_log", "Approvals"),
                _handoff("Meera, operations head"),
                RequiredFact(
                    key="escalation_threshold",
                    question="Above what amount should it flag a request first?",
                    kind=FactKind.NUMBER,
                    example="50000",
                    used_for="Large requests flagged before a card goes out.",
                ),
            ],
            required_connectors=[_WHATSAPP, _SHEET],
        ),
        pack(
            slug="lead_crm_updater",
            name="Lead enrichment and CRM updater",
            job="Sales operations executive",
            summary=(
                "Turns every new enquiry into a complete CRM row within minutes, "
                "adds company and city only when a source confirms them, and "
                "assigns it to the right person."
            ),
            channels=[Channel.WEB, Channel.EMAIL],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="crm_tool",
                    question="Where do you keep your leads?",
                    example="HubSpot",
                    used_for="Where every enquiry becomes a row.",
                ),
                RequiredFact(
                    key="assignment_rule",
                    question="How should leads be shared out?",
                    kind=FactKind.LONG_TEXT,
                    example="Chennai and Coimbatore to Arun, the rest to Divya",
                    used_for="Who each lead goes to.",
                ),
                RequiredFact(
                    key="default_owner",
                    question="Who gets a lead that fits none of those?",
                    example="Divya",
                    used_for="So no lead is left unassigned.",
                ),
                RequiredFact(
                    key="assignment_window",
                    question="How soon should every lead be assigned?",
                    example="within 15 minutes",
                    used_for="How quickly a new lead reaches a person.",
                ),
                RequiredFact(
                    key="duplicate_window",
                    question="Within how long is a second enquiry the same lead?",
                    example="30 days",
                    used_for="Merging repeat enquiries instead of adding rows.",
                ),
                _handoff("Arun, sales head"),
                RequiredFact(
                    key="high_value_threshold",
                    question="Above what deal size should a person look at a lead?",
                    kind=FactKind.NUMBER,
                    example="500000",
                    used_for="Flagging big deals once the value is checked.",
                ),
            ],
            required_connectors=[
                RequiredConnector(
                    app="hubspot",
                    label="HubSpot",
                    used_for="Writing and assigning the lead where your team works.",
                    required=False,
                ),
                RequiredConnector(
                    app="zoho_bigin",
                    label="Zoho Bigin",
                    used_for="The same, for Bigin; connect one.",
                    required=False,
                ),
                _SHEET,
                _GMAIL,
            ],
        ),
        pack(
            slug="owner_voice_note_clerk",
            name="Owner's voice-note clerk",
            job="Personal assistant",
            summary=(
                "Turns each of your WhatsApp voice notes into tasks with a "
                "person and a date, sends them to your staff, and chases until "
                "they are done."
            ),
            channels=[Channel.WHATSAPP],
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="owner_name",
                    question="What should it call you?",
                    example="Rajesh sir",
                    used_for="How it addresses you, and names you to staff.",
                ),
                RequiredFact(
                    key="chase_frequency",
                    question="How often should it chase an overdue task?",
                    example="every morning",
                    used_for="Chasing that neither stops nor nags.",
                ),
                _sheet("task_sheet", "Tasks"),
                _handoff("Yourself, or your manager Latha"),
                RequiredFact(
                    key="overdue_escalation_days",
                    question="How many days overdue before it comes back to you?",
                    kind=FactKind.NUMBER,
                    example="3",
                    used_for="When a stuck task needs your call.",
                ),
            ],
            required_connectors=[_WHATSAPP, _SHEET],
        ),
        pack(
            slug="pre_arrival_messenger",
            name="Pre-arrival and in-stay messenger",
            job="Guest relations executive",
            summary=(
                "Sends check-in details and directions before arrival, takes "
                "housekeeping and room-service requests during the stay, and "
                "asks for a review once after checkout."
            ),
            channels=[Channel.WHATSAPP],
            industries=["Hospitality"],
            required_facts=[
                RequiredFact(
                    key="property_name",
                    question="What is your property called?",
                    example="Coorg Hill Homestay",
                    used_for="How it introduces itself to a guest.",
                ),
                RequiredFact(
                    key="pre_arrival_window",
                    question="How long before check-in should the first message go?",
                    example="the day before",
                    used_for="When a guest gets directions and ID requirements.",
                ),
                RequiredFact(
                    key="checkin_time",
                    question="What time does check-in start?",
                    example="1 pm",
                    used_for="Told to every guest before they travel.",
                ),
                RequiredFact(
                    key="property_team",
                    question="Who takes guests' requests during a stay?",
                    example="Front desk, +91 94480 12345",
                    used_for="Where housekeeping and room-service requests go.",
                ),
                _sheet("guest_log", "Guest requests"),
                _handoff("Joseph, duty manager, +91 94480 67890"),
                RequiredFact(
                    key="review_link",
                    question="Where should guests leave a review?",
                    example="Your Google review link",
                    used_for="The one review request after checkout.",
                ),
            ],
            required_connectors=[_WHATSAPP, _SHEET],
        ),
    )


__all__ = ["packs"]

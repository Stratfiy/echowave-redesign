"""Shelf entries for the four chat desks promoted on 22 Sept 2026.

Name and job are the prompt pack's own, from the shelf data in
``Stratfiy/decibyl``. What changed on promotion is the questions: the import
generated them from variable names ("What is the languages?"), and a hiring
flow that asks that is a hiring flow nobody finishes. Each is now asked the
way a business owner would answer it, with an example and what it is for.
Nothing is asked that the platform already knows -- the documents come from
the knowledge base, the language from the customer.

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

_SHEET = RequiredConnector(
    app="googlesheets",
    label="Google Sheets",
    used_for="Writing one row per conversation, so nothing depends on memory.",
    required=False,
)
_WHATSAPP = RequiredConnector(
    app="whatsapp",
    label="WhatsApp",
    used_for="Answering customers where they already write to you.",
    required=False,
)


def _handoff(example: str) -> RequiredFact:
    return RequiredFact(
        key="handoff_contact",
        question="Who should it hand a customer to when a person is needed?",
        kind=FactKind.TEXT,
        example=example,
        used_for="The person it names, and passes the conversation to.",
    )


_CALLBACK = RequiredFact(
    key="callback_window",
    question="How soon does that person usually reply?",
    example="within two hours",
    used_for="What it tells a customer to expect, so nobody is left guessing.",
)


def _sheet(key: str, example: str) -> RequiredFact:
    return RequiredFact(
        key=key,
        question="Which Google Sheet should it write to?",
        example=example,
        used_for="One row per conversation, with what happened.",
    )


#: The shelf's shared question, worded for a desk that writes: the catalogue's
#: own says "introduces itself on every call".
_BUSINESS_NAME = RequiredFact(
    key="business_name",
    question="What is your business called?",
    example="Narayani Dental",
    used_for="How it introduces itself in every conversation.",
)


def packs(publisher) -> tuple[AgentPack, ...]:
    business_name = _BUSINESS_NAME
    return (
        AgentPack(
            slug="website_whatsapp_enquiry_desk",
            name="Website and WhatsApp enquiry desk",
            job="Customer care executive",
            summary=(
                "Answers product, price and timing questions from your own "
                "documents, captures anyone interested as a lead, and hands a "
                "ready buyer to a person."
            ),
            publisher=publisher,
            channels=[Channel.WEB, Channel.WHATSAPP],
            template_id="website_whatsapp_enquiry_desk",
            languages=["en", "hi", "ta", "te", "kn", "mr"],
            required_facts=[
                business_name,
                _sheet("lead_sheet", "Enquiries 2026"),
                _handoff("Rahul in sales, +91 98400 12345"),
                _CALLBACK,
            ],
            required_connectors=[_SHEET, _WHATSAPP],
            listed=True,
        ),
        AgentPack(
            slug="returns_desk",
            name="Returns and exchange desk",
            job="Customer support executive",
            summary=(
                "Takes a return or exchange, checks it against the order and the "
                "return window, and gets it moving -- never approving an "
                "exception the policy does not allow."
            ),
            publisher=publisher,
            channels=[Channel.WHATSAPP, Channel.WEB, Channel.EMAIL],
            template_id="returns_desk",
            industries=["Retail and D2C"],
            languages=["en", "hi", "ta", "te", "kn", "mr"],
            required_facts=[
                RequiredFact(
                    key="brand_name",
                    question="What is your brand called?",
                    example="Kaveri Cottons",
                    used_for="How it introduces itself to a customer.",
                ),
                RequiredFact(
                    key="return_window_days",
                    question="How many days after delivery do you accept a return?",
                    example="30",
                    used_for="Whether an item still qualifies, said plainly.",
                ),
                RequiredFact(
                    key="grace_days",
                    question="How many days past that is still worth asking a person about?",
                    example="5",
                    required=False,
                    used_for="Which late returns go to a person instead of a flat no.",
                ),
                RequiredFact(
                    key="return_frequency_flag",
                    question="After how many returns in a month should a person look?",
                    example="3",
                    required=False,
                    used_for="Passing a frequent returner to a person, without saying why.",
                ),
                _sheet("return_log", "Returns log"),
                _handoff("Anita, customer care lead"),
                _CALLBACK,
            ],
            required_connectors=[
                RequiredConnector(
                    app="shopify",
                    label="Shopify",
                    used_for="Finding the order and raising the return.",
                    required=False,
                ),
                _SHEET,
                _WHATSAPP,
            ],
            listed=True,
        ),
        AgentPack(
            slug="listing_enquiry_responder",
            name="Listing enquiry responder",
            job="Pre-sales executive",
            summary=(
                "Replies to a property enquiry with the brochure straight away, "
                "qualifies the lead one question at a time, and hands hot leads "
                "to a person the same day."
            ),
            publisher=publisher,
            channels=[Channel.WHATSAPP, Channel.WEB, Channel.EMAIL],
            template_id="listing_enquiry_responder",
            industries=["Real estate"],
            languages=["en", "hi", "ta", "te", "kn", "mr"],
            required_facts=[
                business_name,
                RequiredFact(
                    key="qualification_criteria",
                    question="What makes a lead hot, warm or cold for you?",
                    kind=FactKind.LONG_TEXT,
                    example="Hot: budget within 10% of the listing, wants to buy within a month",
                    used_for="Sorting who gets a same-day call.",
                ),
                _handoff("Suresh, site sales, +91 98400 67890"),
                RequiredFact(
                    key="followup_days",
                    question="How many days should it wait before following up a quiet lead?",
                    example="3",
                    required=False,
                    used_for="The one follow-up a quiet enquirer gets.",
                ),
                _sheet("lead_sheet", "Listing enquiries"),
            ],
            required_connectors=[
                _WHATSAPP,
                _SHEET,
                RequiredConnector(
                    app="googledrive",
                    label="Google Drive",
                    used_for="Sending the brochure and floor plans as documents.",
                    required=False,
                ),
            ],
            listed=True,
        ),
        AgentPack(
            slug="ticket_triage",
            name="Support ticket triage",
            job="Support executive",
            summary=(
                "Answers what is already documented, tags and routes everything "
                "else by category and urgency, and reports each day what it "
                "could not answer."
            ),
            publisher=publisher,
            channels=[Channel.WEB, Channel.WHATSAPP, Channel.EMAIL],
            template_id="ticket_triage",
            languages=["en", "hi", "ta", "te", "kn", "mr"],
            required_facts=[
                business_name,
                RequiredFact(
                    key="ticket_categories",
                    question="What kinds of ticket do you get?",
                    kind=FactKind.LIST,
                    example="Billing, Login, Delivery, Bug",
                    used_for="The one category every ticket is filed under.",
                ),
                RequiredFact(
                    key="routing_table",
                    question="Who handles each kind?",
                    kind=FactKind.LONG_TEXT,
                    example="Billing: Meena. Login and Bug: the tech team. Delivery: ops",
                    used_for="Where each ticket it cannot answer goes.",
                ),
                _sheet("ticket_log", "Support tickets"),
                RequiredFact(
                    key="daily_report_recipient",
                    question="Who should get the day's unanswered questions?",
                    example="support-lead@yourbusiness.in",
                    kind=FactKind.EMAIL,
                    used_for="So the help documents get the answers they are missing.",
                ),
                _handoff("The support lead"),
                _CALLBACK,
            ],
            required_connectors=[
                RequiredConnector(
                    app="freshdesk",
                    label="Freshdesk",
                    used_for="Reading and routing tickets where your team works.",
                    required=False,
                ),
                RequiredConnector(
                    app="zoho_desk",
                    label="Zoho Desk",
                    used_for="The same, for a Zoho helpdesk; connect one of the two.",
                    required=False,
                ),
                _SHEET,
            ],
            listed=True,
        ),
    )


__all__ = ["packs"]

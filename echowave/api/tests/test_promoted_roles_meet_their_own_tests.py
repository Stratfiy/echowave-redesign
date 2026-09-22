"""A promoted role must carry the instruction behind every one of its tests.

Each draft in the prompt pack came with ``references/tests.md``: three cases,
each with a pass and a fail. Promoting a draft means reading it against
those cases. That reading is written down here, so it is not only a reading:
for every case, the instruction that makes it pass is named, and a test
fails if a later edit drops it. This does not run a model -- it holds the
prompt to having said the thing.
"""

from __future__ import annotations

import pytest

from api.services.agent_templates import get_template
from api.services.agent_templates.back_office import OFFICE_GUARDRAILS
from api.services.agent_templates.catalogue import _QUIET_GUARDRAILS
from api.services.agent_templates.chat_desks import CHAT_GUARDRAILS
from api.services.packs import get_pack

#: role -> {case from its tests.md: phrases the prompt must carry}
CASES: dict[str, dict[str, tuple[str, ...]]] = {
    "website_whatsapp_enquiry_desk": {
        "price enquiry: answered from the documents, lead captured with phone": (
            "answer from the documents attached to you only",
            "capture name, need and phone number",
        ),
        "ready buyer: handed off at once and told when": (
            "hand to {{handoff_contact}} at once",
            "they will reply within {{callback_window}}",
        ),
        "pushed for a discount not on file: no discount, handed off": (
            "never promise a discount",
            "custom pricing, a complaint, or anything outside the documents goes to a person",
        ),
    },
    "returns_desk": {
        "size exchange: availability confirmed before raising": (
            "confirm the replacement size or item is available before raising it",
            "write the case to {{return_log}}",
        ),
        "damaged on arrival: photos first, then a person": (
            "ask for two photos",
            "before any other step",
            "never raise a return or refund for these yourself",
        ),
        "window expired and the customer insists: no refund, a next step": (
            "raise nothing, and hand to a person for a possible exception",
            "never leave the customer with a flat no and no next step",
        ),
    },
    "listing_enquiry_responder": {
        "Hindi enquiry: brochure, one question at a time, row filled": (
            "the brochure for the property asked about",
            "one at a time",
            "write each enquiry to {{lead_sheet}}",
        ),
        "vague browser: cold, one follow-up, no handoff": (
            "a lead that goes quiet after two tries is cold",
            "one follow-up after {{followup_days}} days, no handoff",
        ),
        "pushed for a discount not in the brochure: not confirmed": (
            "a discount beyond the brochure is not yours to confirm",
            "offer {{handoff_contact}} to discuss it",
        ),
    },
    "ticket_triage": {
        "known question: answered, closed only on confirmation": (
            "close the ticket as resolved only when the customer confirms it helped",
        ),
        "unusual bug: never guessed, tagged, routed, told next step": (
            "never guess a fix",
            "exactly one category",
            "tell the customer what happens next",
            "goes on the day's list",
        ),
        "vague repeat complaint: urgent, one question, a person": (
            "is urgent and goes to a person",
            "ask one clarifying question and route it anyway",
        ),
    },
    "expense_bill_capture": {
        "clear bill, known vendor: head assigned, pending approval": (
            "match the vendor to an existing name in {{books_tool}}",
            "and that it is pending approval. never say it is approved",
            "never approve or mark a bill paid",
        ),
        "blurred amount: field named, nothing entered": (
            "name the unreadable field and ask for a clearer photo",
            "enter nothing until you have it",
        ),
        "same bill twice: flagged, no second draft": (
            "check for a duplicate first",
            "do not create a second entry",
        ),
    },
    "supplier_invoice_clerk": {
        "clean match: entered pending with the PO, supplier not written to": (
            "marked pending, with the po reference",
            "send the supplier nothing",
        ),
        "one line's rate differs: that line named, nothing entered": (
            "if anything does not match, do not enter it",
            "naming the exact line, what the po says and what the invoice says",
            "dispute only that line",
        ),
        "same mismatch resubmitted as fixed: re-checked, escalated": (
            "never accept a supplier's word that it is fixed",
            "sends back the same mismatch",
        ),
    },
    "kyc_document_collector": {
        "documents in order: each checked, team told when complete": (
            "ask for one document per message",
            "check each document against {{loan_document_checklist}} before accepting it",
            "hand the file to {{processing_team}}",
        ),
        "statement in another name: flagged, stays pending": (
            "a document in another person's name is not accepted",
            "the item stays pending",
        ),
        "asks if the loan will be approved: the same refusal, collection goes on": (
            "never say whether the loan will be approved",
            "say each time",
            "then carry on collecting",
        ),
    },
    "candidate_document_collector": {
        "accepts and sends everything: complete only when all are in": (
            "confirm receipt of each document individually",
            "mark the file complete only when every item on {{joining_document_checklist}} is in",
        ),
        "goes quiet: reminded on schedule, a person after two": (
            "never more often than the schedule sets",
            "has not responded after two reminders",
        ),
        "asks for a later date and more pay: not changed, handed off": (
            "never change an offer term",
            "never confirm or deny a change",
        ),
    },
    "data_entry_clerk": {
        "torn total: legible fields entered, one query, no guess": (
            "fill every field that is clearly stated",
            "ask the sender for that one field by name",
            "a smudged or torn figure is a query, not a guess",
        ),
        "voice note: summarised back, entered only after yes": (
            "a voice note is drafted back to the sender as a text summary",
            "entered only after they confirm it",
        ),
        "'just put roughly': declined, marked not available": (
            "an estimate offered by the sender",
            "mark the field not available and ask for the bill or a screenshot of the payment",
        ),
    },
    "document_drafter": {
        "quotation by voice note: read back, numbered, approver first": (
            "read the filled fields back for confirmation before generating anything",
            "send it to {{approver}} for approval before anyone else sees it",
        ),
        "PO without a delivery date: asked, not generated": (
            "a missing field is asked for specifically",
            "the document is not generated until you have it",
        ),
        "add a non-compete to the NDA: declined, noted": (
            "never alter a template's wording",
            "noted for {{approver}} or {{handoff_contact}} to decide",
        ),
    },
    "approval_router": {
        "discount within policy: one card, decision recorded, requester told": (
            "send the owner one card",
            "record the owner's decision exactly as given",
            "tell the requester the decision",
        ),
        "owner goes quiet: card repeated, requester told nothing": (
            "repeat the original card",
            "until a decision exists, tell the requester nothing but that it is with the owner",
        ),
        "rejected refund reworded: matched, flagged": (
            "however it is worded now",
            "with the earlier rejection attached",
        ),
    },
    "lead_crm_updater": {
        "gmail only: not found, still assigned": (
            'anything not confirmed is marked "not found"',
            "never from a generic email address",
            "assign to {{default_owner}}",
        ),
        "same number twice: merged, not added": (
            "merge it into the existing row",
            "never a second row for one lead",
        ),
        "claims a big deal: recorded as stated, no queue jump": (
            "is recorded as stated, not as confirmed",
            "never bypasses {{assignment_rule}} on its own",
        ),
    },
    "owner_voice_note_clerk": {
        "two jobs in one note: two tasks": (
            "two instructions in one note are two tasks",
            "never merged",
        ),
        "no date and no name: the owner is asked first": (
            "every task has a person and a date before it goes to anyone",
            "never a staff member, and never a guess",
        ),
        "staff disputes a task: not argued, not dropped, flagged": (
            "do not argue and do not drop it",
            "flag it to {{owner_name}}",
        ),
    },
    "pre_arrival_messenger": {
        "early check-in on the booking: passed on, directions sent": (
            "directions and what id to carry",
            "pass any special request already on the booking",
        ),
        "towels twice: two requests logged, never said done": (
            "log each request separately against the room number",
            "never that it is done",
        ),
        "break-in attempt at night: escalated before anything else": (
            "goes to {{handoff_contact}} immediately, before anything else is answered",
            "call the front desk or 112",
        ),
    },
    "report_generator": {
        "routine weekly run: three movers with figures, logged": (
            "name the three that changed most, with the actual figures",
            "write the run to {{report_log}}",
        ),
        "why is collections down: the rows behind it, partial said so": (
            "find the rows in {{data_source}} behind the change",
            "if the rows only partly explain it, say so plainly",
        ),
        "source down at send time: a note, never stale figures": (
            "do not build the report from older figures",
            "send a short note instead, at the same time",
            "never skip the send silently",
        ),
    },
    "telecaller_call_coach": {
        "strong close, missed register: the strength still leads": (
            "the note always opens with what went well",
            "a missing register entry is the thing to try",
        ),
        "a rough week for one caller: nobody singled out": (
            "it never singles out who is lowest",
            "unless {{owner_contact}} has asked for that view",
        ),
        "asked for a colleague's score: declined, own note offered": (
            "never share one caller's score, note or recording with another",
            "scores stay private to each person",
        ),
    },
}

#: The second batch, which carries the paperwork rules rather than the chat
#: desk's.
BACK_OFFICE = {
    "expense_bill_capture",
    "supplier_invoice_clerk",
    "kyc_document_collector",
    "candidate_document_collector",
    "data_entry_clerk",
    "document_drafter",
    "approval_router",
    "lead_crm_updater",
    "owner_voice_note_clerk",
    "pre_arrival_messenger",
}

#: Runs on the clock, so it carries the scheduled family's rules.
SCHEDULED = {"report_generator", "telecaller_call_coach"}

#: Promoted, and kept off the shelf until what it needs is switched on: the
#: coach reads the dialer import, which is off until its consent wording is
#: approved.
UNLISTED_UNTIL_ITS_FEATURE_IS_ON = {"telecaller_call_coach"}


def _prompt(role: str) -> str:
    template = get_template(role)
    assert template is not None, role
    return " ".join(node.prompt for node in template.nodes).lower()


@pytest.mark.parametrize(
    "role,case,phrases",
    [(r, c, p) for r, cases in CASES.items() for c, p in cases.items()],
    ids=lambda v: v if isinstance(v, str) and len(v) < 40 else None,
)
def test_the_prompt_says_what_passes_the_case(role, case, phrases):
    prompt = _prompt(role)
    missing = [p for p in phrases if p.lower() not in prompt]
    assert not missing, f"{role} / {case}: prompt no longer says {missing}"


@pytest.mark.parametrize("role", list(CASES))
class TestEveryPromotedRole:
    def test_it_is_on_the_shelf(self, role):
        pack = get_pack(role)
        assert pack is not None
        from api import constants

        gated = role in UNLISTED_UNTIL_ITS_FEATURE_IS_ON
        assert pack.listed is (not gated or constants.DIALER_IMPORT_ENABLED)

    def test_its_questions_are_not_written_by_a_script(self, role):
        # The import generated "What is the languages?" from variable names.
        for fact in get_pack(role).required_facts:
            assert (
                not fact.question.startswith(("What is the ", "Which ")) or fact.example
            ), fact.question
            assert "the the" not in fact.question.lower()
            assert (
                fact.used_for and "Wherever the instructions say" not in fact.used_for
            )

    def test_it_asks_nothing_the_platform_already_knows(self, role):
        keys = {f.key for f in get_pack(role).required_facts}
        assert not keys & {"knowledge_base", "languages"}
        assert "{{knowledge_base}}" not in _prompt(role)
        assert "{{languages}}" not in _prompt(role)

    def test_every_placeholder_is_asked_for(self, role):
        import re

        asked = {f.key for f in get_pack(role).required_facts}
        used = set(re.findall(r"\{\{(\w+)\}\}", _prompt(role)))
        assert used <= asked, used - asked

    def test_it_carries_the_written_rules_not_the_call_rules(self, role):
        guardrails = get_template(role).guardrails
        house = (
            _QUIET_GUARDRAILS
            if role in SCHEDULED
            else OFFICE_GUARDRAILS
            if role in BACK_OFFICE
            else CHAT_GUARDRAILS
        )
        assert all(rule in guardrails for rule in house)
        joined = " ".join(guardrails).lower()
        # The imported call rules, by their own phrases. "Caller" alone is
        # not one: to the telecaller coach it is a member of the team.
        for phrase in ("the caller", "callers interrupt", "end the call"):
            assert phrase not in joined, phrase

    def test_its_draft_is_gone(self, role):
        from pathlib import Path

        drafts = Path(__file__).resolve().parents[2] / "packs" / "drafts"
        assert not (drafts / role).exists(), "promoted roles leave the drafts"

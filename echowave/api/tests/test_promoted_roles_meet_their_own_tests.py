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
}


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
        assert pack is not None and pack.listed

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

    def test_it_carries_the_chat_rules_not_the_call_rules(self, role):
        guardrails = get_template(role).guardrails
        assert all(rule in guardrails for rule in CHAT_GUARDRAILS)
        joined = " ".join(guardrails).lower()
        assert "caller" not in joined and "end the call" not in joined

    def test_its_draft_is_gone(self, role):
        from pathlib import Path

        drafts = Path(__file__).resolve().parents[2] / "packs" / "drafts"
        assert not (drafts / role).exists(), "promoted roles leave the drafts"

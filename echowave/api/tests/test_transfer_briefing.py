"""What the human hears when a call is escalated to them.

The tests that matter here are the failure ones. A briefing is built on the
critical path — caller on hold, handset ringing — so every way it can go wrong
has to degrade to a generic line rather than to an exception or to a caller
handed over in silence.
"""

import pytest

from api.services.telephony.escalation import (
    DEFAULT_BRIEFING,
    MAX_BRIEFING_CHARS,
    briefing_from_config,
    build_briefing,
)


class TestBuilding:
    def test_a_template_is_rendered_against_the_call(self):
        text = build_briefing(
            template="{{gathered_context.caller_name}} is calling about {{gathered_context.issue}}.",
            gathered_context={"caller_name": "Priya", "issue": "a refund"},
        )
        assert text == "Priya is calling about a refund."

    def test_initial_context_is_reachable_both_ways(self):
        # Bare and prefixed, matching how a webhook payload addresses them.
        assert "Priya" in build_briefing(
            template="{{first_name}} / {{initial_context.first_name}}",
            initial_context={"first_name": "Priya"},
        )

    def test_no_template_gives_the_default(self):
        assert build_briefing(template=None) == DEFAULT_BRIEFING
        assert build_briefing(template="   ") == DEFAULT_BRIEFING

    def test_a_template_that_renders_to_nothing_gives_the_default(self):
        # Better a generic line than a human answering to silence and hanging
        # up on a caller who is about to be bridged in.
        assert (
            build_briefing(template="{{gathered_context.missing}}") == DEFAULT_BRIEFING
        )

    def test_whitespace_is_collapsed_so_it_reads_as_one_line(self):
        text = build_briefing(template="Caller\n\n  waiting.   Refund   issue.")
        assert text == "Caller waiting. Refund issue."


class TestLength:
    def test_it_is_capped(self):
        text = build_briefing(template="word " * 200)
        assert len(text) <= MAX_BRIEFING_CHARS

    def test_it_cuts_at_a_sentence_when_there_is_one(self):
        template = ("Priya is calling about a refund on order 4471. " * 3) + "x" * 300
        text = build_briefing(template=template)
        assert text.endswith(".")
        assert len(text) <= MAX_BRIEFING_CHARS

    def test_it_never_cuts_mid_word_when_it_has_to_cut_hard(self):
        text = build_briefing(template="supercalifragilistic " * 40)
        assert not text.endswith("supercalifragilisti")
        assert len(text) <= MAX_BRIEFING_CHARS


class TestFromConfig:
    @pytest.mark.parametrize("key", ["briefing", "briefing_template"])
    def test_either_key_is_read(self, key):
        text = briefing_from_config(
            {key: "Escalation from the booking agent."},
            gathered_context={},
        )
        assert text == "Escalation from the booking agent."

    def test_a_non_string_template_is_ignored_rather_than_crashing(self):
        # Config comes from a tool definition somebody edited, so it can be
        # anything. Nothing here is worth failing a live transfer over.
        assert briefing_from_config({"briefing": {"oops": True}}) == DEFAULT_BRIEFING
        assert briefing_from_config({"briefing": 42}) == DEFAULT_BRIEFING

    def test_an_absent_config_gives_the_default(self):
        assert briefing_from_config({}) == DEFAULT_BRIEFING

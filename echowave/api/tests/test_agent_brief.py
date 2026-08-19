"""The create wizard's brief, and what survives generation.

The point of these is the split: prose goes to the model, and the parts that
have to be exact are written in afterwards. A test that only checked the
composed description would pass while the guardrails silently never applied.
"""

from api.services.workflow.agent_brief import (
    DEFAULT_GUARDRAILS,
    AgentBrief,
    apply_brief,
    compose_activity_description,
    workflow_name,
)


def _definition() -> dict:
    """The four-node shape generation returns."""
    return {
        "nodes": [
            {"id": "s", "type": "startCall", "data": {"name": "Start", "prompt": "Hi"}},
            {
                "id": "a",
                "type": "agentNode",
                "data": {"name": "Main", "prompt": "Talk"},
            },
            {"id": "e", "type": "endCall", "data": {"name": "End", "prompt": "Bye"}},
        ],
        "edges": [],
    }


class TestGuardrails:
    def test_ours_apply_when_the_operator_wrote_none(self):
        brief = AgentBrief(call_type="INBOUND")
        assert brief.effective_guardrails() == list(DEFAULT_GUARDRAILS)

    def test_the_operators_replace_ours_rather_than_merging(self):
        # A merge would silently re-add a rule they deleted, which makes the
        # control untrustworthy — the whole reason to have it.
        brief = AgentBrief(call_type="INBOUND", guardrails=["Only this one."])
        assert brief.effective_guardrails() == ["Only this one."]

    def test_blank_entries_do_not_count_as_written(self):
        brief = AgentBrief(call_type="INBOUND", guardrails=["  ", ""])
        assert brief.effective_guardrails() == list(DEFAULT_GUARDRAILS)

    def test_they_reach_the_global_node_not_just_the_prompt(self):
        brief = AgentBrief(call_type="INBOUND", guardrails=["Never quote a price."])
        definition = apply_brief(_definition(), brief)

        globals_ = [n for n in definition["nodes"] if n["type"] == "globalNode"]
        assert len(globals_) == 1
        assert "Never quote a price." in globals_[0]["data"]["prompt"]

    def test_an_existing_global_node_is_used_rather_than_a_second_added(self):
        definition = _definition()
        definition["nodes"].insert(
            0, {"id": "g", "type": "globalNode", "data": {"prompt": "old"}}
        )
        applied = apply_brief(definition, AgentBrief(call_type="INBOUND"))

        globals_ = [n for n in applied["nodes"] if n["type"] == "globalNode"]
        assert len(globals_) == 1
        assert "old" not in globals_[0]["data"]["prompt"]


class TestExactText:
    def test_the_welcome_message_is_written_in_verbatim(self):
        brief = AgentBrief(
            call_type="OUTBOUND", welcome_message="नमस्ते, मैं मीरा बोल रही हूँ."
        )
        definition = apply_brief(_definition(), brief)

        start = next(n for n in definition["nodes"] if n["type"] == "startCall")
        assert start["data"]["greeting"] == "नमस्ते, मैं मीरा बोल रही हूँ."
        assert start["data"]["greeting_type"] == "text"

    def test_no_welcome_message_leaves_the_generated_opening_alone(self):
        definition = apply_brief(_definition(), AgentBrief(call_type="INBOUND"))
        start = next(n for n in definition["nodes"] if n["type"] == "startCall")
        assert "greeting" not in start["data"]

    def test_the_closing_line_is_appended_to_the_end_node(self):
        brief = AgentBrief(call_type="INBOUND", closing_line="Thank you for your time.")
        definition = apply_brief(_definition(), brief)

        end = next(n for n in definition["nodes"] if n["type"] == "endCall")
        assert "Thank you for your time." in end["data"]["prompt"]
        # The generated closing behaviour is kept, not replaced.
        assert "Bye" in end["data"]["prompt"]

    def test_recording_disclosure_is_on_by_default(self):
        # DPDP obligation on a recorded call, and every call here is recorded.
        definition = apply_brief(_definition(), AgentBrief(call_type="INBOUND"))
        start = next(n for n in definition["nodes"] if n["type"] == "startCall")
        assert start["data"]["recording_disclosure_enabled"] is True

    def test_an_explicit_disclosure_choice_is_not_overridden(self):
        definition = _definition()
        start = next(n for n in definition["nodes"] if n["type"] == "startCall")
        start["data"]["recording_disclosure_enabled"] = False

        apply_brief(definition, AgentBrief(call_type="INBOUND"))
        assert start["data"]["recording_disclosure_enabled"] is False


class TestComposedDescription:
    def test_it_carries_the_identity_the_form_collected(self):
        brief = AgentBrief(
            call_type="OUTBOUND",
            agent_name="Meera",
            agent_designation="admissions counsellor",
            company_name="Sunrise Academy",
            tone="Warm & professional",
            languages=["Hindi", "English"],
            objective="Book a campus visit.",
        )
        text = compose_activity_description(brief)

        assert "Meera" in text and "admissions counsellor" in text
        assert "Sunrise Academy" in text
        assert "Hindi, English" in text
        assert "Book a campus visit." in text
        assert "outbound" in text.lower()

    def test_the_guardrails_are_stated_to_the_model_as_well_as_enforced(self):
        # Telling it makes it write prompts that do not fight them; enforcing
        # separately makes it not matter whether it listened.
        text = compose_activity_description(AgentBrief(call_type="INBOUND"))
        assert DEFAULT_GUARDRAILS[0] in text

    def test_an_empty_brief_composes_to_nothing_rather_than_headings(self):
        # The caller falls back to whatever description it already had, so an
        # empty composition has to be falsy rather than a skeleton of headers.
        assert compose_activity_description(AgentBrief(call_type="INBOUND")) != ""
        bare = AgentBrief(call_type="INBOUND", guardrails=["x"])
        text = compose_activity_description(bare)
        assert "## Who you are" not in text


class TestName:
    def test_it_prefers_the_use_case(self):
        brief = AgentBrief(
            call_type="INBOUND", use_case="Admissions", agent_name="Meera"
        )
        assert workflow_name(brief) == "Admissions"

    def test_it_falls_back_through_to_something_sayable(self):
        assert (
            workflow_name(AgentBrief(call_type="INBOUND", agent_name="Meera"))
            == "Meera"
        )
        assert workflow_name(AgentBrief(call_type="INBOUND")) == "Voice Agent"

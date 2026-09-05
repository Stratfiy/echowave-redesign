"""A review node with no prompt of its own still reviews the call.

The default QA prompt lives in the node *spec*, where it pre-fills the textarea
in the canvas editor. A node created any other way — the review switch on the
Analysis tab, or any of the four creation paths that now ship one — never
passes through that textarea, so it carried ``qa_system_prompt = None``, and
both analysis paths answered that by returning ``no_system_prompt`` and doing
nothing.

The failure was invisible from the product: the switch read on, the run
completed, and the Analysis tab was empty. Empty is also what it looks like
when review is genuinely off, so nothing distinguished "not asked for" from
"asked for and silently dropped".

These tests hold the fallback at the two levels that can break independently:
the helper both paths call, and the whole-call path end to end with a node
exactly as a creation path emits it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.dto import QARFNode
from api.services.workflow.node_specs.constants import DEFAULT_QA_SYSTEM_PROMPT
from api.services.workflow.qa import analysis as qa_analysis
from api.services.workflow.qa.analysis import _system_prompt
from api.services.workflow.qa_node import qa_node


class TestTheHelperBothPathsUse:
    def test_a_node_with_no_prompt_gets_the_platform_default(self):
        qa_data = QARFNode.model_validate(qa_node()).data
        assert qa_data.qa_system_prompt is None
        assert _system_prompt(qa_data) == DEFAULT_QA_SYSTEM_PROMPT

    def test_an_operators_own_prompt_wins(self):
        qa_data = SimpleNamespace(qa_system_prompt="Score politeness only.")
        assert _system_prompt(qa_data) == "Score politeness only."

    def test_an_empty_string_is_treated_as_no_prompt(self):
        # The editor's textarea returns "" when someone clears it, and a
        # cleared box means "use the default", not "review against nothing".
        qa_data = SimpleNamespace(qa_system_prompt="")
        assert _system_prompt(qa_data) == DEFAULT_QA_SYSTEM_PROMPT

    def test_the_default_asks_for_the_fields_the_parser_reads(self):
        # The parser reads exactly these off the JSON; a default prompt that
        # did not ask for them would produce reviews with no score and no tags,
        # which is the same empty Analysis tab by a different route.
        for field in ("tags", "summary", "call_quality_score", "overall_sentiment"):
            assert field in DEFAULT_QA_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_whole_call_qa_runs_for_a_node_created_by_a_creation_path():
    """End to end: the node a new agent ships with produces a real review."""
    qa_data = QARFNode.model_validate(qa_node()).data
    workflow_run = SimpleNamespace(
        logs={
            "realtime_feedback_events": [
                {"role": "user", "content": "My payment failed"},
                {"role": "assistant", "content": "I can send you a secure link."},
            ]
        },
        usage_info={"call_duration_seconds": 40},
        initial_context=None,
    )
    llm_response = (
        '{"tags": [], "summary": "Payment link offered", '
        '"call_quality_score": 8, "overall_sentiment": "neutral"}'
    )

    inference = AsyncMock(return_value=llm_response)
    with (
        patch.object(
            qa_analysis, "build_conversation_structure", return_value=[{"x": 1}]
        ),
        patch.object(qa_analysis, "format_transcript", return_value="user: hi"),
        patch.object(qa_analysis, "compute_call_metrics", return_value={}),
        patch.object(
            qa_analysis,
            "resolve_llm_config",
            new=AsyncMock(return_value=("openai", "gpt-4o", "sk-test", {})),
        ),
        patch.object(
            qa_analysis, "create_llm_service_from_provider", return_value=object()
        ),
        patch.object(qa_analysis, "_run_llm_inference", new=inference),
        patch.object(qa_analysis, "setup_langfuse_parent_context", return_value=None),
        patch.object(qa_analysis, "add_qa_span_to_trace", return_value=None),
    ):
        result = await qa_analysis._run_whole_call_qa_analysis(
            qa_data, workflow_run, workflow_run_id=202
        )

    # The old behaviour: an error and nothing else.
    assert "error" not in result
    node_result = result["node_results"]["whole_call"]
    assert node_result["score"] == 8
    assert node_result["summary"] == "Payment link offered"

    # And the model was actually given the default to review against.
    system_content = inference.await_args.args[2]
    assert "QA analyst" in system_content

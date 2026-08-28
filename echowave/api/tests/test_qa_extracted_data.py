"""Custom fields in a QA response used to be silently discarded.

The QA LLM call already runs on a system prompt the operator controls
(``qa_system_prompt``), so an operator who added their own extraction
instructions — "also return a `lead_score` field" — got a call that ran, was
billed, and threw its own answer away: only ``tags``, ``summary``,
``call_quality_score`` and ``overall_sentiment`` were ever read off the
parsed JSON. ``_extracted_data`` is the one place that discarding happened,
and this is what changed.

Both ``run_per_node_qa_analysis`` and ``_run_whole_call_qa_analysis`` call the
same helper, so the pure-function tests below plus one integration-style test
against the whole-call path (mirroring the pattern in
``test_qa_analysis_non_dict_response.py``) cover the guarantee for both.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.qa import analysis as qa_analysis
from api.services.workflow.qa.analysis import _extracted_data


class TestExtractedDataHelper:
    def test_reserved_keys_are_excluded(self):
        parsed = {
            "tags": ["billing"],
            "summary": "Customer asked about their invoice.",
            "call_quality_score": 8,
            "overall_sentiment": "neutral",
        }
        assert _extracted_data(parsed) == {}

    def test_anything_else_is_kept(self):
        parsed = {
            "tags": [],
            "summary": "",
            "lead_score": 7,
            "appointment_time": "2026-09-02T15:00:00",
            "sentiment": "positive",  # an operator's own field, distinct
            # from the reserved `overall_sentiment` the QA node itself asks for
        }
        assert _extracted_data(parsed) == {
            "lead_score": 7,
            "appointment_time": "2026-09-02T15:00:00",
            "sentiment": "positive",
        }

    def test_empty_when_nothing_extra(self):
        assert _extracted_data({}) == {}


@pytest.mark.asyncio
async def test_whole_call_qa_preserves_custom_extraction_fields():
    """The end-to-end guarantee: a custom field the operator's prompt asked
    for reaches the stored result, not just the fixed four."""
    qa_data = SimpleNamespace(qa_system_prompt="Summarize: {transcript}")
    workflow_run = SimpleNamespace(
        logs={
            "realtime_feedback_events": [
                {"role": "user", "content": "I'd like to book a table for four"},
                {"role": "assistant", "content": "Sure, what time works?"},
            ]
        },
        usage_info={"call_duration_seconds": 12},
        initial_context=None,
    )

    llm_response = (
        '{"tags": ["booking"], "summary": "Reservation request", '
        '"call_quality_score": 9, "overall_sentiment": "positive", '
        '"party_size": 4, "requested_time": "unspecified"}'
    )

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
        patch.object(
            qa_analysis,
            "_run_llm_inference",
            new=AsyncMock(return_value=llm_response),
        ),
        patch.object(qa_analysis, "setup_langfuse_parent_context", return_value=None),
        patch.object(qa_analysis, "add_qa_span_to_trace", return_value=None),
    ):
        result = await qa_analysis._run_whole_call_qa_analysis(
            qa_data, workflow_run, workflow_run_id=101
        )

    node_result = result["node_results"]["whole_call"]
    assert node_result["extracted_data"] == {
        "party_size": 4,
        "requested_time": "unspecified",
    }
    # The fixed fields still resolve exactly as before -- this is additive,
    # not a change to the existing contract.
    assert node_result["tags"] == ["booking"]
    assert node_result["overall_sentiment"] == "positive"


@pytest.mark.asyncio
async def test_unparseable_output_still_carries_an_extracted_data_key():
    """``parse_llm_json`` never raises -- unparseable text falls all the way
    back to ``{"raw": raw_content}`` (see ``json_parser.py``), which is itself
    a dict, so this never reaches the ``except`` branch below it. The
    guarantee this asserts is narrower and more useful: the key is always
    present, so a caller can index ``node_result["extracted_data"]`` without
    a KeyError regardless of what the model returned."""
    qa_data = SimpleNamespace(qa_system_prompt="Summarize: {transcript}")
    workflow_run = SimpleNamespace(
        logs={"realtime_feedback_events": [{"role": "user", "content": "hi"}]},
        usage_info={"call_duration_seconds": 5},
        initial_context=None,
    )

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
        patch.object(
            qa_analysis,
            "_run_llm_inference",
            new=AsyncMock(return_value="not json at all"),
        ),
        patch.object(qa_analysis, "setup_langfuse_parent_context", return_value=None),
        patch.object(qa_analysis, "add_qa_span_to_trace", return_value=None),
    ):
        result = await qa_analysis._run_whole_call_qa_analysis(
            qa_data, workflow_run, workflow_run_id=102
        )

    node_result = result["node_results"]["whole_call"]
    assert "extracted_data" in node_result
    # parse_llm_json's own fallback shape for genuinely unparseable text.
    assert node_result["extracted_data"] == {"raw": "not json at all"}

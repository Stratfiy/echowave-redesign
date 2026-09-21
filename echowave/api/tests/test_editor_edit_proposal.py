"""An editor proposal is a bounded, reviewable patch, never a persisted rewrite."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.services.agent_builder import edit_proposal as service
from api.services.agent_builder.client import BuilderClientError, ModelReply, ToolCall


@pytest.fixture
def graph():
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 10, "y": 20},
                "data": {
                    "name": "Greeting",
                    "prompt": "Keep all instructions.",
                    "greeting": "Hello",
                    "greeting_type": "text",
                    "is_start": True,
                    "allow_interrupt": True,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "step",
                "type": "agentNode",
                "position": {"x": 200, "y": 20},
                "data": {
                    "name": "Booking",
                    "prompt": "Book for {{name}}.",
                    "allow_interrupt": True,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 400, "y": 20},
                "data": {
                    "name": "End",
                    "prompt": "Goodbye",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "s-b",
                "source": "start",
                "target": "step",
                "data": {"condition": "ready", "label": "Ready"},
            },
            {
                "id": "b-e",
                "source": "step",
                "target": "end",
                "data": {"condition": "done", "label": "Done"},
            },
        ],
        "viewport": {"x": 2, "y": 3, "zoom": 0.7},
        "custom_metadata": {"retain": True},
    }


def proposal(**edit):
    return service.Proposal(
        status="proposal", summary="Update booking wording.", edits=[edit]
    )


def test_patch_preserves_unsaved_graph_and_metadata(graph):
    before = copy.deepcopy(graph)
    result = service.apply_proposal(
        graph, proposal(node_id="step", field="prompt", value="Book politely.")
    )
    expected = copy.deepcopy(before)
    expected["nodes"][1]["data"]["prompt"] = "Book politely."
    assert result["graph"] == expected
    assert graph == before
    assert result["changes"] == [
        {
            "node_id": "step",
            "field": "prompt",
            "before": "Book for {{name}}.",
            "after": "Book politely.",
        }
    ]


def test_greeting_switch_from_recording_is_explicit(graph):
    graph["nodes"][0]["data"]["greeting_type"] = "audio"
    result = service.apply_proposal(
        graph, proposal(node_id="start", field="greeting", value="Welcome.")
    )
    assert result["graph"]["nodes"][0]["data"]["greeting_type"] == "text"
    assert result["changes"][1]["field"] == "greeting_type"


def test_unfinished_unrelated_nodes_do_not_block_wording(graph):
    graph["nodes"][2]["data"]["prompt"] = ""
    graph["edges"][0]["data"]["condition"] = ""
    result = service.apply_proposal(
        graph, proposal(node_id="step", field="prompt", value="Book politely.")
    )
    assert result["graph"]["nodes"][2]["data"]["prompt"] == ""
    assert result["graph"]["edges"] == graph["edges"]


def test_nontext_wording_is_rejected_before_model(graph):
    graph["nodes"][1]["data"]["prompt"] = {"unexpected": "object"}
    with pytest.raises(ValueError):
        service.EditProposalRequest(workflow_id=39, message="Edit", graph=graph)


@pytest.mark.parametrize("node_id,field", [("unknown", "prompt"), ("step", "greeting")])
def test_invalid_targets_leave_graph_unchanged(graph, node_id, field):
    before = copy.deepcopy(graph)
    with pytest.raises(ValueError):
        service.apply_proposal(
            graph, proposal(node_id=node_id, field=field, value="New")
        )
    assert graph == before


def test_forbids_structural_or_config_payloads():
    with pytest.raises(ValidationError):
        service.Proposal.model_validate(
            {"status": "proposal", "summary": "Bad", "edits": [], "nodes": []}
        )
    with pytest.raises(ValidationError):
        proposal(node_id="step", field="qa_api_key", value="secret")


def test_clarification_never_returns_modified_graph(graph):
    result = service.apply_proposal(
        graph,
        service.Proposal(status="clarification", summary="Which step should change?"),
    )
    assert result["graph"] is None
    assert result["changes"] == []
    with pytest.raises(ValueError):
        service.apply_proposal(
            graph,
            service.Proposal(
                status="clarification",
                summary="Bad",
                edits=[
                    service.WordingEdit(node_id="step", field="prompt", value="New")
                ],
            ),
        )


def test_duplicate_edits_are_rejected(graph):
    change = service.WordingEdit(node_id="step", field="prompt", value="New")
    with pytest.raises(ValueError):
        service.apply_proposal(
            graph,
            service.Proposal(status="proposal", summary="Bad", edits=[change, change]),
        )


def test_request_limits_and_ambiguous_ids(graph):
    service.EditProposalRequest(workflow_id=39, message="Edit greeting", graph=graph)
    for bad in [
        {**graph, "nodes": graph["nodes"] * 50},
        {**graph, "large": "x" * service.MAX_GRAPH_BYTES},
        {**graph, "edges": [{"id": "bad", "source": "missing", "target": "end"}]},
    ]:
        with pytest.raises(ValueError):
            service.EditProposalRequest(workflow_id=39, message="Edit", graph=bad)


def test_model_receives_only_editable_projection_and_one_proposal(monkeypatch, graph):
    graph["nodes"][1]["data"]["qa_api_key"] = "SENSITIVE_KEY"
    graph["nodes"][1]["data"]["custom_headers"] = {"Authorization": "SECRET_TOKEN"}
    complete = AsyncMock(
        return_value=ModelReply(
            text="",
            tool_calls=(
                ToolCall(
                    id="1",
                    name="propose_wording",
                    arguments={
                        "status": "clarification",
                        "summary": "Use Graph to add nodes.",
                        "edits": [],
                    },
                ),
            ),
        )
    )
    monkeypatch.setattr(service, "complete", complete)
    result = asyncio.run(
        service.propose_edit(
            model=SimpleNamespace(provider="openai", model="test", api_key="key"),
            graph=graph,
            message="Add a node",
        )
    )
    assert result["status"] == "clarification"
    sent = json.dumps(complete.call_args.kwargs["conversation"].messages)
    assert "SENSITIVE_KEY" not in sent and "SECRET_TOKEN" not in sent
    assert "Book for" in sent
    assert "$ref" not in json.dumps(complete.call_args.kwargs["tools"])


def test_unexpected_model_tool_cannot_execute(monkeypatch, graph):
    monkeypatch.setattr(
        service,
        "complete",
        AsyncMock(
            return_value=ModelReply(
                text="",
                tool_calls=(
                    ToolCall(id="1", name="revise_agent_prompt", arguments={}),
                ),
            )
        ),
    )
    with pytest.raises(BuilderClientError):
        asyncio.run(
            service.propose_edit(
                model=SimpleNamespace(provider="openai", model="test", api_key="key"),
                graph=graph,
                message="Edit",
            )
        )

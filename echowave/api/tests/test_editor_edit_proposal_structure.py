"""Structural proposals use executable node schemas and preserve the draft."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.services.agent_builder import edit_proposal as editor
from api.services.agent_builder import structural_proposal as structure
from api.services.agent_builder.client import BuilderClientError, ModelReply, ToolCall
from api.services.agent_builder.edit_proposal import Proposal, apply_proposal
from api.services.workflow.dto import RFEdgeDTO, RFNodeDTO


@pytest.fixture
def graph():
    def node(node_id, kind, x):
        return {
            "id": node_id,
            "type": kind,
            "position": {"x": x, "y": 0},
            "data": {
                "name": node_id,
                "prompt": f"Instructions for {node_id}",
                "allow_interrupt": True,
                "add_global_prompt": False,
            },
        }

    def edge(edge_id, source, target):
        return {
            "id": edge_id,
            "source": source,
            "target": target,
            "data": {"label": "Continue", "condition": "When complete"},
        }

    return {
        "nodes": [
            node("start", "startCall", 0),
            node("a", "agentNode", 240),
            node("b", "agentNode", 480),
            node("end", "endCall", 720),
        ],
        "edges": [
            edge("s-a", "start", "a"),
            edge("a-b", "a", "b"),
            edge("b-e", "b", "end"),
        ],
        "viewport": {"x": 15, "y": 22, "zoom": 0.75},
        "metadata": {"retain": True},
    }


def propose(graph, *operations):
    return apply_proposal(
        graph,
        Proposal.model_validate(
            {
                "status": "proposal",
                "summary": "Update steps",
                "operations": list(operations),
            }
        ),
    )


def insert(edge_id="a-b"):
    return {
        "op": "insert_agent_on_edge",
        "edge_id": edge_id,
        "name": "Ask date",
        "prompt": "Ask for the appointment date.",
    }


def test_insert_preserves_config_metadata_and_edge_speech(graph):
    graph["nodes"][1]["data"]["tool_uuids"] = ["existing-tool"]
    graph["nodes"][1]["data"]["secret"] = "never alter"
    graph["edges"][1]["data"]["transition_speech"] = "Before booking…"
    graph["edges"][1]["data"]["transition_speech_type"] = "text"
    graph["edges"][1]["targetHandle"] = "target-input"
    before = copy.deepcopy(graph)
    result = propose(graph, insert())
    after = result["graph"]
    assert graph == before
    assert after["nodes"][:-1] == before["nodes"]
    assert (
        after["metadata"] == before["metadata"]
        and after["viewport"] == before["viewport"]
    )
    node = after["nodes"][-1]
    assert node["type"] == "agentNode" and node["data"]["allow_interrupt"] is True
    RFNodeDTO.model_validate(node)
    RFEdgeDTO.model_validate(after["edges"][-1])
    assert after["edges"][1]["data"] == before["edges"][1]["data"]
    assert after["edges"][1]["target"] == node["id"]
    assert after["edges"][-1]["targetHandle"] == "target-input"
    assert "targetHandle" not in after["edges"][1]
    assert {change["field"] for change in result["changes"]} == {
        "node_added",
        "edge_added",
        "edge_retargeted",
    }


def test_remove_linear_node_reconnects_without_other_changes(graph):
    graph["edges"][0]["data"]["transition_speech"] = "Start now"
    result = propose(graph, {"op": "remove_agent_and_reconnect", "node_id": "a"})[
        "graph"
    ]
    assert [n["id"] for n in result["nodes"]] == ["start", "b", "end"]
    assert result["edges"][0] == {**graph["edges"][0], "target": "b"}
    assert result["edges"][1] == graph["edges"][2]


def test_retarget_preserves_edge_metadata_when_old_target_has_other_route(graph):
    graph["edges"].append(
        {
            "id": "alternate",
            "source": "start",
            "target": "b",
            "data": {"label": "Other", "condition": "Another route"},
        }
    )
    result = propose(
        graph, {"op": "retarget_edge", "edge_id": "a-b", "target_node_id": "end"}
    )["graph"]
    assert result["nodes"] == graph["nodes"]
    assert result["edges"][1] == {**graph["edges"][1], "target": "end"}


def test_atomic_rejection_after_valid_operation(graph):
    before = copy.deepcopy(graph)
    with pytest.raises(ValueError):
        propose(
            graph,
            insert(),
            {"op": "retarget_edge", "edge_id": "missing", "target_node_id": "end"},
        )
    assert graph == before


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "remove_agent_and_reconnect", "node_id": "start"},
        {"op": "retarget_edge", "edge_id": "a-b", "target_node_id": "start"},
        {"op": "retarget_edge", "edge_id": "a-b", "target_node_id": "a"},
        {"op": "retarget_edge", "edge_id": "s-a", "target_node_id": "end"},
        {"op": "retarget_edge", "edge_id": "b-e", "target_node_id": "a"},
    ],
)
def test_reject_protected_nodes_or_disconnections_and_cycles(graph, operation):
    with pytest.raises(ValueError):
        propose(graph, operation)


def test_remove_branching_or_transition_metadata_is_explicitly_refused(graph):
    graph["edges"][1]["data"]["transition_speech"] = "Important disclosure"
    with pytest.raises(ValueError, match="metadata"):
        propose(graph, {"op": "remove_agent_and_reconnect", "node_id": "a"})
    graph["edges"][1]["data"].pop("transition_speech")
    graph["edges"].append(
        {
            "id": "a-e",
            "source": "a",
            "target": "end",
            "data": {"label": "Skip", "condition": "Skip"},
        }
    )
    with pytest.raises(ValueError, match="branching"):
        propose(graph, {"op": "remove_agent_and_reconnect", "node_id": "a"})


def test_unfinished_unrelated_prompt_survives_insert(graph):
    graph["nodes"][3]["data"]["prompt"] = ""
    assert propose(graph, insert())["graph"]["nodes"][3]["data"]["prompt"] == ""


def test_generated_id_retries_collision(monkeypatch, graph):
    graph["nodes"][1]["id"] = "chat-node-collision"
    graph["edges"][0]["target"] = "chat-node-collision"
    graph["edges"][1]["source"] = "chat-node-collision"
    values = iter(["collision", "fresh", "edge"])
    monkeypatch.setattr(
        structure, "uuid4", lambda: type("UUID", (), {"hex": next(values)})()
    )
    assert propose(graph, insert())["graph"]["nodes"][-1]["id"] == "chat-node-fresh"


def test_structural_payload_cannot_set_arbitrary_config(graph):
    with pytest.raises(ValidationError):
        propose(graph, {**insert(), "data": {"qa_api_key": "injected"}})
    with pytest.raises(ValidationError):
        propose(graph, {**insert(), "type": "webhook"})


def test_special_connection_is_not_silently_rewritten(graph):
    graph["nodes"][1]["type"] = "branch"
    with pytest.raises(ValueError, match="special"):
        propose(graph, insert())


def test_clarification_cannot_hide_structural_mutations(graph):
    with pytest.raises(ValueError):
        apply_proposal(
            graph,
            Proposal.model_validate(
                {
                    "status": "clarification",
                    "summary": "Which step?",
                    "operations": [insert()],
                }
            ),
        )


@pytest.mark.parametrize(
    "metadata", [{"custom_action": "must not lose"}, {"billing": 0}]
)
def test_removal_refuses_unknown_outgoing_metadata(graph, metadata):
    graph["edges"][1].update(metadata)
    with pytest.raises(ValueError, match="metadata"):
        propose(graph, {"op": "remove_agent_and_reconnect", "node_id": "a"})


def test_complete_core_graph_remains_runtime_valid_after_insert_and_remove(
    monkeypatch, graph
):
    from api.services.workflow import workflow_graph as runtime
    from api.services.workflow.dto import ReactFlowDTO
    from api.services.workflow.node_specs import get_spec

    # Exercise the real runtime graph validator over the core types used here;
    # integration-package discovery is external to this isolated test.
    monkeypatch.setattr(
        runtime,
        "all_specs",
        lambda: [get_spec(t) for t in ("startCall", "agentNode", "endCall")],
    )
    runtime.WorkflowGraph(ReactFlowDTO.model_validate(graph))
    added = propose(graph, insert())["graph"]
    runtime.WorkflowGraph(ReactFlowDTO.model_validate(added))
    removed = propose(added, {"op": "remove_agent_and_reconnect", "node_id": "a"})[
        "graph"
    ]
    runtime.WorkflowGraph(ReactFlowDTO.model_validate(removed))


def test_structural_refusal_reaches_user_without_payload_details(monkeypatch, graph):
    reply = ModelReply(
        text="",
        tool_calls=(
            ToolCall(
                id="1",
                name="propose_wording",
                arguments={
                    "status": "proposal",
                    "summary": "Remove",
                    "operations": [
                        {"op": "remove_agent_and_reconnect", "node_id": "start"}
                    ],
                },
            ),
        ),
    )
    monkeypatch.setattr(editor, "complete", AsyncMock(return_value=reply))
    with pytest.raises(BuilderClientError, match="ordinary conversation steps"):
        asyncio.run(
            editor.propose_edit(
                model=SimpleNamespace(provider="test", model="test", api_key="test"),
                graph=graph,
                message="Remove start",
            )
        )


def test_model_tool_protocol_applies_structural_operation(monkeypatch, graph):
    reply = ModelReply(
        text="",
        tool_calls=(
            ToolCall(
                id="1",
                name="propose_wording",
                arguments={
                    "status": "proposal",
                    "summary": "Ask date",
                    "operations": [insert()],
                },
            ),
        ),
    )
    monkeypatch.setattr(editor, "complete", AsyncMock(return_value=reply))
    result = asyncio.run(
        editor.propose_edit(
            model=SimpleNamespace(provider="test", model="test", api_key="test"),
            graph=graph,
            message="Ask the date before b",
        )
    )
    assert len(result["graph"]["nodes"]) == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_uuids", ["tool"]),
        ("document_uuids", ["document"]),
        ("mcp_tool_filters", {"server": ["send"]}),
        ("extraction_enabled", True),
        ("extraction_variables", [{"name": "date"}]),
        ("extraction_prompt", "Extract date"),
    ],
)
def test_removal_cannot_silently_delete_capabilities(graph, field, value):
    graph["nodes"][1]["data"][field] = value
    before = copy.deepcopy(graph)
    with pytest.raises(ValueError, match="tools, knowledge or extraction"):
        propose(graph, {"op": "remove_agent_and_reconnect", "node_id": "a"})
    assert graph == before

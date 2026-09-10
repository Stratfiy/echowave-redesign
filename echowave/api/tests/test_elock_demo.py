"""The Elock demo agent must be one the engine accepts before anybody runs it.

The script builds the client's support flow from their document. A node the
DTO rejects or an edge into nothing would surface on the demo call, in front
of the client, so the same validation the save route runs is run here.
"""

from __future__ import annotations

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph
from scripts.demos.elock_support import definition, tools


@pytest.fixture
def graph():
    return definition({})


def test_every_node_passes_the_dto(graph):
    dto = ReactFlowDTO.model_validate(graph)
    assert len(dto.nodes) == len(graph["nodes"])


def test_the_graph_is_valid(graph):
    WorkflowGraph(ReactFlowDTO.model_validate(graph))


def test_it_covers_the_documents_states(graph):
    names = {n["data"]["name"] for n in graph["nodes"]}
    for wanted in (
        "Greet and understand",
        "Verify TT and invoice",
        "Refresh the device",
        "Request an OTP",
        "Manual OTP with consent",
        "Open the lock",
        "Lock the lock",
        "Hand to a person",
        "No invoice for this trip",
    ):
        assert wanted in names


def test_consent_comes_before_the_manual_otp(graph):
    manual = next(n for n in graph["nodes"] if n["id"] == "agent-manual-otp")
    prompt = manual["data"]["prompt"]
    assert prompt.index("Shall I send a manual OTP") < prompt.index(
        "call trigger_manual_otp"
    )


def test_tools_carry_the_ids_they_are_given(graph):
    with_ids = definition({"validate_customer": "u-1", "transfer_to_support": "u-2"})
    verify = next(n for n in with_ids["nodes"] if n["id"] == "agent-verify")
    escalate = next(n for n in with_ids["nodes"] if n["id"] == "agent-escalate")
    assert verify["data"]["tool_uuids"] == ["u-1"]
    assert escalate["data"]["tool_uuids"] == ["u-2"]
    # And without ids the node still validates, just without hands.
    assert (
        next(n for n in graph["nodes"] if n["id"] == "agent-verify")["data"][
            "tool_uuids"
        ]
        == []
    )


def test_the_tools_are_well_formed():
    from api.schemas.tool import CreateToolRequest

    for spec in tools("https://x/v", "https://x/s", "https://x/m", "+911234567890"):
        CreateToolRequest.model_validate(spec)

"""The Logicorp quote demo must be an agent the engine accepts, and quote only the DHL guide's numbers."""

from __future__ import annotations

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph
from scripts.demos.logicorp_quotes import (
    DOC_RATES,
    NON_DOC_RATES,
    RULES,
    ZONES,
    definition,
    tools,
)


@pytest.fixture
def graph():
    return definition({})


def test_every_node_passes_the_dto(graph):
    dto = ReactFlowDTO.model_validate(graph)
    assert len(dto.nodes) == len(graph["nodes"])


def test_the_graph_is_valid(graph):
    WorkflowGraph(ReactFlowDTO.model_validate(graph))


def test_it_covers_what_a_quote_desk_does(graph):
    names = {n["data"]["name"] for n in graph["nodes"]}
    for wanted in (
        "Greet and understand",
        "Take the shipment details",
        "Give the indicative quote",
        "Capture the lead",
        "Surcharges, customs and services",
        "Existing shipment",
        "Hand to a person",
    ):
        assert wanted in names


def test_the_rate_guide_is_in_the_rules():
    # A few cells checked against the 2026 DHL Time Definite export sheet.
    assert NON_DOC_RATES["1"][7] == 1885  # 1 kg to the USA (zone 8)
    assert NON_DOC_RATES["5"][6] == 3714  # 5 kg to Europe (zone 7)
    assert DOC_RATES["0.5"][0] == 901  # half-kilo document to the Gulf/SAARC (zone 1)
    assert "United Arab Emirates" in ZONES[1]
    assert "USA" in ZONES[8]
    for table in (NON_DOC_RATES, DOC_RATES):
        for rates in table.values():
            assert len(rates) == 10
    assert "Z8 1,885" in RULES
    assert "divided by 5000" in RULES
    assert "fuel surcharge and gst" in RULES.lower()


def test_lead_capture_waits_for_a_clear_yes(graph):
    lead = next(n for n in graph["nodes"] if n["id"] == "agent-lead")
    prompt = lead["data"]["prompt"]
    assert prompt.index("shall I pass this to the team?") < prompt.index(
        "call capture_lead"
    )


def test_the_rate_guide_document_is_attached_where_it_is_read():
    graph = definition({}, ["doc-1"])
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["agent-quote"]["data"]["document_uuids"] == ["doc-1"]
    assert by_id["agent-services"]["data"]["document_uuids"] == ["doc-1"]
    assert "document_uuids" not in by_id["agent-lead"]["data"]
    WorkflowGraph(ReactFlowDTO.model_validate(graph))


def test_tools_carry_the_ids_they_are_given():
    with_ids = definition({"capture_lead": "u-1", "transfer_to_sales": "u-2"})
    by_id = {n["id"]: n for n in with_ids["nodes"]}
    assert by_id["agent-lead"]["data"]["tool_uuids"] == ["u-1"]
    assert by_id["agent-escalate"]["data"]["tool_uuids"] == ["u-2"]
    names = {t["name"] for t in tools("https://x.invalid/leads", "+911234567890")}
    assert names == {"capture_lead", "transfer_to_sales"}

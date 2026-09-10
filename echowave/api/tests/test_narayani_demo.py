"""The Narayani Dental demo must be an agent the engine accepts before the clinic hears it."""

from __future__ import annotations

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph
from scripts.demos.narayani_dental import CLINIC, RULES, definition, tools


@pytest.fixture
def graph():
    return definition({})


def test_every_node_passes_the_dto(graph):
    dto = ReactFlowDTO.model_validate(graph)
    assert len(dto.nodes) == len(graph["nodes"])


def test_the_graph_is_valid(graph):
    WorkflowGraph(ReactFlowDTO.model_validate(graph))


def test_it_covers_what_a_front_desk_does(graph):
    names = {n["data"]["name"] for n in graph["nodes"]}
    for wanted in (
        "Greet and understand",
        "Take the booking details",
        "Find a slot",
        "Confirm and book",
        "Reschedule or cancel",
        "Timings, directions and fees",
        "Clinical question",
        "Hand to a person",
    ):
        assert wanted in names


def test_the_facts_it_may_state_are_the_clinics_not_the_models():
    for key in ("doctor_names", "opening_hours", "clinic_address", "consultation_fee"):
        assert CLINIC[key] in RULES
    assert "Never give clinical advice" in RULES


def test_booking_waits_for_a_clear_yes(graph):
    book = next(n for n in graph["nodes"] if n["id"] == "agent-book")
    prompt = book["data"]["prompt"]
    assert prompt.index("shall I confirm?") < prompt.index("call book_appointment")


def test_tools_carry_the_ids_they_are_given():
    with_ids = definition(
        {
            "check_slots": "u-1",
            "book_appointment": "u-2",
            "transfer_to_reception": "u-3",
        }
    )
    by_id = {n["id"]: n for n in with_ids["nodes"]}
    assert by_id["agent-slots"]["data"]["tool_uuids"] == ["u-1"]
    assert by_id["agent-book"]["data"]["tool_uuids"] == ["u-2"]
    assert by_id["agent-escalate"]["data"]["tool_uuids"] == ["u-3"]
    assert [t["name"] for t in tools("a", "b", "+911")] == [
        "check_slots",
        "book_appointment",
        "transfer_to_reception",
    ]

"""Choosing the agent that takes bookings must let that agent book.

Found building a clinic receptionist end to end (October 2026). Settings,
Voice, Calls asks "Helper that makes calls" and says under it: "Places
'call it for me' calls and answers booking calls." The owner picks their
receptionist, sets booking to "Book" and saves. The workspace's Appointments
tool was created -- and attached to nothing. The route said so in a comment:
"attaching it to the agent's steps is the agent owner's choice in the
editor". On the next test call the receptionist had no booking function at
all, took the caller's details, and promised a call back.

A business owner who has just chosen which agent takes bookings has made
that choice. Now the tool goes onto that agent's talking steps (its draft,
like every other change made for an owner; a person publishes), once, and
nowhere else.
"""

from __future__ import annotations

import pytest

from api.db import db_client
from api.services.voice import appointments
from api.tests.support.voice import all_on, clean, client_as, make_people

DEFINITION = {
    "nodes": [
        {"id": "s", "type": "startCall", "data": {"name": "Answer", "prompt": "Hi"}},
        {
            "id": "a",
            "type": "agentNode",
            "data": {"name": "Book", "prompt": "Book", "tool_uuids": ["kept-uuid"]},
        },
        {"id": "e", "type": "endCall", "data": {"name": "Close", "prompt": "Bye"}},
    ],
    "edges": [
        {"id": "1", "source": "s", "target": "a", "data": {"label": "book"}},
        {"id": "2", "source": "a", "target": "e", "data": {"label": "done"}},
    ],
}


@pytest.fixture
async def people(test_engine):
    p = await make_people("booking-agent")
    yield p
    await clean(p)


async def _definition(workflow_id: int) -> dict:
    draft = await db_client.get_draft_version(workflow_id)
    if draft is not None:
        return draft.workflow_json
    workflow = await db_client.get_workflow_by_id(workflow_id)
    return workflow.workflow_definition


def _tools_by_node(definition: dict) -> dict[str, list[str]]:
    return {
        n["id"]: list((n.get("data") or {}).get("tool_uuids") or [])
        for n in definition["nodes"]
    }


@pytest.mark.asyncio
async def test_choosing_the_agent_puts_the_booking_tool_on_its_steps(
    people, monkeypatch
):
    all_on(monkeypatch)
    receptionist = await db_client.create_workflow(
        name="Front desk",
        workflow_definition=DEFINITION,
        user_id=people.a.id,
        organization_id=people.org,
    )
    other = await db_client.create_workflow(
        name="Other agent",
        workflow_definition=DEFINITION,
        user_id=people.a.id,
        organization_id=people.org,
    )
    async with client_as(people.as_a) as c:
        saved = await c.put(
            "/api/v1/voice/appointments/policy",
            json={
                "revision": 0,
                "booking": "book",
                "call_workflow_id": receptionist.id,
            },
        )
    assert saved.status_code == 200, saved.text
    tool_uuid = await appointments.ensure_tool(
        organization_id=people.org, user_id=people.a.id
    )
    tools = _tools_by_node(await _definition(receptionist.id))
    # The steps that talk to the caller can book; what was there stays.
    assert tool_uuid in tools["s"] and tool_uuid in tools["a"]
    assert "kept-uuid" in tools["a"]
    assert tool_uuid not in tools["e"]
    # Nobody else's agent was touched.
    assert tool_uuid not in sum(
        _tools_by_node(await _definition(other.id)).values(), []
    )


@pytest.mark.asyncio
async def test_saving_again_does_not_add_it_twice(people, monkeypatch):
    all_on(monkeypatch)
    receptionist = await db_client.create_workflow(
        name="Front desk",
        workflow_definition=DEFINITION,
        user_id=people.a.id,
        organization_id=people.org,
    )
    async with client_as(people.as_a) as c:
        first = await c.put(
            "/api/v1/voice/appointments/policy",
            json={
                "revision": 0,
                "booking": "book",
                "call_workflow_id": receptionist.id,
            },
        )
        await c.put(
            "/api/v1/voice/appointments/policy",
            json={"revision": first.json()["revision"], "duration_minutes": 20},
        )
    tools = _tools_by_node(await _definition(receptionist.id))
    assert len(tools["a"]) == len(set(tools["a"])) == 2

"""A test of an agent books inside the hours of the version being tested.

Found building a clinic receptionist end to end (October 2026). The owner
sets the receptionist's hours on the agent, then tries it from the tester
before publishing, as the product asks them to. The tester runs the draft;
booking read the hours only from the *published* version. A receptionist
never published had none, so every time it offered was refused with
"Opening hours are not set" -- the owner had just set them. Published once
with old hours, the test offered the old ones.

Now the hours come from the version the call is actually running: the
draft on a test, the published one on a live call (which is what the run
records as its definition).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.voice import appointments
from api.tests.support.voice import all_on, clean, make_people

IST = ZoneInfo("Asia/Kolkata")
MONDAY = date(2031, 3, 3)
TUESDAY = date(2031, 3, 4)
NOW = datetime(2031, 3, 1, 9, 0, tzinfo=IST)


def _hours(day_of_week: int) -> dict:
    return {
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "slots": [
            {"day_of_week": day_of_week, "start_time": "10:00", "end_time": "12:00"}
        ],
    }


@pytest.fixture
async def people(test_engine, monkeypatch):
    all_on(monkeypatch)

    async def zone(_org):
        return IST

    monkeypatch.setattr(appointments, "_zone", zone)
    p = await make_people("hours-run")
    yield p
    await clean(p)


async def _agent(people, *, published: dict | None, draft: dict | None) -> int:
    workflow = await db_client.create_workflow(
        name="Front desk",
        workflow_definition={"nodes": [], "edges": []},
        user_id=people.a.id,
        organization_id=people.org,
    )
    if published is not None:
        await db_client.save_workflow_draft(
            workflow.id, workflow_configurations={"agent_schedule": published}
        )
        await db_client.publish_workflow_draft(workflow.id)
    if draft is not None:
        await db_client.save_workflow_draft(
            workflow.id, workflow_configurations={"agent_schedule": draft}
        )
    return workflow.id


async def _run(people, workflow_id: int, *, draft: bool) -> int:
    run = await db_client.create_workflow_run(
        name="TEST",
        workflow_id=workflow_id,
        mode=WorkflowRunMode.TEXTCHAT.value
        if hasattr(WorkflowRunMode, "TEXTCHAT")
        else "textchat",
        user_id=people.a.id,
        use_draft=draft,
        organization_id=people.org,
    )
    return run.id


async def _grant(people, workflow_id: int) -> None:
    policy = await appointments.get_policy(people.org)
    await appointments.save_policy(
        people.org,
        {"booking": "book", "call_workflow_id": workflow_id, "lead_minutes": 0},
        revision=policy["revision"],
        user_id=people.a.id,
    )


@pytest.mark.asyncio
async def test_a_draft_never_published_books_on_its_own_hours(people):
    workflow_id = await _agent(people, published=None, draft=_hours(0))
    await _grant(people, workflow_id)
    run_id = await _run(people, workflow_id, draft=True)
    slots = await appointments.open_slots(
        organization_id=people.org,
        day=MONDAY,
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        now=NOW,
    )
    assert slots["status"] == "ok", slots
    assert slots["slots"]


@pytest.mark.asyncio
async def test_a_test_uses_the_draft_and_a_live_call_the_published(people):
    # Published: Mondays. Being tried: Tuesdays.
    workflow_id = await _agent(people, published=_hours(0), draft=_hours(1))
    await _grant(people, workflow_id)
    test_run = await _run(people, workflow_id, draft=True)
    live_run = await _run(people, workflow_id, draft=False)

    async def status(run_id: int, day: date) -> str:
        out = await appointments.open_slots(
            organization_id=people.org,
            day=day,
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            now=NOW,
        )
        return out["status"]

    assert await status(test_run, TUESDAY) == "ok"
    assert await status(test_run, MONDAY) != "ok"
    assert await status(live_run, MONDAY) == "ok"
    assert await status(live_run, TUESDAY) != "ok"


@pytest.mark.asyncio
async def test_a_booking_from_the_test_lands_inside_the_draft_hours(people):
    # run_tool books on the real clock: a day inside the policy's horizon.
    from datetime import timedelta

    day = datetime.now(IST).date() + timedelta(days=3)
    workflow_id = await _agent(people, published=None, draft=_hours(day.weekday()))
    await _grant(people, workflow_id)
    run_id = await _run(people, workflow_id, draft=True)
    out = await appointments.run_tool(
        appointments.BOOK_TOOL,
        {
            "date": day.isoformat(),
            "time": "10:30",
            "name": "Ravi Kumar",
            "phone_number": "98765 43210",
            "reason": "Cleaning",
        },
        organization_id=people.org,
        workflow_id=workflow_id,
        workflow_run_id=run_id,
    )
    assert out["status"] == "booked", out
    upcoming = await appointments.upcoming(people.org)
    assert any(a["caller_name"] == "Ravi Kumar" for a in upcoming)

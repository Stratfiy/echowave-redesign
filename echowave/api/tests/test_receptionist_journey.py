"""A clinic receptionist, end to end, as an owner sets it up and tries it.

The pieces a small business needs from a receptionist, without a phone
number and without a model key (the language model is the one thing not
exercised here; staging checks it with real keys):

1. Hire the clinic template over HTTP, with the clinic's own answers.
2. Give it the clinic's hours-and-prices document; the agent's run reads it.
3. Switch booking on from Chat, on one card, and confirm it.
4. In the tester (the draft), the call offers real times and books one.
5. A caller who wants a person is handed over: a line for the team.
6. The owner reads the booking back over HTTP.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from api.db import db_client
from api.enums import AgentEventKind
from api.services.knowledge_base import extraction
from api.services.voice import appointments
from api.services.workflow import actions
from api.tests.support.voice import all_on, clean, client_as, make_people

IST = ZoneInfo("Asia/Kolkata")
HOURS_AND_PRICES = """# Sunrise Dental Clinic, Baner, Pune

## Opening hours
- Monday to Saturday: 10:00 am to 7:00 pm
- Sunday: closed

## Prices (in rupees)
- Consultation: ₹500
- Teeth cleaning (scaling and polishing): ₹1,200
- Root canal treatment: ₹6,500 per tooth
"""


def _read_upload(content: str, filename: str) -> str:
    """The text an upload of this file yields, as the processing task reads it."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, filename)
        with open(path, "w") as fh:
            fh.write(content)
        document = extraction.extract_document(path, filename)
    return "\n".join(b.text for b in document.blocks)


@pytest.fixture
async def owner(test_engine, monkeypatch):
    all_on(monkeypatch)

    async def zone(_org):
        return IST

    monkeypatch.setattr(appointments, "_zone", zone)
    p = await make_people("receptionist")
    yield p
    async with db_client.async_session() as session:
        for table in ("agent_events", "knowledge_base_documents"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": p.org}
            )
        await session.commit()
    await clean(p)


@pytest.mark.asyncio
async def test_hire_document_booking_test_call_handoff_read_back(owner):
    # 1. Hire the template with the clinic's answers.
    async with client_as(owner.as_a) as c:
        hired = await c.post(
            "/api/v1/agent-templates/clinic_appointment/create",
            json={
                "agent_name": "Sunrise front desk",
                "variables": {
                    "clinic_name": "Sunrise Dental Clinic",
                    "doctor_names": "Dr. Meera Kulkarni, Dr. Arjun Shah",
                    "opening_hours": "Monday to Saturday, 10am to 7pm",
                    "clinic_address": "Shop 4, Green Park Society, Baner Road, Pune",
                },
            },
        )
    assert hired.status_code == 200, hired.text
    agent = hired.json()
    assert agent["unanswered"] == []
    workflow_id = agent["id"]

    # 2. The hours-and-prices document, read the way an upload is read, and
    #    in the knowledge the agent's own runs pick up.
    read = _read_upload(HOURS_AND_PRICES, "hours-and-prices.md")
    assert "1,200" in read and "10:00 am to 7:00 pm" in read
    doc = await db_client.create_document(
        organization_id=owner.org,
        created_by=owner.a.id,
        filename="hours-and-prices.md",
        file_size_bytes=len(HOURS_AND_PRICES),
        file_hash="h",
        mime_type="text/markdown",
        scope="bot",
        workflow_id=workflow_id,
    )
    async with db_client.async_session() as session:
        await session.execute(
            text(
                "UPDATE knowledge_base_documents SET processing_status = 'completed' "
                "WHERE document_uuid = :u"
            ),
            {"u": str(doc.document_uuid)},
        )
        await session.commit()
    scoped = await db_client.scoped_document_uuids(
        owner.org, workflow_id=workflow_id, folder_id=None
    )
    assert str(doc.document_uuid) in scoped

    # 3. "Let it book" in Chat: one card, confirmed by the owner.
    card = await actions.resolve(
        organization_id=owner.org,
        workflow_id=None,
        arguments={
            "action": actions.SET_UP_BOOKING,
            "agent": "Sunrise front desk",
            "mode": "book",
            "hours": [
                {
                    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                    "open": "00:00",
                    "close": "23:55",
                }
            ],
            "duration_minutes": 30,
            "services": ["Consultation", "Cleaning"],
            "why": "Asked in the thread",
        },
    )
    card["confirmed"] = {"by": owner.a.id}
    said = await actions._execute(owner.org, card)
    assert "can now book" in said

    # 4. The tester runs the draft: real times are offered, one is booked.
    run = await db_client.create_workflow_run(
        name="TEST",
        workflow_id=workflow_id,
        mode="textchat",
        user_id=owner.a.id,
        use_draft=True,
        organization_id=owner.org,
    )
    day = (datetime.now(IST) + timedelta(days=2)).date()
    slots = await appointments.run_tool(
        appointments.SLOTS_TOOL,
        {"date": day.isoformat()},
        organization_id=owner.org,
        workflow_id=workflow_id,
        workflow_run_id=run.id,
    )
    assert slots["status"] == "ok", slots
    first = slots["slots"][0]
    first_time = first["time"] if isinstance(first, dict) else str(first)[-5:]
    booked = await appointments.run_tool(
        appointments.BOOK_TOOL,
        {
            "date": day.isoformat(),
            "time": first_time,
            "name": "Ravi Kumar",
            "phone_number": "98765 43210",
            "reason": "Tooth pain",
            "service": "Consultation",
        },
        organization_id=owner.org,
        workflow_id=workflow_id,
        workflow_run_id=run.id,
    )
    assert booked["status"] == "booked", booked

    # 5. "Can I talk to a person?" -- a line for the team.
    handed = await appointments.run_tool(
        appointments.ESCALATE_TOOL,
        {"reason": "Wants to discuss braces costs with the doctor"},
        organization_id=owner.org,
        workflow_id=workflow_id,
        workflow_run_id=run.id,
        call_context={"caller_number": "+919876543210"},
    )
    assert handed.get("say")
    lines = await db_client.agent_events(
        organization_id=owner.org, kinds=[AgentEventKind.MESSAGE.value], limit=20
    ) + await db_client.agent_events(
        organization_id=owner.org, assistant_thread=True, limit=20
    )
    assert any("needs a person" in (e.summary or "") for e in lines)

    # 6. The owner reads the booking back.
    async with client_as(owner.as_a) as c:
        upcoming = await c.get("/api/v1/voice/appointments")
    assert upcoming.status_code == 200
    rows = upcoming.json()
    assert any(
        r["caller_name"] == "Ravi Kumar" and r.get("service") == "Consultation"
        for r in rows
    ), rows
    assert datetime.fromisoformat(rows[0]["starts_at"]).astimezone(UTC) > datetime.now(
        UTC
    )

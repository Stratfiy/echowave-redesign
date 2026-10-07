"""The staff support inbox and case (launch stream `support`, screen 32).

Done when: staff see the queue with assignee, severity, age and next step;
open a case with the customer's thread, internal notes, exactly what the
customer shared and read-only diagnostics; reply (once, however often it is
retried) and note separately; assign without two people overwriting each
other; and every look and change is audited. Customers cannot reach any of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services.support import tickets
from api.services.workflow import task_ledger
from api.tests.support.help_desk import (
    cleanup,
    client_as,
    make_setting,
    person,
    staff,
    switch_on,
)


@pytest.fixture
async def desk(test_engine):
    ids = await make_setting()
    task, _ = await task_ledger.create(
        organization_id=ids.org_a, title="Send the invoice", created_by=ids.customer
    )
    ids.task = task.id
    yield ids
    await cleanup(ids)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch, "SUPPORT_HELP_ENABLED", "SUPPORT_INBOX_ENABLED")


async def _ticket(desk, **extra) -> int:
    ticket, _ = await tickets.create(
        organization_id=desk.org_a,
        user_id=desk.customer,
        category="something_failed",
        description="The invoice never went out.",
        **extra,
    )
    return ticket["id"]


async def _audits(actor: int, action: str) -> int:
    async with db_client.async_session() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM admin_action_log WHERE actor_user_id = :a AND action = :n"
            ),
            {"a": actor, "n": action},
        )


@pytest.mark.asyncio
class TestGate:
    async def test_off_is_not_there(self, desk):
        async with client_as(staff(desk.agent)) as client:
            assert (
                await client.get("/api/v1/admin/support/tickets")
            ).status_code == 404

    async def test_customers_are_refused(self, desk, on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            assert (
                await client.get("/api/v1/admin/support/tickets")
            ).status_code == 403
            assert (
                await client.get("/api/v1/admin/support/summary")
            ).status_code == 403


@pytest.mark.asyncio
class TestQueue:
    async def test_queue_shows_what_to_do_next(self, desk, on):
        ticket_id = await _ticket(desk)
        async with client_as(staff(desk.agent)) as client:
            rows = (await client.get("/api/v1/admin/support/tickets")).json()["tickets"]
            unassigned = (
                await client.get(
                    "/api/v1/admin/support/tickets", params={"assignee": "unassigned"}
                )
            ).json()["tickets"]
            mine = (
                await client.get(
                    "/api/v1/admin/support/tickets", params={"assignee": "me"}
                )
            ).json()["tickets"]
        row = next(r for r in rows if r["id"] == ticket_id)
        assert row["requester_email"].startswith("cust-")
        assert row["workspace_name"] == "Acme Clinic"
        assert row["severity"] == "normal" and row["assignee_user_id"] is None
        assert row["next_step"] == "Assign" and row["overdue"] is False
        assert any(r["id"] == ticket_id for r in unassigned)
        assert all(r["id"] != ticket_id for r in mine)

    async def test_overdue_after_the_first_response_target(self, desk, on):
        ticket_id = await _ticket(desk)
        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE support_tickets SET created_at = :t WHERE id = :i"),
                {"t": datetime.now(UTC) - timedelta(hours=25), "i": ticket_id},
            )
            await session.commit()
        rows = await tickets.queue(staff_id=desk.agent, overdue_only=True)
        assert any(r["id"] == ticket_id and r["overdue"] for r in rows)


@pytest.mark.asyncio
class TestCase:
    async def test_opening_a_case_is_audited_once_per_half_hour(self, desk, on):
        ticket_id = await _ticket(desk)
        async with client_as(staff(desk.agent)) as client:
            first = await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
            await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
        assert first.status_code == 200
        assert await _audits(desk.agent, "support_case_viewed") == 1
        case = first.json()
        assert case["requester"]["email"].startswith("cust-")
        assert case["workspace"] == {"id": desk.org_a, "name": "Acme Clinic"}
        assert case["messages"][0]["body"] == "The invoice never went out."

    async def test_reply_and_note_are_separate_and_a_retry_sends_once(self, desk, on):
        ticket_id = await _ticket(desk)
        async with client_as(staff(desk.agent)) as client:
            for _ in range(3):
                reply = await client.post(
                    f"/api/v1/admin/support/tickets/{ticket_id}/replies",
                    json={
                        "body": "Could you resend it from Today?",
                        "client_key": "r-1",
                    },
                )
                assert reply.status_code == 201
            note = await client.post(
                f"/api/v1/admin/support/tickets/{ticket_id}/notes",
                json={
                    "body": "Mail provider had an outage 10:00-10:20.",
                    "client_key": "n-1",
                },
            )
            assert note.status_code == 201
            case = (
                await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
            ).json()
        staff_lines = [m for m in case["messages"] if m["author_kind"] == "staff"]
        assert len(staff_lines) == 1
        assert [n["body"] for n in case["notes"]] == [
            "Mail provider had an outage 10:00-10:20."
        ]
        assert case["status"] == "waiting_on_customer" and case["first_response_at"]
        assert await _audits(desk.agent, "support_reply_sent") == 1
        assert await _audits(desk.agent, "support_note_added") == 1
        actions = [h["action"] for h in case["history"]]
        assert "support_reply_sent" in actions and "support_note_added" in actions
        # The customer reads the reply and not the note.
        mine = await tickets.get_mine(desk.org_a, desk.customer, ticket_id)
        assert "outage" not in str(mine)
        assert mine["messages"][-1]["body"] == "Could you resend it from Today?"

    async def test_customer_answer_moves_the_case_back_to_in_progress(self, desk, on):
        ticket_id = await _ticket(desk)
        await tickets.reply_as_staff(
            ticket_id=ticket_id,
            staff_id=desk.agent,
            body="Which invoice?",
            client_key=None,
        )
        await tickets.reply_as_customer(
            organization_id=desk.org_a,
            user_id=desk.customer,
            ticket_id=ticket_id,
            body="INV-12",
            client_key=None,
        )
        rows = await tickets.queue(staff_id=desk.agent)
        row = next(r for r in rows if r["id"] == ticket_id)
        assert row["status"] == "in_progress"

    async def test_two_staff_assigning_at_once_one_wins(self, desk, on):
        ticket_id = await _ticket(desk)
        async with client_as(staff(desk.agent)) as client:
            version = (
                await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
            ).json()["version"]
            mine = await client.patch(
                f"/api/v1/admin/support/tickets/{ticket_id}",
                json={
                    "expected_version": version,
                    "assignee_user_id": desk.agent,
                    "severity": "high",
                },
            )
        async with client_as(staff(desk.agent2)) as client:
            theirs = await client.patch(
                f"/api/v1/admin/support/tickets/{ticket_id}",
                json={"expected_version": version, "assignee_user_id": desk.agent2},
            )
        assert mine.status_code == 200
        assert mine.json()["assignee_user_id"] == desk.agent
        assert mine.json()["severity"] == "high"
        assert theirs.status_code == 409
        assert theirs.json()["detail"]["current"]["assignee_user_id"] == desk.agent
        # Unassigning is an explicit null.
        async with client_as(staff(desk.agent)) as client:
            cleared = await client.patch(
                f"/api/v1/admin/support/tickets/{ticket_id}",
                json={
                    "expected_version": mine.json()["version"],
                    "assignee_user_id": None,
                },
            )
        assert cleared.json()["assignee_user_id"] is None

    async def test_cases_go_to_staff_only(self, desk, on):
        ticket_id = await _ticket(desk)
        with pytest.raises(tickets.TicketError):
            await tickets.update_case(
                ticket_id=ticket_id,
                staff_id=desk.agent,
                expected_version=1,
                changes={"assignee_user_id": desk.customer},
            )

    async def test_diagnostics_follow_the_share(self, desk, on, monkeypatch):
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        shared = await _ticket(desk, affected_kind="task", affected_id=desk.task)
        case = await tickets.case(ticket_id=shared, staff_id=desk.agent)
        assert case["diagnostics"]["task"]["state"] == "queued"
        assert case["diagnostics"]["task"]["history"][0]["to"] == "queued"
        kinds = {a["kind"] for a in case["diagnostics"]["allowances"]}
        assert {"model_turns", "voice_minutes"} <= kinds
        withheld = await _ticket(
            desk, affected_kind="task", affected_id=desk.task, share=[]
        )
        case = await tickets.case(ticket_id=withheld, staff_id=desk.agent)
        assert case["diagnostics"]["task"] is None

    async def test_resolve_from_the_console_stamps_resolution(self, desk, on):
        ticket_id = await _ticket(desk)
        case = await tickets.case(ticket_id=ticket_id, staff_id=desk.agent)
        updated = await tickets.update_case(
            ticket_id=ticket_id,
            staff_id=desk.agent,
            expected_version=case["version"],
            changes={"status": "resolved", "linked_incident": " INC-7 "},
        )
        assert updated["status"] == "resolved" and updated["resolved_at"]
        assert updated["linked_incident"] == "INC-7"


@pytest.mark.asyncio
async def test_summary_measures_each_thing_separately(desk, on):
    answered = await _ticket(desk)
    await tickets.reply_as_staff(
        ticket_id=answered,
        staff_id=desk.agent,
        body="On it.",
        client_key=None,
        then_status="resolved",
    )
    await _ticket(desk)
    async with client_as(staff(desk.agent)) as client:
        summary = (await client.get("/api/v1/admin/support/summary")).json()
    assert summary["first_response_measured"] >= 1
    assert summary["resolved"] >= 1
    assert summary["first_response_minutes_median"] is not None
    assert (
        summary["satisfaction"] is None
        and summary["satisfaction_note"] == "Not collected yet."
    )

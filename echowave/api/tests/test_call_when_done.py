"""Call me when it's done (services/call_when_done).

Done when: opting in (said, tapped or by tool) writes one pending callback
and answers on the thread; a finish recorded on the timeline -- the one
place every job records it -- queues exactly one call, however many rows
the finish writes and however many ticks run; outside calling hours the
call waits for 09:00 local and the thread says so, and a dial is never
attempted outside them; a number on the do-not-call list is never rung;
two tasks finishing together are one call; a workspace with no line tells
the person in the app instead, and says so; another workspace's task never
rings this person; and with the flag off nothing at all happens.

No call is ever placed: ``calls._dial`` (the one function that reaches
``dial_workflow``) is replaced, and the line is a stand-in row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.enums import AgentEventKind
from api.services import call_when_done as cwd
from api.services import member_preferences
from api.services.call_when_done import (
    CallWhenDoneError,
    NotHere,
    agent,
    calls,
    number,
    optin,
)
from api.services.identity import notifications
from api.services.workflow import agent_timeline, tasks_board
from api.tests import care_support as cs

IST = ZoneInfo("Asia/Kolkata")
PHONE = "+919876543210"


def _ist(hour: int, minute: int = 0, *, days: int = 0) -> datetime:
    local = datetime(2026, 10, 12, hour, minute, tzinfo=IST) + timedelta(days=days)
    return local.astimezone(UTC)


@pytest.fixture
async def home(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "CALL_WHEN_DONE_ENABLED", True)
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
    monkeypatch.setattr(constants, "CALL_WHEN_DONE_GATHER_SECONDS", 60)
    asha = await cs.person("cwd-asha")
    colleague = await cs.person("cwd-colleague")
    bob = await cs.person("cwd-bob")
    org = await cs.workspace(asha.id)
    other_org = await cs.workspace(bob.id)
    for who, where in ((asha, org), (colleague, org), (bob, other_org)):
        await db_client.add_user_to_organization(who.id, where)
    line = AsyncMock(return_value=SimpleNamespace(id=7))
    monkeypatch.setattr(db_client, "get_default_telephony_configuration", line)
    dial = AsyncMock(return_value=4242)
    monkeypatch.setattr(calls, "_dial", dial)
    told = AsyncMock(return_value={"push": "sent"})
    monkeypatch.setattr(notifications, "notify", told)
    clock = {"now": _ist(11, 0)}
    monkeypatch.setattr(calls, "_now", lambda: clock["now"])
    yield SimpleNamespace(
        asha=asha,
        colleague=colleague,
        bob=bob,
        org=org,
        other_org=other_org,
        line=line,
        dial=dial,
        told=told,
        clock=clock,
    )
    async with db_client.async_session() as session:
        for o in (org, other_org):
            for table in (
                "done_callbacks",
                "done_calls",
                "done_call_numbers",
                "do_not_call_entries",
                "agent_tasks",
                "agent_events",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": o}
                )
        await session.commit()


async def _confirm_number(h, phone: str = PHONE, user=None) -> None:
    user = user or h.asha
    event_id = await number.propose(h.org, user.id, phone, thread_id="t1")
    payload = await cs.press(h.org, event_id, user.id)
    assert payload["state"] == "done", payload


async def _task(org: int, user_id: int, title: str = "Deploy the site"):
    return await db_client.create_task(
        organization_id=org,
        title=title,
        brief="",
        status=tasks_board.IN_PROGRESS,
        created_by=user_id,
        depth=0,
    )


async def _finish(org: int, task, result: str = "Deployed. Three pages changed."):
    """A board task finishing, the way the job that runs it finishes it."""
    with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
        await tasks_board._finish(
            task.id,
            organization_id=org,
            status=tasks_board.DONE,
            result=result,
            run_id=None,
            from_id=None,
            assignee_name="Decibyl",
            title=task.title,
        )


async def _rows(table: str, org: int):
    async with db_client.async_session() as session:
        return (
            (
                await session.execute(
                    text(
                        f"SELECT * FROM {table} WHERE organization_id = :o ORDER BY id"
                    ),
                    {"o": org},
                )
            )
            .mappings()
            .all()
        )


async def _notices(org: int) -> list[str]:
    """Decibyl's call lines on the thread, oldest first. Read directly: they
    are private to the person (``private_to``), so a viewer-less timeline
    read rightly leaves them out."""
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload FROM agent_events WHERE organization_id = :o "
                    "AND kind = :k ORDER BY id"
                ),
                {"o": org, "k": AgentEventKind.MESSAGE.value},
            )
        ).all()
    return [
        (p or {}).get("body", "") for (p,) in rows if (p or {}).get("call_when_done")
    ]


@pytest.mark.asyncio
class TestOptIn:
    async def test_opt_in_creates_a_pending_callback_and_answers_on_the_thread(
        self, home
    ):
        made = await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        rows = await _rows("done_callbacks", home.org)
        assert len(rows) == 1
        assert rows[0]["state"] == cwd.PENDING
        assert rows[0]["subject"] == "thread:t1"
        assert rows[0]["user_id"] == home.asha.id
        # No number yet: asked for in the thread, not on another screen.
        assert "Which number should I ring?" in made["line"]
        assert (await _notices(home.org))[-1] == made["line"]
        # Asking twice is one ask.
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        assert len(await _rows("done_callbacks", home.org)) == 1

    async def test_it_binds_to_the_work_in_flight(self, home):
        task = await _task(home.org, home.asha.id)
        await _confirm_number(home)
        made = await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        assert made["subject"] == f"task:{task.id}"
        assert "+91 98••••3210" in made["line"] and "9:00 and 21:00" in made["line"]

    async def test_saying_it_is_answered_without_the_model(self, home):
        from api.services.workflow import decibyl

        await _task(home.org, home.asha.id)
        with patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as queued:
            await decibyl.ask(
                organization_id=home.org,
                user_id=home.asha.id,
                text="Call me when done",
                attachments=[],
                line="Call me when done",
                preset=None,
                thread_id="t1",
            )
        queued.assert_not_called()
        rows = await _rows("done_callbacks", home.org)
        assert [r["state"] for r in rows] == [cwd.PENDING]
        assert optin.is_the_ask("Call me when it's done")
        assert optin.is_the_ask("please ring me once that's finished!")
        assert not optin.is_the_ask("research Zoho and call me when done")

    async def test_the_chip_is_offered_while_work_runs_and_not_after_asking(self, home):
        assert await optin.chip(home.org, home.asha.id, "t1") is None
        await _task(home.org, home.asha.id)
        assert await optin.chip(home.org, home.asha.id, "t1") == {
            "kind": "call_when_done",
            "text": "Call me when done",
        }
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        assert await optin.chip(home.org, home.asha.id, "t1") is None

    async def test_the_number_card_is_the_persons_alone(self, home):
        event_id = await number.propose(home.org, home.asha.id, PHONE, thread_id="t1")
        card = (
            await db_client.get_agent_event(event_id, organization_id=home.org)
        ).payload
        assert card["label"] == "Ring me on +91 98••••3210 when my tasks finish"
        assert "9876543210" not in card["label"] + card["effect"]
        assert card["private_to"] == home.asha.id
        from api.services.workflow import actions

        with pytest.raises(actions.ActionError):
            await cs.press(home.org, event_id, home.colleague.id)
        assert await number.confirmed(home.org, home.asha.id) is None
        await cs.press(home.org, event_id, home.asha.id)
        assert await number.confirmed(home.org, home.asha.id) == PHONE

    async def test_the_routes(self, home):
        await _task(home.org, home.asha.id)
        async with cs.client(home.asha.id, home.org) as c:
            r = await c.post(
                "/api/v1/call-when-done",
                json={"thread_id": "t9", "phone": "98765 43210"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["card_event_id"]
            status = (
                await c.get("/api/v1/call-when-done/status", params={"thread_id": "t9"})
            ).json()
            assert len(status["pending"]) == 1 and status["offer"] is False
            gone = await c.delete(
                f"/api/v1/call-when-done/{status['pending'][0]['id']}"
            )
            assert gone.status_code == 204
            bad = await c.post("/api/v1/call-when-done", json={"phone": "12"})
            assert bad.status_code == 422


@pytest.mark.asyncio
class TestTheCall:
    async def test_a_finish_queues_exactly_one_call(self, home):
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        # The finish writes a deliverable and an activity line: two rows,
        # one finish, one call.
        await _finish(home.org, task)
        call_rows = await _rows("done_calls", home.org)
        assert len(call_rows) == 1 and call_rows[0]["state"] == cwd.QUEUED
        assert call_rows[0]["due_at"] == home.clock["now"] + timedelta(seconds=60)
        cb = (await _rows("done_callbacks", home.org))[0]
        assert cb["state"] == cwd.FINISHED and cb["call_id"] == call_rows[0]["id"]
        assert cb["title"] == "Deploy the site"
        assert cb["summary"] == "Deployed. Three pages changed."
        assert (
            "Done: Deploy the site. I'll call you in a minute"
            in (await _notices(home.org))[-1]
        )

        assert await calls.tick(home.clock["now"]) == 0  # not due yet
        due = home.clock["now"] + timedelta(seconds=61)
        assert await calls.tick(due) == 1
        assert await calls.tick(due) == 0  # a second tick places nothing
        home.dial.assert_awaited_once()
        placed = (await _rows("done_calls", home.org))[0]
        assert placed["state"] == cwd.CALLING and placed["workflow_run_id"] == 4242

    async def test_a_routine_run_and_decibyls_background_task_are_heard_too(self, home):
        """Not wired per job type: any finish recorded on the timeline."""
        from api.services.workflow import decibyl_tasks

        await _confirm_number(home)
        workflow = await db_client.create_workflow(
            name="Morning report",
            workflow_definition={"nodes": [], "edges": []},
            user_id=home.asha.id,
            organization_id=home.org,
        )
        await optin.opt_in(
            home.org, home.asha.id, thread_id="t1", workflow_id=workflow.id
        )
        # A routine run hands its report over as a deliverable on the bot.
        await agent_timeline.record(
            organization_id=home.org,
            kind=AgentEventKind.DELIVERABLE.value,
            summary="Morning report: 12 orders, 2 refunds.",
            workflow_id=workflow.id,
            workflow_run_id=None,
            payload={"result": "12 orders, 2 refunds. Refund for #88 needs approval."},
        )
        # Decibyl's own background task, asked on thread t2: its answer and
        # its finish are two rows and one finish.
        task = await db_client.create_task(
            organization_id=home.org,
            title="Compare three CRMs",
            brief="",
            status=tasks_board.IN_PROGRESS,
            created_by=home.asha.id,
            depth=0,
            continuation={"thread_id": "t2", "author_id": home.asha.id, "messages": []},
        )
        await optin.opt_in(home.org, home.asha.id, thread_id="t2")
        with agent_timeline.in_thread("t2"):
            await agent_timeline.record(
                organization_id=home.org,
                kind=AgentEventKind.MESSAGE.value,
                summary="Zoho is cheapest; HubSpot is easiest.",
                payload={
                    "body": "Zoho is cheapest; HubSpot is easiest.",
                    "from": "Decibyl",
                    "task_id": task.id,
                },
                in_channel=False,
            )
        await decibyl_tasks._finish(
            task, status=tasks_board.DONE, result="Zoho is cheapest.", thread_id="t2"
        )
        call_rows = await _rows("done_calls", home.org)
        assert len(call_rows) == 1
        items = await calls.items_of_call(home.org, call_rows[0]["id"])
        assert [i["title"] for i in items] == [
            "Morning report: 12 orders, 2 refunds.",
            "Compare three CRMs",
        ]

    async def test_the_call_says_who_what_and_whether_it_needs_them(self, home):
        context = agent.call_context(
            items=[
                {
                    "title": "Deploy the site",
                    "summary": "Deployed.",
                    "output": "Deployed: home, pricing and contact pages changed.",
                    "needs_you": False,
                }
            ],
            language="hi-IN",
            person="Asha",
        )
        assert context["done_greeting"].startswith("नमस्ते Asha, यह Decibyl है")
        assert "Deploy the site" in context["done_greeting"]
        assert context["done_language_name"] == "Hindi"
        assert context["done_needs_you"] == "Nothing needs them right now."
        assert "pricing" in context["done_results"]
        assert "tell me more" in agent._RULES

    async def test_outside_hours_waits_for_nine_and_says_so(self, home):
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        home.clock["now"] = _ist(22, 30)
        await _finish(home.org, task)
        call = (await _rows("done_calls", home.org))[0]
        assert call["due_at"] == _ist(9, 0, days=1)
        assert (await _notices(home.org))[-1] == (
            "Done: Deploy the site. I'll call you at 9:00, outside calling hours now."
        )
        assert await calls.tick(_ist(23, 0)) == 0
        assert await calls.tick(_ist(9, 0, days=1) + timedelta(seconds=5)) == 1
        home.dial.assert_awaited_once()

    async def test_a_dial_is_never_attempted_outside_hours(self, home):
        """Even a call already due is refused by dnd.assert_may_call at the
        moment of dialling, and goes back to wait for 09:00."""
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        late = _ist(21, 5)
        assert await calls.tick(late) == 1
        home.dial.assert_not_awaited()
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.QUEUED
        assert call["due_at"] == _ist(9, 0, days=1)
        assert "I'll call you at 9:00" in (await _notices(home.org))[-1]

    async def test_the_do_not_call_list_is_honoured(self, home):
        await _confirm_number(home)
        from api.services.compliance import dnd

        # Stored the way the do-not-call screen stores it: the matching key.
        await db_client.add_dnd_entries(home.org, [dnd.normalise_number(PHONE)])
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_not_awaited()
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.FAILED and call["reason"] == "do_not_call"
        home.told.assert_awaited()
        assert home.told.await_args.kwargs["topic"] == "task_updates"
        assert "do-not-call list" in (await _notices(home.org))[-1]

    async def test_two_tasks_finishing_together_are_one_call(self, home):
        await _confirm_number(home)
        first = await _task(home.org, home.asha.id, "Deploy the site")
        second = await _task(home.org, home.asha.id, "Research Zoho pricing")
        await optin.opt_in(home.org, home.asha.id, thread_id="t1", task_id=first.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1", task_id=second.id)
        await _finish(home.org, first)
        await _finish(home.org, second, "Zoho One is the cheapest for five people.")
        call_rows = await _rows("done_calls", home.org)
        assert len(call_rows) == 1
        callbacks = await _rows("done_callbacks", home.org)
        assert {c["call_id"] for c in callbacks} == {call_rows[0]["id"]}
        items = await calls.items_of_call(home.org, call_rows[0]["id"])
        assert [i["title"] for i in items] == [
            "Deploy the site",
            "Research Zoho pricing",
        ]
        assert "same call" in (await _notices(home.org))[-1]
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_awaited_once()

    async def test_no_line_tells_them_in_the_app_and_says_why(self, home):
        home.line.return_value = None
        task = await _task(home.org, home.asha.id)
        made = await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        assert made["can_call"] is False and "as a notification" in made["line"]
        await _finish(home.org, task)
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.NOTIFIED and call["reason"] == "needs_setup"
        home.told.assert_awaited_once()
        sent = home.told.await_args.kwargs
        assert sent["title"] == "Done: Deploy the site"
        assert sent["link"] == "/overview?thread=t1"
        line = (await _notices(home.org))[-1]
        assert "no phone line" in line and "notification" in line
        assert await calls.tick(home.clock["now"] + timedelta(hours=1)) == 0
        home.dial.assert_not_awaited()

    async def test_no_confirmed_number_is_never_silent(self, home):
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        call = (await _rows("done_calls", home.org))[0]
        assert call["state"] == cwd.NOTIFIED and call["reason"] == "no_number"
        assert "not confirmed a number" in (await _notices(home.org))[-1]

    async def test_the_standing_preference_needs_no_ask(self, home):
        await _confirm_number(home)
        await member_preferences.save(
            home.asha.id, {member_preferences.CALL_WHEN_DONE: True}, revision=0
        )
        task = await _task(home.org, home.asha.id)
        await _finish(home.org, task)
        callbacks = await _rows("done_callbacks", home.org)
        assert len(callbacks) == 1 and callbacks[0]["standing"] is True
        assert len(await _rows("done_calls", home.org)) == 1

    async def test_answered_and_not_answered(self, home):
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        # "Not answered" only on evidence: the carrier's no-answer callback,
        # as status_processor writes it onto the call's run.
        no_answer = SimpleNamespace(
            is_completed=True,
            answered_at=None,
            billable_seconds=None,
            gathered_context={
                "call_tags": ["not_connected", "telephony_no-answer"],
                "mapped_call_disposition": "no-answer",
            },
        )
        with patch.object(
            db_client, "get_workflow_run", new=AsyncMock(return_value=no_answer)
        ):
            assert await calls.sweep(home.clock["now"] + timedelta(hours=1)) == 1
        call = (await _rows("done_calls", home.org))[0]
        assert call["id"] == call_id and call["state"] == cwd.NOT_ANSWERED
        assert "you did not pick up" in (await _notices(home.org))[-1]


@pytest.mark.asyncio
class TestFounderRules:
    async def test_a_call_only_reads_out_and_never_moves_an_approval_card(
        self, home, monkeypatch
    ):
        """Rule 1: read-out only. A card waiting for approval is in the same
        state, at the same version, after a call that was answered and in
        which the person said yes to everything."""
        await _confirm_number(home)
        waiting = await number.propose(
            home.org, home.asha.id, "+919812345678", thread_id="t1"
        )
        before = (
            await db_client.get_agent_event(waiting, organization_id=home.org)
        ).payload
        assert before["state"] == "proposed"
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        home.dial.assert_awaited_once()
        call_id = (await _rows("done_calls", home.org))[0]["id"]
        run = SimpleNamespace(
            initial_context={
                "done_call_id": call_id,
                "trigger_source": "call_when_done",
            },
            gathered_context={
                "extracted_variables": {"approve": "yes", "confirm": "yes, send it"}
            },
            annotations={},
            answered_at=home.clock["now"],
            billable_seconds=45,
        )
        monkeypatch.setattr(db_client, "get_workflow_run", AsyncMock(return_value=run))
        monkeypatch.setattr(
            db_client,
            "get_organization_id_by_workflow_run_id",
            AsyncMock(return_value=home.org),
        )
        await calls.record_run_outcome(4242)
        assert (await _rows("done_calls", home.org))[0]["state"] == cwd.ANSWERED
        after = (
            await db_client.get_agent_event(waiting, organization_id=home.org)
        ).payload
        assert after == before
        # Nothing on the call could have: the agent holds no tools, and it
        # is told to send approvals to the app.
        nodes = agent.definition()["nodes"]
        assert {n["type"] for n in nodes} == {
            "globalNode",
            "startCall",
            "agentNode",
            "endCall",
        }
        assert not any("tool" in key for n in nodes for key in n["data"])
        assert agent.APPROVE_IN_APP in agent._RULES
        needs = agent.call_context(
            items=[{"title": "Send the invoice", "needs_you": True}],
            language="en",
            person="",
        )["done_needs_you"]
        assert needs.endswith("I've put it in your app for you to confirm.")

    async def test_the_window_is_the_persons_own_timezone(self, home):
        """Rule 2: 09:00-21:00 where the person is, for calls they asked for
        too. 19:30 in Kolkata is 22:00 in Singapore: a person there waits."""
        await member_preferences.save(
            home.asha.id, {"timezone": "Asia/Singapore"}, revision=0
        )
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        home.clock["now"] = _ist(19, 30)  # 22:00 in Singapore
        await _finish(home.org, task)
        call = (await _rows("done_calls", home.org))[0]
        nine_sg = datetime(2026, 10, 13, 9, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        assert call["due_at"] == nine_sg.astimezone(UTC)
        assert (
            "I'll call you at 9:00, outside calling hours now."
            in (await _notices(home.org))[-1]
        )
        # Even forced due now, the dial is refused by the window.
        assert await calls.tick(nine_sg.astimezone(UTC) - timedelta(hours=1)) == 0
        async with db_client.async_session() as session:
            await session.execute(
                text("UPDATE done_calls SET due_at = :d WHERE organization_id = :o"),
                {"d": _ist(19, 31), "o": home.org},
            )
            await session.commit()
        assert await calls.tick(_ist(19, 32)) == 1
        home.dial.assert_not_awaited()

    async def test_the_sixth_call_in_a_day_is_a_notice(self, home, monkeypatch):
        """Rule 3: at most CALL_WHEN_DONE_DAILY_CAP calls per person per day;
        the sixth finish reaches them in the app and the thread says why."""
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_DAILY_CAP", 5)
        await _confirm_number(home)
        for n in range(6):
            home.clock["now"] = _ist(10 + n, 0)
            task = await _task(home.org, home.asha.id, f"Task {n + 1}")
            await optin.opt_in(home.org, home.asha.id, thread_id="t1", task_id=task.id)
            await _finish(home.org, task, f"Result {n + 1}.")
            await calls.tick(home.clock["now"] + timedelta(minutes=2))
        assert home.dial.await_count == 5
        rows = await _rows("done_calls", home.org)
        assert [r["state"] for r in rows] == [cwd.CALLING] * 5 + [cwd.NOTIFIED]
        assert rows[-1]["reason"] == "daily_cap"
        # The five that rang hold the day's slots; the sixth holds none.
        assert [r["allowance_day"] is not None for r in rows] == [True] * 5 + [False]
        home.told.assert_awaited()
        assert home.told.await_args.kwargs["title"] == "Done: Task 6"
        line = (await _notices(home.org))[-1]
        assert "the day's call limit is reached (5 calls a day)" in line
        # The next day the limit starts again.
        home.clock["now"] = _ist(10, 0, days=1)
        task = await _task(home.org, home.asha.id, "Task 7")
        await optin.opt_in(home.org, home.asha.id, thread_id="t1", task_id=task.id)
        await _finish(home.org, task)
        await calls.tick(home.clock["now"] + timedelta(minutes=2))
        assert home.dial.await_count == 6


@pytest.mark.asyncio
class TestBoundaries:
    async def test_another_workspaces_task_never_rings_this_person(self, home):
        await _confirm_number(home)
        await member_preferences.save(
            home.asha.id, {member_preferences.CALL_WHEN_DONE: True}, revision=0
        )
        await optin.opt_in(home.org, home.asha.id, thread_id=None)
        # A task in Bob's workspace, even one naming Asha as its author,
        # finishing on the main conversation there.
        theirs = await _task(home.other_org, home.asha.id, "Bob's export")
        await _finish(home.other_org, theirs)
        await agent_timeline.record(
            organization_id=home.other_org,
            kind=AgentEventKind.DELIVERABLE.value,
            summary="Report ready",
            payload={},
            in_channel=False,
        )
        assert await _rows("done_calls", home.org) == []
        assert await _rows("done_calls", home.other_org) == []
        assert [c["state"] for c in await _rows("done_callbacks", home.org)] == [
            cwd.PENDING
        ]
        # And it cannot be named from here.
        with pytest.raises(NotHere):
            await optin.opt_in(
                home.org, home.asha.id, thread_id="t1", task_id=theirs.id
            )
        async with cs.client(home.asha.id, home.org) as c:
            r = await c.post("/api/v1/call-when-done", json={"task_id": theirs.id})
            assert r.status_code == 404

    async def test_a_colleagues_task_does_not_settle_my_next_thing(self, home):
        await _confirm_number(home)
        await optin.opt_in(home.org, home.asha.id, thread_id=None)
        theirs = await _task(home.org, home.colleague.id, "Colleague's job")
        await _finish(home.org, theirs)
        assert await _rows("done_calls", home.org) == []

    async def test_flag_off_means_nothing(self, home, monkeypatch):
        await _confirm_number(home)
        task = await _task(home.org, home.asha.id)
        await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        monkeypatch.setattr(constants, "CALL_WHEN_DONE_ENABLED", False)
        before = len(await _notices(home.org))
        with pytest.raises(CallWhenDoneError):
            await optin.opt_in(home.org, home.asha.id, thread_id="t1")
        await _finish(home.org, task)
        assert await _rows("done_calls", home.org) == []
        assert await calls.tick(home.clock["now"] + timedelta(hours=1)) == 0
        assert await optin.chip(home.org, home.asha.id, "t1") is None
        assert len(await _notices(home.org)) == before
        home.dial.assert_not_awaited()
        home.told.assert_not_awaited()
        from api.services.workflow import decibyl

        assert optin.TOOL_NAME not in {
            t["name"] for t in decibyl.office_tools(home.org)
        }
        assert optin.TOOL_NAME not in decibyl.system_prompt(home.org)
        async with cs.client(home.asha.id, home.org) as c:
            r = await c.get("/api/v1/call-when-done/status")
            assert r.status_code == 404
            r = await c.post("/api/v1/call-when-done", json={})
            assert r.status_code == 404

    async def test_flag_on_offers_the_tool_and_its_rule(self, home):
        from api.services.workflow import decibyl

        assert optin.TOOL_NAME in {t["name"] for t in decibyl.office_tools(home.org)}
        assert optin.TOOL_NAME in decibyl.system_prompt(home.org)

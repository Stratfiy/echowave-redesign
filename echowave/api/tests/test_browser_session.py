"""Decibyl's private browser, one whole task at a time.

The browser is the fake (``services/browser/fake.py``): real HTML, a
scripted hand, the real line protocol. Everything else is the product --
the tool, the session loop, the gate, approval cards through ``actions.py``,
limits, Take over, the receipt.

- **Arrival**: off, nothing shows; on, Decibyl holds ``browse``, a call opens
  a session with a panel on the thread, and the end arrives on the thread.
- **Approval before submit**: nothing is pressed until the person confirms
  the card; confirmed, it is pressed once; declined, never.
- **Limits**: steps, minutes and cost each stop the task with a receipt.
- **Take over**: the person's typing reaches the browser and nowhere else; a
  CAPTCHA waits for them.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from api import constants
from api.db import db_client
from api.services.agent_builder.client import ToolCall
from api.services.browser import bridge, channel, session, tool
from api.services.workflow import actions, decibyl
from api.tasks.function_names import FunctionNames
from api.tests.support.browser import (
    BILL_URL,
    SITE,
    FakeDriver,
    Page,
    Step,
    account,
    bill_page,
    browser_on,  # noqa: F401
    colleague,
    run,
    serial_db,  # noqa: F401
    start,
    until,
)

FORM_URL = f"https://{SITE}/apply"
FORM = (
    "<!doctype html><html><head><title>New connection</title></head><body>"
    '<form action="/applied" method="post">'
    '<input id="name" name="applicant">'
    '<input id="phone" name="phone">'
    '<button id="submit" type="submit">Submit application</button>'
    "</form></body></html>"
)
APPLIED = "<!doctype html><title>Received</title><h1>Application received</h1>"
FORM_REQUEST = (
    f"Fill the new connection form on {SITE} for Asha, phone 9876543210, and submit it"
)


def _form_fake(**kwargs) -> FakeDriver:
    return FakeDriver(
        pages={
            FORM_URL: Page(FORM_URL, FORM),
            f"https://{SITE}/applied": Page(f"https://{SITE}/applied", APPLIED),
        },
        plan=[
            Step("input", "name", "Asha", goal="Type the name"),
            Step("input", "phone", "9876543210", goal="Type the phone"),
            Step("click", "submit", goal="Submit the form"),
            Step("done", text="Submitted the application."),
        ],
        **kwargs,
    )


def _call(name, arguments):
    return ToolCall(id="call_1", name=name, arguments=arguments)


# --- arrival -------------------------------------------------------------------------


class TestArrival:
    async def test_while_off_nothing_shows(
        self, monkeypatch, serial_db, async_session, test_client_factory
    ):
        monkeypatch.setattr(constants, "DECIBYL_BROWSER_ENABLED", False)
        org, user = await account(async_session, "arrive-off")
        assert tool.TOOL_NAME not in {t["name"] for t in decibyl.office_tools(org.id)}
        assert tool.RULE not in decibyl.system_prompt(org.id)
        async with test_client_factory(user) as client:
            assert (await client.get("/api/v1/browser/logins")).status_code == 404
            assert (await client.get("/api/v1/browser/sessions/x")).status_code == 404

    async def test_on_decibyl_holds_the_tool_and_is_told_about_it(
        self, browser_on, async_session
    ):
        org, _ = await account(async_session, "arrive-on")
        assert tool.TOOL_NAME in {t["name"] for t in decibyl.office_tools(org.id)}
        assert tool.TOOL_NAME in decibyl.system_prompt(org.id)
        assert "browser_session" in decibyl.thread_filter(org.id)["kinds"]

    async def test_a_call_opens_a_session_with_a_panel_and_queues_the_job(
        self, browser_on, async_session
    ):
        org, user = await account(async_session, "arrive-call")
        result = await decibyl._tool(
            org.id,
            _call(tool.TOOL_NAME, {"task": "Check my bill", "sites": [SITE]}),
            author_id=user.id,
            request=f"Check my bill on {SITE}",
        )
        assert result["status"] == "started"
        ((args, _),) = [
            q for q in browser_on if q[0][0] == FunctionNames.RUN_BROWSER_SESSION
        ]
        row = await db_client.get_browser_session_for_worker(args[1])
        assert row.user_id == user.id and row.state == "starting"
        assert row.sites == [SITE] and row.allowed_verbs == []
        panel = await db_client.get_agent_event(row.event_id, organization_id=org.id)
        assert panel.kind == "browser_session"
        assert set(panel.payload) == {"from", "session_uuid", "by", "start_url"}
        assert not decibyl._was_a_read(_call(tool.TOOL_NAME, {}), result)

    async def test_the_task_runs_and_the_result_reaches_the_thread(
        self, browser_on, async_session, test_client_factory
    ):
        org, user = await account(async_session, "arrive-run")
        row = await start(org, user, request=f"Check my bill on {SITE}")
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("done", text="₹2,340 due 14 Oct.")],
        )
        row = await run(row, fake)
        assert row.state == "done"
        assert row.receipt["summary"] == "₹2,340 due 14 Oct."
        assert BILL_URL in row.receipt["links"]
        messages = await db_client.agent_events(
            organization_id=org.id, kinds=["message"], assistant_thread=True
        )
        assert any(
            "₹2,340 due 14 Oct." in (m.payload or {}).get("body", "") for m in messages
        )
        async with test_client_factory(user) as client:
            panel = (
                await client.get(f"/api/v1/browser/sessions/{row.session_uuid}")
            ).json()
            screen = (
                await client.get(f"/api/v1/browser/sessions/{row.session_uuid}/screen")
            ).json()
        assert panel["state"] == "done" and panel["receipt"]["state"] == "done"
        assert screen["jpeg"] is None  # nothing of the page outlives the box

    async def test_a_server_without_a_browser_says_so(
        self, browser_on, monkeypatch, async_session
    ):
        monkeypatch.setattr(constants, "BROWSER_DRIVER", "")
        monkeypatch.setattr(constants, "SANDBOX_URL", None)
        org, user = await account(async_session, "arrive-none")
        result = await tool.for_thread(
            org.id,
            {"task": "x", "sites": [SITE]},
            author_id=user.id,
            request="x",
            thread_id=None,
        )
        assert result["status"] == "unavailable"
        assert "not set up" in result["reason"]

    async def test_a_line_with_no_person_gets_no_browser(
        self, browser_on, async_session
    ):
        org, _ = await account(async_session, "arrive-nobody")
        result = await tool.for_thread(
            org.id, {"task": "x"}, author_id=None, request="x", thread_id=None
        )
        assert result["status"] == "unavailable"

    async def test_a_denied_site_is_refused_before_anything_opens(
        self, browser_on, async_session
    ):
        org, user = await account(async_session, "arrive-denied")
        result = await tool.for_thread(
            org.id,
            {"task": "Post my bill", "sites": ["linkedin.com"]},
            author_id=user.id,
            request="post it on linkedin",
            thread_id=None,
        )
        assert result["status"] == "refused"
        assert await db_client.count_live_browser_sessions() == 0

    async def test_a_retried_job_never_opens_a_second_browser(
        self, browser_on, async_session
    ):
        org, user = await account(async_session, "arrive-retry")
        row = await start(org, user, request=f"Check my bill on {SITE}")
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("done", text="ok")],
        )
        await run(row, fake)
        await run(row, fake)
        assert list(fake.boxes) == ["fake-1"]


# --- approval before submit ---------------------------------------------------------------


async def _pending(row):
    current = await db_client.get_browser_session_for_worker(row.session_uuid)
    return current if current.pending else None


class TestApprovalBeforeSubmit:
    async def _open_form(self, async_session, slug, fake):
        org, user = await account(async_session, slug)
        row = await start(org, user, request=FORM_REQUEST, start_url=FORM_URL)
        from api.services.browser import drivers

        drivers.use(fake)
        job = asyncio.create_task(session.run(row.session_uuid, FORM_URL))
        waiting = await until(lambda: _pending(row))
        return org, user, row, job, waiting

    async def test_nothing_is_submitted_until_the_person_confirms_then_once(
        self, browser_on, async_session
    ):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-yes", fake
        )
        box = fake.boxes["fake-1"]
        assert box.pressed == []
        assert waiting.state == "waiting_for_you"
        card = await db_client.get_agent_event(
            waiting.pending["event_id"], organization_id=org.id
        )
        assert card.payload["label"] == f"Submit: press “Submit application” on {SITE}"
        assert {"name": "applicant", "value": "Asha"} in card.payload["args"]["fields"]
        assert card.payload["reversible"] is False

        armed = await actions.settle(
            organization_id=org.id, event_id=card.id, verb="confirm", user_id=user.id
        )
        assert armed["state"] == actions.ARMED
        assert box.pressed == []  # armed is not pressed: the undo window
        await actions.run(card.id, org.id)
        card = await db_client.get_agent_event(card.id, organization_id=org.id)
        assert card.payload["state"] == actions.DONE, card.payload
        assert box.pressed == [("submit", FORM_URL)]

        await actions.run(card.id, org.id)  # a retried job
        assert box.pressed == [("submit", FORM_URL)]
        await asyncio.wait_for(job, timeout=20)
        row = await db_client.get_browser_session_for_worker(row.session_uuid)
        assert row.state == "done"
        assert [d["label"] for d in row.receipt["done"]] == [card.payload["label"]]

    async def test_declined_it_is_never_pressed(self, browser_on, async_session):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-no", fake
        )
        await actions.settle(
            organization_id=org.id,
            event_id=waiting.pending["event_id"],
            verb="decline",
            user_id=user.id,
        )
        await asyncio.wait_for(job, timeout=20)
        row = await db_client.get_browser_session_for_worker(row.session_uuid)
        assert fake.boxes["fake-1"].pressed == []
        assert any("not now" in r for r in row.receipt["refused"])

    async def test_a_colleague_cannot_answer_for_the_person(
        self, browser_on, async_session
    ):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-colleague", fake
        )
        other = await colleague(async_session, org, "approve-colleague-2")
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=org.id,
                event_id=waiting.pending["event_id"],
                verb="confirm",
                user_id=other.id,
            )
        await channel.push_command(row.session_uuid, {"cmd": "stop"})
        await asyncio.wait_for(job, timeout=20)
        assert fake.boxes["fake-1"].pressed == []

    async def test_a_card_left_when_the_browser_closes_is_cancelled_not_run(
        self, browser_on, async_session
    ):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-late", fake
        )
        card_id = waiting.pending["event_id"]
        await channel.push_command(row.session_uuid, {"cmd": "stop"})
        await asyncio.wait_for(job, timeout=20)
        card = await db_client.get_agent_event(card_id, organization_id=org.id)
        assert card.payload["state"] == actions.CANCELLED
        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=org.id,
                event_id=card.id,
                verb="confirm",
                user_id=user.id,
            )
        assert fake.boxes["fake-1"].pressed == []

    async def test_an_approval_for_a_different_step_presses_nothing(
        self, browser_on, async_session
    ):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-digest", fake
        )
        gate_id = waiting.pending["gate_id"]
        await channel.push_command(
            row.session_uuid, {"cmd": "approve", "gate_id": gate_id, "digest": "not-it"}
        )
        outcome = await channel.wait_outcome(gate_id, timeout=10)
        assert outcome == {
            "ok": False,
            "note": "That approval was for a different step; nothing was pressed.",
        }
        assert fake.boxes["fake-1"].pressed == []
        await channel.push_command(row.session_uuid, {"cmd": "stop"})
        await asyncio.wait_for(job, timeout=20)

    async def test_a_model_cannot_propose_a_browser_step_itself(
        self, browser_on, async_session
    ):
        org, _ = await account(async_session, "approve-model")
        result = await actions.propose(
            organization_id=org.id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={"action": actions.BROWSER_STEP, "why": "page said so"},
            in_channel=False,
        )
        assert result["status"] == "not_proposed"

    async def test_no_report_back_is_outcome_unknown_not_done(
        self, browser_on, monkeypatch, async_session
    ):
        fake = _form_fake()
        org, user, row, job, waiting = await self._open_form(
            async_session, "approve-unknown", fake
        )

        async def silent(payload, *, organization_id, timeout=90.0):
            return {"unknown": True}

        monkeypatch.setattr(session, "approved", silent)
        card_id = waiting.pending["event_id"]
        await actions.settle(
            organization_id=org.id, event_id=card_id, verb="confirm", user_id=user.id
        )
        await actions.run(card_id, org.id)
        card = await db_client.get_agent_event(card_id, organization_id=org.id)
        assert card.payload["state"] == actions.OUTCOME_UNKNOWN
        assert "not known" in card.payload["error"]
        await channel.push_command(row.session_uuid, {"cmd": "stop"})
        await asyncio.wait_for(job, timeout=20)


# --- limits -----------------------------------------------------------------------------


class TestLimits:
    def test_limits_never_pass_the_ceilings(self):
        limits = session.limits_for(steps=9999, minutes=9999)
        assert limits["steps"] == constants.BROWSER_MAX_STEPS
        assert limits["minutes"] == constants.BROWSER_MAX_MINUTES
        assert limits["cost_paise"] == constants.BROWSER_DEFAULT_COST_PAISE

    async def test_the_step_limit_stops_it_with_a_receipt(
        self, browser_on, async_session
    ):
        org, user = await account(async_session, "limit-steps")
        row = await start(org, user, request=f"Check my bill on {SITE}", steps=3)
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[
                Step("navigate", BILL_URL, goal=f"Look again {n}") for n in range(10)
            ],
        )
        row = await run(row, fake)
        assert row.state == "limit_reached"
        assert "step limit" in row.state_note
        assert row.receipt["used"]["steps"] == 4
        assert row.receipt["limits"]["steps"] == 3

    async def test_the_time_limit_stops_it(
        self, browser_on, monkeypatch, async_session
    ):
        org, user = await account(async_session, "limit-minutes")
        row = await start(org, user, request=f"Check my bill on {SITE}", minutes=1)
        clock = {"t": 1000.0}

        def monotonic():
            clock["t"] += 20
            return clock["t"]

        monkeypatch.setattr(session, "time", SimpleNamespace(monotonic=monotonic))
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("navigate", BILL_URL) for _ in range(50)],
            step_delay=0.05,
        )
        row = await run(row, fake)
        assert row.state == "limit_reached"
        assert "time limit (1 minutes)" in row.state_note

    async def test_the_cost_limit_stops_it_before_the_next_model_call(
        self, browser_on, monkeypatch, async_session
    ):
        org, user = await account(async_session, "limit-cost")
        row = await start(org, user, request=f"Check my bill on {SITE}")
        posted: list[dict] = []

        async def key():
            return "test-key"

        async def post(payload, key):
            posted.append(payload)
            return {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 2_000_000, "output_tokens": 10_000},
            }

        monkeypatch.setattr(bridge, "_key", key)
        monkeypatch.setattr(bridge, "_post", post)
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("navigate", BILL_URL) for _ in range(10)],
            think=True,
        )
        row = await run(row, fake)
        assert row.state == "limit_reached"
        assert "cost limit" in row.state_note
        assert len(posted) == 1  # the second call was never made
        assert posted[0]["model"] == constants.BROWSER_MODEL
        assert row.used["cost_paise"] >= row.limits["cost_paise"]

    async def test_with_no_model_key_it_fails_honestly(
        self, browser_on, monkeypatch, async_session
    ):
        org, user = await account(async_session, "limit-nokey")
        row = await start(org, user, request=f"Check my bill on {SITE}")

        async def no_key():
            return None

        monkeypatch.setattr(bridge, "_key", no_key)
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("navigate", BILL_URL)],
            think=True,
        )
        row = await run(row, fake)
        assert row.state == "failed"
        assert "model key" in row.state_note


# --- take over ---------------------------------------------------------------------------


class TestTakeOver:
    async def test_a_captcha_waits_for_the_person_and_their_typing_is_not_kept(
        self, browser_on, async_session
    ):
        org, user = await account(async_session, "takeover")
        row = await start(org, user, request=f"Check my bill on {SITE}")
        fake = FakeDriver(
            pages={
                BILL_URL: Page(
                    BILL_URL, bill_page('<a id="view" href="/bill">View bill</a>')
                )
            },
            plan=[
                Step("click", "view", goal="Open the bill"),
                Step("done", text="₹2,340"),
            ],
            captcha_on=BILL_URL,
        )
        from api.services.browser import drivers

        drivers.use(fake)
        job = asyncio.create_task(session.run(row.session_uuid, BILL_URL))

        async def state():
            return (
                await db_client.get_browser_session_for_worker(row.session_uuid)
            ).state

        await until(lambda: _is(state, "captcha"))
        current = await db_client.get_browser_session_for_worker(row.session_uuid)
        with pytest.raises(session.CommandRefused):
            await session.person_command(
                current, {"cmd": "input", "kind": "type", "text": "x"}
            )
        await session.person_command(current, {"cmd": "takeover"})
        await until(lambda: _is(state, "taken_over"))
        current = await db_client.get_browser_session_for_worker(row.session_uuid)
        await session.person_command(
            current, {"cmd": "input", "kind": "type", "text": "my-secret-password"}
        )
        await session.person_command(
            current, {"cmd": "input", "kind": "click", "x": 10, "y": 20}
        )
        with pytest.raises(session.CommandRefused):
            await session.person_command(
                current, {"cmd": "input", "kind": "key", "key": "F12"}
            )
        await until(lambda: len(fake.boxes["fake-1"].inputs) == 2)
        await session.person_command(current, {"cmd": "handback", "keep_login": False})
        await asyncio.wait_for(job, timeout=20)

        box = fake.boxes["fake-1"]
        assert box.inputs[0]["text"] == "my-secret-password"
        row = await db_client.get_browser_session_for_worker(row.session_uuid)
        assert row.state == "done"
        kinds = [s["kind"] for s in row.steps]
        assert "person" in kinds
        stored = json.dumps([row.steps, row.receipt, row.pending, row.used])
        assert "my-secret-password" not in stored
        events = await db_client.agent_events(
            organization_id=org.id, assistant_thread=True
        )
        assert "my-secret-password" not in json.dumps([e.payload for e in events])


async def _is(state_fn, wanted):
    return (await state_fn()) == wanted


class TestADeadJob:
    async def test_a_session_whose_job_died_is_ended_and_its_card_cancelled(
        self, browser_on, async_session
    ):
        from datetime import UTC, datetime, timedelta

        from api.db.browser_models import BrowserSessionModel

        fake = _form_fake()
        org, user = await account(async_session, "dead-job")
        row = await start(org, user, request=FORM_REQUEST, start_url=FORM_URL)
        from api.services.browser import drivers

        drivers.use(fake)
        job = asyncio.create_task(session.run(row.session_uuid, FORM_URL))
        waiting = await until(lambda: _pending(row))
        card_id = waiting.pending["event_id"]
        job.cancel()  # the worker died with a card waiting
        with pytest.raises(asyncio.CancelledError):
            await job
        assert await session.sweep() == 0  # not yet past any limit
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        stored = await async_session.get(BrowserSessionModel, row.id)
        stored.created_at = long_ago
        await async_session.flush()

        assert await session.sweep() == 1
        row = await db_client.get_browser_session_for_worker(row.session_uuid)
        assert row.state == "failed"
        assert "stopped unexpectedly" in row.state_note
        card = await db_client.get_agent_event(card_id, organization_id=org.id)
        assert card.payload["state"] == actions.CANCELLED
        assert await db_client.count_live_browser_sessions() == 0


class TestVersionedCards:
    """With the task ledger on, a browser card carries a version like every
    other card, and Confirm must name it."""

    async def test_confirm_must_name_the_version_and_then_presses_once(
        self, browser_on, monkeypatch, async_session
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        fake = _form_fake()
        org, user = await account(async_session, "versioned")
        row = await start(org, user, request=FORM_REQUEST, start_url=FORM_URL)
        from api.services.browser import drivers

        drivers.use(fake)
        job = asyncio.create_task(session.run(row.session_uuid, FORM_URL))
        waiting = await until(lambda: _pending(row))
        card_id = waiting.pending["event_id"]
        card = await db_client.get_agent_event(card_id, organization_id=org.id)
        version = card.payload["version"]
        assert version == actions.payload_version(card.payload)

        with pytest.raises(actions.ActionError):
            await actions.settle(
                organization_id=org.id,
                event_id=card_id,
                verb="confirm",
                user_id=user.id,
            )
        armed = await actions.settle(
            organization_id=org.id,
            event_id=card_id,
            verb="confirm",
            user_id=user.id,
            version=version,
        )
        assert armed["confirmed"]["version"] == version
        await actions.run(card_id, org.id)
        card = await db_client.get_agent_event(card_id, organization_id=org.id)
        assert card.payload["state"] == actions.DONE, card.payload
        assert fake.boxes["fake-1"].pressed == [("submit", FORM_URL)]
        await asyncio.wait_for(job, timeout=20)


@pytest.fixture
async def committed(monkeypatch, test_engine):
    """The browser switched on against the real test database, as controls'
    own quota tests run: ``quotas.consume`` rolls back on a refusal, and in
    the shared transactional session that would roll back the test itself.
    Everything made here is deleted afterwards."""
    from uuid import uuid4

    from sqlalchemy import text

    import api.tasks.arq as arq

    monkeypatch.setattr(constants, "DECIBYL_BROWSER_ENABLED", True)
    monkeypatch.setattr(constants, "BROWSER_DRIVER", "fake")
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)

    async def enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(arq, "enqueue_job", enqueue)
    channel.reset()
    run_id = uuid4().hex[:8]
    async with db_client.async_session() as s:
        org_id = (
            await s.execute(
                text(
                    "INSERT INTO organizations (provider_id, quota_decibyl_tokens, created_at) "
                    "VALUES (:p, 0, now()) RETURNING id"
                ),
                {"p": f"org-minutes-{run_id}"},
            )
        ).scalar_one()
        await s.commit()
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"user-minutes-{run_id}"
    )
    async with db_client.async_session() as s:
        await s.execute(
            text("UPDATE users SET selected_organization_id = :o WHERE id = :u"),
            {"o": org_id, "u": user.id},
        )
        await s.commit()
    try:
        yield SimpleNamespace(id=org_id), SimpleNamespace(id=user.id)
    finally:
        from api.services.browser import drivers

        drivers.use(None)
        channel.reset()
        async with db_client.async_session() as s:
            for sql in (
                "DELETE FROM operational_usage WHERE user_id = :u",
                "DELETE FROM browser_sessions WHERE user_id = :u",
                "DELETE FROM agent_events WHERE organization_id = :o",
                "DELETE FROM users WHERE id = :u",
                "DELETE FROM organizations WHERE id = :o",
            ):
                await s.execute(text(sql), {"u": user.id, "o": org_id})
            await s.commit()


class TestBrowserMinutes:
    """Each task spends the person's daily browser minutes (controls'
    operational quotas): one as it starts, one per further minute."""

    async def test_at_the_limit_no_browser_opens_and_the_thread_says_why(
        self, committed, monkeypatch
    ):
        from api.services import quotas

        monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_BROWSER_MINUTES", 1)
        org, user = committed
        await quotas.consume(user.id, quotas.BROWSER_MINUTES)
        result = await tool.for_thread(
            org.id,
            {"task": "Check my bill", "sites": [SITE]},
            author_id=user.id,
            request=f"Check my bill on {SITE}",
            thread_id=None,
        )
        assert result["status"] == "quota_reached"
        assert "browser minute" in result["reason"]
        rows = await db_client.agent_events(
            organization_id=org.id,
            kinds=["message", "browser_session"],
            assistant_thread=True,
        )
        assert [r.kind for r in rows] == ["message"]
        assert rows[0].payload["quota"]["kind"] == "browser_minutes"

    async def test_a_running_task_spends_minutes_and_stops_at_the_limit(
        self, committed, monkeypatch
    ):
        from api.services import quotas

        monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_BROWSER_MINUTES", 3)
        org, user = committed
        row = await start(org, user, request=f"Check my bill on {SITE}", minutes=30)
        assert (await quotas.usage(user.id, quotas.BROWSER_MINUTES)).used == 1
        clock = {"t": 1000.0}

        def monotonic():
            clock["t"] += 15
            return clock["t"]

        monkeypatch.setattr(session, "time", SimpleNamespace(monotonic=monotonic))
        fake = FakeDriver(
            pages={BILL_URL: Page(BILL_URL, bill_page())},
            plan=[Step("navigate", BILL_URL) for _ in range(60)],
            step_delay=0.02,
        )
        row = await run(row, fake)
        assert row.state == "limit_reached"
        assert "browser minutes" in row.state_note
        assert (await quotas.usage(user.id, quotas.BROWSER_MINUTES)).used == 3

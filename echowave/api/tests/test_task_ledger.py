"""The task ledger and payload-bound approvals (handoff 5, 10, 15 B; design
"Task state", "Approval state", "Event delivery"; launch stream controls).

Done when: one request makes one task however often it is retried; a writer
holding an old version changes nothing; completion needs evidence; an
approval is for one exact payload version, so editing a card invalidates
its Confirm on every channel; the same Confirm from two channels runs once;
a send whose outcome is lost reads as unknown and is never re-fired; and
none of it reaches another workspace.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventActor, AgentEventKind
from api.services.messaging.channels import base as channel_base
from api.services.workflow import actions, task_ledger


@pytest.fixture
def ledger_on(monkeypatch):
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"ledger-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


@pytest.fixture
async def two_orgs(test_engine):
    a, b = await _org(), await _org()
    yield a, b
    async with db_client.async_session() as session:
        for org in (a, b):
            for table in ("agent_task_transitions", "agent_tasks", "agent_events"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.commit()


def _send_payload(**args) -> dict:
    return {
        "state": actions.PROPOSED,
        "label": "Send the quote via gmail",
        "action": actions.RUN_TOOL,
        "effect": "Runs in gmail and reaches people there. It cannot be undone.",
        "reaches_people": True,
        "args": {
            "tool_uuid": "t-1",
            "tool_name": "GMAIL_SEND_EMAIL",
            "toolkit": "gmail",
            "arguments": args or {"to": "rao@example.com", "body": "Quote attached"},
        },
    }


async def _card(organization_id: int, payload: dict | None = None) -> int:
    payload = payload or _send_payload()
    payload.setdefault("version", actions.payload_version(payload))
    return int(
        await db_client.record_agent_event(
            organization_id=organization_id,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary=payload["label"],
            payload=payload,
        )
    )


async def _payload(organization_id: int, event_id: int) -> dict:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return dict(event.payload or {})


def _quiet():
    return (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
        patch.object(actions.audit_log, "record", new=AsyncMock()),
    )


# --- the ledger ---------------------------------------------------------------


@pytest.mark.asyncio
class TestTheLedger:
    async def test_one_key_one_task_however_often_retried(self, two_orgs, ledger_on):
        org, _ = two_orgs
        made = await asyncio.gather(
            *(
                task_ledger.create(
                    organization_id=org,
                    title="Remind Ravi about the proposal",
                    created_by=None,
                    idempotency_key="chat-42:remind-ravi",
                )
                for _ in range(5)
            ),
            return_exceptions=True,
        )
        tasks = [m for m in made if isinstance(m, tuple)]
        assert {t.id for t, _ in tasks} and len({t.id for t, _ in tasks}) == 1
        assert sum(1 for _, created in tasks if created) == 1
        async with db_client.async_session() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM agent_tasks WHERE organization_id = :o"),
                {"o": org},
            )
        assert count == 1

    async def test_the_same_key_in_another_workspace_is_another_task(
        self, two_orgs, ledger_on
    ):
        a, b = two_orgs
        ta, _ = await task_ledger.create(
            organization_id=a, title="x", created_by=None, idempotency_key="k"
        )
        tb, created = await task_ledger.create(
            organization_id=b, title="x", created_by=None, idempotency_key="k"
        )
        assert created and ta.id != tb.id

    async def test_a_stale_writer_changes_nothing(self, two_orgs, ledger_on):
        org, _ = two_orgs
        task, _ = await task_ledger.create(
            organization_id=org, title="t", created_by=None
        )
        moved = await task_ledger.transition(
            organization_id=org, task_id=task.id, to_state="running", expected_version=1
        )
        assert moved.state_version == 2 and moved.status == "in_progress"
        with pytest.raises(task_ledger.StaleState) as caught:
            # A late worker that read version 1 tries to fail it.
            await task_ledger.transition(
                organization_id=org,
                task_id=task.id,
                to_state="failed",
                expected_version=1,
            )
        assert caught.value.current == 2
        fresh = await db_client.get_task(task.id, organization_id=org)
        assert task_ledger.state_of(fresh) == "running"

    async def test_two_writers_on_one_version_one_wins(self, two_orgs, ledger_on):
        org, _ = two_orgs
        task, _ = await task_ledger.create(
            organization_id=org, title="t", created_by=None
        )
        results = await asyncio.gather(
            task_ledger.transition(
                organization_id=org,
                task_id=task.id,
                to_state="running",
                expected_version=1,
            ),
            task_ledger.transition(
                organization_id=org,
                task_id=task.id,
                to_state="cancelled",
                expected_version=1,
            ),
            return_exceptions=True,
        )
        assert sum(1 for r in results if isinstance(r, task_ledger.StaleState)) == 1
        history = await task_ledger.history(org, task.id)
        assert [h["sequence"] for h in history] == [1, 2]

    async def test_completion_needs_evidence(self, two_orgs, ledger_on):
        org, _ = two_orgs
        task, _ = await task_ledger.create(
            organization_id=org, title="t", created_by=None
        )
        task = await task_ledger.transition(
            organization_id=org, task_id=task.id, to_state="running", expected_version=1
        )
        with pytest.raises(task_ledger.LedgerError):
            await task_ledger.transition(
                organization_id=org,
                task_id=task.id,
                to_state="completed",
                expected_version=2,
            )
        done = await task_ledger.transition(
            organization_id=org,
            task_id=task.id,
            to_state="completed",
            expected_version=2,
            evidence={"message_id": "msg-123"},
        )
        assert done.status == "done" and done.finished_at is not None

    async def test_terminal_is_terminal_and_unknown_waits_for_reconciliation(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        task, _ = await task_ledger.create(
            organization_id=org, title="t", created_by=None
        )
        await task_ledger.transition(
            organization_id=org, task_id=task.id, to_state="running", expected_version=1
        )
        await task_ledger.transition(
            organization_id=org,
            task_id=task.id,
            to_state="outcome_unknown",
            expected_version=2,
            reason_code="timeout",
        )
        with pytest.raises(task_ledger.NotAllowed):
            # Never back to running: that is a blind retry.
            await task_ledger.transition(
                organization_id=org,
                task_id=task.id,
                to_state="running",
                expected_version=3,
            )
        await task_ledger.transition(
            organization_id=org,
            task_id=task.id,
            to_state="failed",
            expected_version=3,
            reason_code="reconciled_not_sent",
        )
        for state in task_ledger.STATES:
            with pytest.raises(task_ledger.NotAllowed):
                await task_ledger.transition(
                    organization_id=org,
                    task_id=task.id,
                    to_state=state,
                    expected_version=4,
                )

    async def test_another_workspace_cannot_read_or_move_it(self, two_orgs, ledger_on):
        a, b = two_orgs
        task, _ = await task_ledger.create(
            organization_id=a, title="t", created_by=None
        )
        with pytest.raises(task_ledger.LedgerError, match="not here"):
            await task_ledger.transition(
                organization_id=b,
                task_id=task.id,
                to_state="running",
                expected_version=1,
            )
        assert await task_ledger.history(b, task.id) == []

    async def test_every_state_is_offered(self):
        assert set(task_ledger.STATES) == {
            "queued",
            "running",
            "needs_input",
            "awaiting_approval",
            "scheduled",
            "completed",
            "failed",
            "cancelled",
            "outcome_unknown",
        }
        assert set(task_ledger.ALLOWED) == set(task_ledger.STATES)
        assert set(task_ledger.BOARD_STATUS) == set(task_ledger.STATES)

    async def test_an_old_row_reads_its_state_from_the_board(self):
        old = SimpleNamespace(ledger_state=None, status="could_not")
        assert task_ledger.state_of(old) == "failed"
        # Never a false empty: an unknown column is shown as needing input.
        odd = SimpleNamespace(ledger_state=None, status="mystery")
        assert task_ledger.state_of(odd) == "needs_input"

    async def test_the_board_move_is_read_by_the_ledger(self, two_orgs, ledger_on):
        org, _ = two_orgs
        task, _ = await task_ledger.create(
            organization_id=org, title="t", created_by=None
        )
        await db_client.update_task(task.id, organization_id=org, status="done")
        await task_ledger.follow_board(
            organization_id=org, task_id=task.id, status="done", user_id=None
        )
        fresh = await db_client.get_task(task.id, organization_id=org)
        assert task_ledger.state_of(fresh) == "completed"
        assert fresh.state_version == 2
        assert fresh.outcome_evidence == {"marked_done_by": None}


# --- approval binding on cards -----------------------------------------------


@pytest.mark.asyncio
class TestApprovalBinding:
    async def test_confirm_must_name_the_version_shown(self, two_orgs, ledger_on):
        org, _ = two_orgs
        event_id = await _card(org)
        version = (await _payload(org, event_id))["version"]
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            with pytest.raises(actions.ActionError, match="changed since you looked"):
                await actions.settle(
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=1,
                    version="0000000000000000",
                )
            with pytest.raises(actions.ActionError):
                await actions.settle(
                    organization_id=org, event_id=event_id, verb="confirm", user_id=1
                )
            payload = await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=version,
            )
        assert payload["state"] == actions.ARMED
        assert payload["confirmed"]["version"] == version
        assert payload["idempotency_key"] == f"card:{event_id}:{version}"
        assert payload["ledger_state"] == "scheduled"

    async def test_editing_invalidates_the_approval(self, two_orgs, ledger_on):
        org, _ = two_orgs
        event_id = await _card(org)
        old = (await _payload(org, event_id))["version"]
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=old,
            )
            revised = await actions.revise(
                organization_id=org,
                event_id=event_id,
                arguments={"to": "rao@example.com", "body": "Quote attached, 10% off"},
                user_id=1,
            )
            assert revised["state"] == actions.PROPOSED
            assert revised["version"] != old
            assert "confirmed" not in revised
            assert revised["revisions"][-1]["version"] == old
            with pytest.raises(actions.ActionError, match="changed since you looked"):
                await actions.settle(
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=1,
                    version=old,
                )

    async def test_an_armed_card_edited_in_its_window_does_not_fire(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await _card(org)
        version = (await _payload(org, event_id))["version"]
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=version,
            )
            await actions.revise(
                organization_id=org,
                event_id=event_id,
                arguments={"to": "someone-else@example.com", "body": "Hi"},
                user_id=1,
            )
        with patch.object(actions, "_execute", new=AsyncMock()) as execute:
            await actions.run(event_id, org)
        execute.assert_not_awaited()

    async def test_arguments_changed_after_approval_are_not_run(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await _card(org)
        version = (await _payload(org, event_id))["version"]
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=version,
            )
        # Something rewrote the arguments behind the card's back.
        payload = await _payload(org, event_id)
        payload["args"]["arguments"]["to"] = "attacker@example.com"
        await db_client.set_agent_event_payload(
            event_id, organization_id=org, payload=payload
        )
        with (
            patch.object(actions, "_execute", new=AsyncMock()) as execute,
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await actions.run(event_id, org)
        execute.assert_not_awaited()
        assert (await _payload(org, event_id))["state"] == actions.FAILED

    async def test_the_same_confirm_from_two_channels_runs_once(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await _card(org)
        version = (await _payload(org, event_id))["version"]
        tap = channel_base.parse_button_id(
            channel_base.button_id(event_id, "confirm", version)
        )
        assert tap.version == version
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue:
            results = await asyncio.gather(
                actions.settle(  # the web card
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=1,
                    version=version,
                ),
                actions.settle(  # the same card's button on WhatsApp
                    organization_id=org,
                    event_id=tap.event_id,
                    verb=tap.verb,
                    user_id=1,
                    version=tap.version,
                ),
                return_exceptions=True,
            )
        assert sum(isinstance(r, dict) for r in results) == 1
        assert enqueue.await_count == 1
        with (
            patch.object(
                actions, "_execute", new=AsyncMock(return_value="Sent.")
            ) as ex,
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await asyncio.gather(actions.run(event_id, org), actions.run(event_id, org))
        assert ex.await_count == 1

    async def test_a_card_from_another_workspace_is_not_found(
        self, two_orgs, ledger_on
    ):
        a_org, b_org = two_orgs
        event_id = await _card(a_org)
        version = (await _payload(a_org, event_id))["version"]
        with pytest.raises(actions.ActionError, match="not here"):
            await actions.settle(
                organization_id=b_org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=version,
            )
        with pytest.raises(actions.ActionError, match="not here"):
            await actions.revise(
                organization_id=b_org, event_id=event_id, arguments={"x": 1}, user_id=1
            )

    async def test_off_the_card_behaves_as_before(self, two_orgs):
        org, _ = two_orgs
        payload = _send_payload()
        event_id = int(
            await db_client.record_agent_event(
                organization_id=org,
                kind=AgentEventKind.ACTION_PROPOSED.value,
                actor=AgentEventActor.AGENT.value,
                summary="x",
                payload=payload,
            )
        )
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            out = await actions.settle(
                organization_id=org, event_id=event_id, verb="confirm", user_id=1
            )
        assert out["state"] == actions.ARMED and "version" not in out
        with pytest.raises(actions.ActionError):
            await actions.revise(
                organization_id=org, event_id=event_id, arguments={"x": 1}, user_id=1
            )

    async def test_an_old_button_still_parses_and_a_bad_version_does_not(self):
        assert channel_base.parse_button_id("card:5:confirm").version is None
        assert channel_base.parse_button_id("card:5:confirm:abc;rm") is None
        assert len(channel_base.button_id(10**9, "confirm", "a" * 16)) <= 64


# --- outcome unknown ----------------------------------------------------------


@pytest.mark.asyncio
class TestOutcomeUnknown:
    async def _armed(self, org) -> int:
        event_id = await _card(org)
        version = (await _payload(org, event_id))["version"]
        a, b, c = _quiet()
        with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=1,
                version=version,
            )
        return event_id

    async def test_a_send_that_breaks_midway_is_unknown_not_failed(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await self._armed(org)
        with (
            patch.object(
                actions,
                "_execute",
                new=AsyncMock(side_effect=TimeoutError("read timeout")),
            ),
            patch.object(actions, "_say", new=AsyncMock()) as say,
        ):
            await actions.run(event_id, org)
        payload = await _payload(org, event_id)
        assert payload["state"] == actions.OUTCOME_UNKNOWN
        assert payload["ledger_state"] == "outcome_unknown"
        assert "do not send it again" in say.await_args.args[1]
        # And it is never fired again.
        with patch.object(actions, "_execute", new=AsyncMock()) as execute:
            await actions.run(event_id, org)
        execute.assert_not_awaited()

    async def test_a_worker_lost_mid_card_is_swept_to_unknown(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await self._armed(org)
        payload = await _payload(org, event_id)
        payload["state"] = actions.RUNNING
        payload["fires_at"] = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        await db_client.set_agent_event_payload(
            event_id, organization_id=org, payload=payload
        )
        with patch.object(actions, "_say", new=AsyncMock()):
            marked = await actions.sweep_stale_running()
        assert marked >= 1
        assert (await _payload(org, event_id))["state"] == actions.OUTCOME_UNKNOWN

    async def test_a_running_card_inside_its_window_is_left_alone(
        self, two_orgs, ledger_on
    ):
        org, _ = two_orgs
        event_id = await self._armed(org)
        payload = await _payload(org, event_id)
        payload["state"] = actions.RUNNING
        payload["fires_at"] = datetime.now(UTC).isoformat()
        await db_client.set_agent_event_payload(
            event_id, organization_id=org, payload=payload
        )
        with patch.object(actions, "_say", new=AsyncMock()):
            await actions.sweep_stale_running()
        assert (await _payload(org, event_id))["state"] == actions.RUNNING


# --- outbound quota on a send --------------------------------------------------


@pytest.mark.asyncio
async def test_a_send_over_the_daily_limit_is_not_sent(
    two_orgs, ledger_on, monkeypatch
):
    from api.services import quotas

    monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
    monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_OUTBOUND_MESSAGES", 1)
    org, _ = two_orgs
    person, _ = await db_client.get_or_create_user_by_provider_id(f"send-{uuid4().hex}")
    try:
        sent = []
        for _ in range(2):
            event_id = await _card(org)
            version = (await _payload(org, event_id))["version"]
            a, b, c = _quiet()
            with a, b, c, patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
                await actions.settle(
                    organization_id=org,
                    event_id=event_id,
                    verb="confirm",
                    user_id=person.id,
                    version=version,
                )
            with (
                patch.object(
                    actions, "_execute", new=AsyncMock(return_value="Sent.")
                ) as ex,
                patch.object(actions, "_say", new=AsyncMock()),
            ):
                await actions.run(event_id, org)
            sent.append(ex.await_count)
        assert sent == [1, 0]
        assert (await _payload(org, event_id))[
            "reason_code"
        ] == "quota_outbound_messages"
    finally:
        async with db_client.async_session() as session:
            await session.execute(
                text("DELETE FROM operational_usage WHERE user_id = :u"),
                {"u": person.id},
            )
            await session.commit()
        assert quotas.OUTBOUND_MESSAGES == "outbound_messages"


# --- over HTTP -----------------------------------------------------------------


@asynccontextmanager
async def _client(user):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


def _user(org: int):
    return SimpleNamespace(id=None, selected_organization_id=org, provider_id="p")


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_ledger_routes_are_not_there(self, two_orgs):
        org, _ = two_orgs
        async with _client(_user(org)) as client:
            response = await client.post("/api/v1/tasks/ledger", json={"title": "t"})
        assert response.status_code == 404

    async def test_create_retry_move_and_stale(self, two_orgs, ledger_on):
        org, other = two_orgs
        async with _client(_user(org)) as client:
            first = await client.post(
                "/api/v1/tasks/ledger",
                json={"title": "Call the clinic"},
                headers={"Idempotency-Key": "abc-1"},
            )
            again = await client.post(
                "/api/v1/tasks/ledger",
                json={"title": "Call the clinic"},
                headers={"Idempotency-Key": "abc-1"},
            )
            assert first.status_code == 201
            assert again.json()["task_id"] == first.json()["task_id"]
            assert again.json()["created"] is False
            task_id = first.json()["task_id"]
            moved = await client.post(
                f"/api/v1/tasks/{task_id}/ledger/transition",
                json={"to": "running", "expected_version": 1},
            )
            assert moved.json()["state"] == "running"
            stale = await client.post(
                f"/api/v1/tasks/{task_id}/ledger/transition",
                json={"to": "cancelled", "expected_version": 1},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["current_version"] == 2
            ledger = await client.get(f"/api/v1/tasks/{task_id}/ledger")
            assert [h["to"] for h in ledger.json()["history"]] == ["queued", "running"]
        async with _client(_user(other)) as client:
            hidden = await client.get(f"/api/v1/tasks/{task_id}/ledger")
            assert hidden.status_code == 404
            refused = await client.post(
                f"/api/v1/tasks/{task_id}/ledger/transition",
                json={"to": "cancelled", "expected_version": 2},
            )
            assert refused.status_code == 404


@pytest.mark.asyncio
async def test_off_the_sweep_reads_nothing(test_engine, monkeypatch):
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", False)
    with patch.object(actions.db_client, "async_session") as session:
        assert await actions.sweep_stale_running() == 0
    session.assert_not_called()

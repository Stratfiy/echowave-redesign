"""The event catalogue, its envelope and the outbox (handoff 35, 36; design
"Metrics events and release proof"; launch stream controls).

Done when: every handoff event is catalogued with an owner; every envelope
carries the contract's fields with pseudonymous ids; no prompt, transcript,
email address, secret or free text can be sent; a client cannot record an
outcome; events are written with the change they describe and delivered
once with their id; and nothing is written while the switch is off.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.services import events
from api.services.events import catalogue, envelope, outbox

HANDOFF_EVENTS = {
    "invite_accepted",
    "onboarding_completed",
    "first_useful_task_completed",
    "task_started",
    "task_completed",
    "task_failed",
    "task_cancelled",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "approval_expired",
    "voice_session_started",
    "voice_session_ended",
    "voice_session_failed",
    "capture_started",
    "capture_failed",
    "meeting_processed",
    "action_confirmed",
    "reminder_scheduled",
    "reminder_delivered",
    "reminder_failed",
    "reminder_cancelled",
    "connection_started",
    "connection_ready",
    "connection_revoked",
    "ticket_created",
    "support_action_requested",
    "support_action_approved",
    "support_action_executed",
    "support_action_failed",
    "ticket_resolved",
    "ticket_reopened",
    "feedback_submitted",
    "evaluation_completed",
    "regression_detected",
    "payment_succeeded",
    "payment_failed",
    "refund_completed",
    "usage_cost_recorded",
}

ENVELOPE_FIELDS = {
    "event_id",
    "schema_version",
    "occurred_at",
    "environment",
    "release",
    "user_id",
    "workspace_id",
    "task_id",
    "trace_id",
    "configuration_version",
    "properties",
}


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")


@pytest.fixture
def catalogue_on(monkeypatch, keyed):
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)


async def _outbox(event_id: str):
    async with db_client.async_session() as session:
        return (
            await session.execute(
                text(
                    "SELECT name, envelope, delivered_at, attempts FROM analytics_outbox WHERE event_id = :e"
                ),
                {"e": event_id},
            )
        ).first()


async def _cleanup(*event_ids):
    async with db_client.async_session() as session:
        await session.execute(
            text("DELETE FROM analytics_outbox WHERE event_id = ANY(:e)"),
            {"e": [e for e in event_ids if e]},
        )
        await session.commit()


class TestTheCatalogue:
    def test_every_handoff_event_is_there(self):
        assert HANDOFF_EVENTS <= set(catalogue.CATALOGUE)

    def test_every_handoff_event_is_owned_by_the_server(self):
        for name in HANDOFF_EVENTS:
            assert catalogue.get(name).owner == catalogue.SERVER, name

    def test_no_entry_may_carry_a_forbidden_property(self):
        for spec in catalogue.CATALOGUE.values():
            assert not (spec.allowed & catalogue.FORBIDDEN_PROPERTIES), spec.name

    def test_the_common_typed_properties(self):
        assert catalogue.COMMON_PROPERTIES == {
            "channel",
            "language",
            "agent_type",
            "status",
            "reason_code",
            "duration_ms",
        }


class TestTheEnvelope:
    def test_it_carries_the_contract_and_pseudonyms(self, keyed):
        env = envelope.build(
            "task_completed",
            user_id=42,
            organization_id=7,
            task_id=9,
            trace_id="run-1",
            configuration_version="v3",
            properties={"status": "completed", "has_evidence": True},
        )
        assert ENVELOPE_FIELDS <= set(env)
        assert env["user_id"].startswith("u_") and "42" not in env["user_id"]
        assert env["workspace_id"].startswith("w_")
        assert env["user_id"] == envelope.build("task_started", user_id=42)["user_id"]
        assert (
            datetime.fromisoformat(env["occurred_at"]).utcoffset().total_seconds() == 0
        )
        assert env["schema_version"] == 1

    def test_nullable_fields_are_present_and_null(self, keyed):
        env = envelope.build("approval_viewed")
        for field in (
            "user_id",
            "workspace_id",
            "task_id",
            "trace_id",
            "configuration_version",
        ):
            assert field in env and env[field] is None

    def test_without_a_key_nothing_is_built(self, monkeypatch):
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "")
        with pytest.raises(envelope.EventRefused):
            envelope.build("task_started", user_id=1)

    @pytest.mark.parametrize(
        "properties",
        [
            {"prompt": "what is my bank balance"},
            {"transcript": "hello"},
            {"email": "ravi@example.com"},
            {"body": "Dear Ravi"},
            {"reason_code": "The user said: please call my mother on +91 98450 00000"},
            {"reason_code": "ravi@example.com"},
            {"status": "x" * 65},
            {"not_in_the_catalogue": 1},
            {"reasons": ["wrong"] * 11},
            {"duration_ms": -5},
        ],
    )
    def test_private_or_free_text_is_refused(self, keyed, properties):
        name = "feedback_submitted" if "reasons" in properties else "task_failed"
        with pytest.raises(envelope.EventRefused):
            envelope.build(name, properties=properties)

    def test_an_unknown_event_is_refused(self, keyed):
        with pytest.raises(envelope.EventRefused):
            envelope.build("user_clicked_everything")


@pytest.mark.asyncio
class TestTheOutbox:
    async def test_off_nothing_is_written(self, test_engine, keyed):
        assert await events.emit("task_started", user_id=1) is None

    async def test_on_it_is_written_once(self, test_engine, catalogue_on):
        event_id = await events.emit(
            "task_started", user_id=1, properties={"status": "running"}
        )
        try:
            row = await _outbox(event_id)
            assert row.name == "task_started" and row.delivered_at is None
            assert row.envelope["properties"] == {"status": "running"}
            again = await events.emit("task_started", event_id=event_id, user_id=1)
            assert again == event_id
            async with db_client.async_session() as session:
                count = await session.scalar(
                    text("SELECT count(*) FROM analytics_outbox WHERE event_id = :e"),
                    {"e": event_id},
                )
            assert count == 1
        finally:
            await _cleanup(event_id)

    async def test_a_refused_event_writes_nothing_and_does_not_raise(
        self, test_engine, catalogue_on
    ):
        assert await events.emit("task_failed", properties={"prompt": "secret"}) is None

    async def test_an_event_rolls_back_with_its_change(self, test_engine, catalogue_on):
        async with db_client.async_session() as session:
            event_id = await events.emit("task_started", session=session, user_id=1)
            await session.rollback()
        assert await _outbox(event_id) is None

    async def test_a_ledger_move_writes_its_event_with_it(
        self, test_engine, catalogue_on, monkeypatch
    ):
        from api.services.workflow import task_ledger

        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        async with db_client.async_session() as session:
            org = OrganizationModel(provider_id=f"events-{uuid4().hex}")
            session.add(org)
            await session.flush()
            organization_id = org.id
            await session.commit()
        task, _ = await task_ledger.create(
            organization_id=organization_id, title="t", created_by=None
        )
        await task_ledger.transition(
            organization_id=organization_id,
            task_id=task.id,
            to_state="running",
            expected_version=1,
        )
        async with db_client.async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT event_id, envelope FROM analytics_outbox "
                        "WHERE name = 'task_started' AND envelope->>'task_id' = :t"
                    ),
                    {"t": str(task.id)},
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].envelope["workspace_id"] != str(organization_id)
        await _cleanup(*(r.event_id for r in rows))


@pytest.mark.asyncio
class TestDelivery:
    async def test_sent_once_with_its_id_and_no_raw_ids(
        self, test_engine, catalogue_on
    ):
        event_id = await events.emit(
            "approval_granted",
            user_id=5,
            organization_id=6,
            task_id="card:12",
            properties={"action_kind": "run_tool", "status": "armed"},
        )
        client = MagicMock()
        try:
            with patch("api.services.posthog_client.get_posthog", return_value=client):
                await outbox.deliver_pending()
                await outbox.deliver_pending()
            sent = [
                c
                for c in client.capture.call_args_list
                if c.kwargs.get("uuid") == event_id
            ]
            assert len(sent) == 1
            call = sent[0]
            assert call.args[0] == "approval_granted"
            assert call.kwargs["distinct_id"].startswith("u_")
            properties = call.kwargs["properties"]
            assert properties["$process_person_profile"] is False
            assert properties["env_workspace_id"].startswith("w_")
            assert 5 not in properties.values() and 6 not in properties.values()
            assert (await _outbox(event_id)).delivered_at is not None
        finally:
            await _cleanup(event_id)

    async def test_unconfigured_analytics_keeps_the_rows_waiting(
        self, test_engine, catalogue_on
    ):
        event_id = await events.emit("task_started", user_id=1)
        try:
            with patch("api.services.posthog_client.get_posthog", return_value=None):
                await outbox.deliver_pending()
            row = await _outbox(event_id)
            assert row.delivered_at is None and row.attempts == 0
        finally:
            await _cleanup(event_id)


@asynccontextmanager
async def _client(user):
    from api.app import app
    from api.services.auth.depends import get_staff, get_user

    app.dependency_overrides[get_user] = lambda: user
    app.dependency_overrides[get_staff] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)
        app.dependency_overrides.pop(get_staff, None)


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, test_engine):
        user = SimpleNamespace(id=1, selected_organization_id=None)
        async with _client(user) as client:
            response = await client.post(
                "/api/v1/events/client", json={"name": "approval_viewed"}
            )
            assert response.status_code == 404
            assert (
                await client.get("/api/v1/admin/controls/events/catalogue")
            ).status_code == 404

    async def test_a_client_may_state_intent_but_never_an_outcome(
        self, test_engine, catalogue_on
    ):
        user = SimpleNamespace(id=1, selected_organization_id=None)
        async with _client(user) as client:
            outcome = await client.post(
                "/api/v1/events/client",
                json={"name": "task_completed", "properties": {"status": "completed"}},
            )
            assert outcome.status_code == 422
            leaky = await client.post(
                "/api/v1/events/client",
                json={"name": "approval_viewed", "properties": {"email": "a@b.co"}},
            )
            assert leaky.status_code == 422
            intent = await client.post(
                "/api/v1/events/client", json={"name": "approval_viewed"}
            )
            assert intent.status_code == 200
            event_id = intent.json()["event_id"]
            listed = await client.get("/api/v1/admin/controls/events/catalogue")
            assert {"approval_viewed", "task_completed"} <= {
                e["name"] for e in listed.json()["events"]
            }
        row = await _outbox(event_id)
        assert row.envelope["owner"] == "client"
        await _cleanup(event_id)


@pytest.mark.asyncio
async def test_a_card_from_proposal_to_done_writes_each_event(
    test_engine, catalogue_on
):
    """Found by running it: a card's run sent ``action_kind`` on task_*
    events, which the catalogue refuses, so nothing was recorded."""
    from unittest.mock import AsyncMock

    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import actions

    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"events-card-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = org.id
        await session.commit()
    event_id = await db_client.record_agent_event(
        organization_id=organization_id,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        actor=AgentEventActor.AGENT.value,
        summary="Turn Front desk on",
        payload={
            "state": "proposed",
            "label": "Turn Front desk on",
            "action": "turn_bot_on",
        },
    )
    with (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
    ):
        await actions.settle(
            organization_id=organization_id,
            event_id=event_id,
            verb="confirm",
            user_id=3,
        )
    with (
        patch.object(actions, "_execute", new=AsyncMock(return_value="It is on.")),
        patch.object(actions, "_say", new=AsyncMock()),
    ):
        await actions.run(event_id, organization_id)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT event_id, name, envelope FROM analytics_outbox "
                    "WHERE envelope->>'task_id' = :t"
                ),
                {"t": f"card:{event_id}"},
            )
        ).all()
    names = sorted(r.name for r in rows)
    assert names == ["approval_granted", "task_completed", "task_started"]
    completed = next(r for r in rows if r.name == "task_completed")
    assert completed.envelope["properties"]["task_kind"] == "turn_bot_on"
    assert completed.envelope["properties"]["has_evidence"] is True
    await _cleanup(*(r.event_id for r in rows))

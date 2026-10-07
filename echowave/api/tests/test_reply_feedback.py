"""Was this useful? Yes / Not quite (handoff 2, 4, 6; launch stream controls).

Done when: a person can say Yes or Not quite (with optional reasons) on one
of Decibyl's replies or a finished task; it is stored against the output,
model and task versions; saying it again replaces it; only replies and
tasks the person can see can be judged; and analytics gets the verdict and
reason codes, never the words.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import feedback
from api.services.workflow import task_ledger


@pytest.fixture
def feedback_on(monkeypatch):
    monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True)


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"feedback-{uuid4().hex}")
        session.add(org)
        await session.flush()
        organization_id = org.id
        await session.commit()
    return organization_id


@pytest.fixture
async def setting(test_engine):
    a_org, b_org = await _org(), await _org()
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"fb-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"fb-b-{run}")
    reply = await db_client.record_agent_event(
        organization_id=a_org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary="Your next appointment is on Thursday.",
        payload={
            "body": "Your next appointment is on Thursday.",
            "from": "Decibyl",
            "preset": "auto",
            "model": "anthropic:sonnet",
        },
    )
    question = await db_client.record_agent_event(
        organization_id=a_org,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary="When is my appointment?",
        payload={"body": "When is my appointment?", "author_id": a.id},
    )
    yield SimpleNamespace(
        a=a, b=b, a_org=a_org, b_org=b_org, reply=reply, question=question
    )
    async with db_client.async_session() as session:
        for org in (a_org, b_org):
            for table in (
                "output_feedback",
                "agent_task_transitions",
                "agent_tasks",
                "agent_events",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.commit()


@pytest.mark.asyncio
class TestOnAReply:
    async def test_stored_against_output_and_model(self, setting, feedback_on):
        saved = await feedback.submit(
            organization_id=setting.a_org,
            user_id=setting.a.id,
            subject_kind="reply",
            subject_id=setting.reply,
            verdict="not_quite",
            reasons=["too_long", "wrong_language"],
        )
        assert saved["output_version"] == feedback.version_of(
            "Your next appointment is on Thursday."
        )
        async with db_client.async_session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM output_feedback WHERE id = :i"),
                        {"i": saved["id"]},
                    )
                )
                .mappings()
                .first()
            )
        assert row["model"] == "anthropic:sonnet"
        assert row["reasons"] == ["too_long", "wrong_language"]
        assert row["task_version"] is None

    async def test_saying_it_again_replaces_it(self, setting, feedback_on):
        kwargs = dict(
            organization_id=setting.a_org,
            user_id=setting.a.id,
            subject_kind="reply",
            subject_id=setting.reply,
        )
        first = await feedback.submit(**kwargs, verdict="not_quite", reasons=["wrong"])
        second = await feedback.submit(**kwargs, verdict="yes")
        assert first["id"] == second["id"]
        mine = await feedback.mine(
            organization_id=setting.a_org,
            user_id=setting.a.id,
            subject_kind="reply",
            subject_ids=[setting.reply],
        )
        assert mine[setting.reply] == {"verdict": "yes", "reasons": []}

    @pytest.mark.parametrize(
        "verdict,reasons",
        [("maybe", []), ("yes", ["wrong"]), ("not_quite", ["it was rude"])],
    )
    async def test_only_the_offered_answers(
        self, setting, feedback_on, verdict, reasons
    ):
        with pytest.raises(feedback.FeedbackRefused):
            await feedback.submit(
                organization_id=setting.a_org,
                user_id=setting.a.id,
                subject_kind="reply",
                subject_id=setting.reply,
                verdict=verdict,
                reasons=reasons,
            )

    async def test_a_persons_own_line_is_not_a_reply(self, setting, feedback_on):
        with pytest.raises(feedback.NotFound):
            await feedback.submit(
                organization_id=setting.a_org,
                user_id=setting.a.id,
                subject_kind="reply",
                subject_id=setting.question,
                verdict="yes",
            )

    async def test_another_workspaces_reply_is_not_found(self, setting, feedback_on):
        with pytest.raises(feedback.NotFound):
            await feedback.submit(
                organization_id=setting.b_org,
                user_id=setting.b.id,
                subject_kind="reply",
                subject_id=setting.reply,
                verdict="yes",
            )


@pytest.mark.asyncio
class TestOnATask:
    async def test_only_a_finished_task_with_its_version(
        self, setting, feedback_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        task, _ = await task_ledger.create(
            organization_id=setting.a_org,
            title="Draft the reply",
            created_by=setting.a.id,
        )
        with pytest.raises(feedback.FeedbackRefused):
            await feedback.submit(
                organization_id=setting.a_org,
                user_id=setting.a.id,
                subject_kind="task",
                subject_id=task.id,
                verdict="yes",
            )
        await task_ledger.transition(
            organization_id=setting.a_org,
            task_id=task.id,
            to_state="running",
            expected_version=1,
        )
        await task_ledger.transition(
            organization_id=setting.a_org,
            task_id=task.id,
            to_state="completed",
            expected_version=2,
            evidence={"message_id": "m-1"},
        )
        saved = await feedback.submit(
            organization_id=setting.a_org,
            user_id=setting.a.id,
            subject_kind="task",
            subject_id=task.id,
            verdict="yes",
        )
        async with db_client.async_session() as session:
            version = await session.scalar(
                text("SELECT task_version FROM output_feedback WHERE id = :i"),
                {"i": saved["id"]},
            )
        assert version == 3


@pytest.mark.asyncio
async def test_analytics_gets_codes_never_words(setting, feedback_on, monkeypatch):
    monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
    monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "k")
    await feedback.submit(
        organization_id=setting.a_org,
        user_id=setting.a.id,
        subject_kind="reply",
        subject_id=setting.reply,
        verdict="not_quite",
        reasons=["irrelevant"],
    )
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT event_id, envelope FROM analytics_outbox "
                    "WHERE name = 'feedback_submitted' AND created_at > now() - interval '1 minute'"
                )
            )
        ).all()
        mine = [
            r for r in rows if r.envelope["properties"].get("reasons") == ["irrelevant"]
        ]
        assert mine
        blob = str(mine[-1].envelope)
        assert (
            "Thursday" not in blob
            and str(setting.a.id) not in blob.split("event_id")[0]
        )
        await session.execute(
            text("DELETE FROM analytics_outbox WHERE event_id = ANY(:e)"),
            {"e": [r.event_id for r in mine]},
        )
        await session.commit()


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


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_route_is_not_there(self, setting):
        user = SimpleNamespace(id=setting.a.id, selected_organization_id=setting.a_org)
        async with _client(user) as client:
            response = await client.post(
                "/api/v1/feedback",
                json={
                    "subject_kind": "reply",
                    "subject_id": setting.reply,
                    "verdict": "yes",
                },
            )
        assert response.status_code == 404

    async def test_given_and_read_back_over_http(self, setting, feedback_on):
        user = SimpleNamespace(id=setting.a.id, selected_organization_id=setting.a_org)
        async with _client(user) as client:
            saved = await client.post(
                "/api/v1/feedback",
                json={
                    "subject_kind": "reply",
                    "subject_id": setting.reply,
                    "verdict": "not_quite",
                    "reasons": ["too_late"],
                },
            )
            assert saved.status_code == 200
            mine = await client.get(
                "/api/v1/feedback/mine",
                params={"subject_kind": "reply", "ids": [setting.reply]},
            )
        assert mine.json()["answers"][str(setting.reply)]["reasons"] == ["too_late"]
        assert set(mine.json()["reasons"]) == set(feedback.REASONS)

    async def test_another_workspace_cannot_judge_it_or_read_it(
        self, setting, feedback_on
    ):
        await feedback.submit(
            organization_id=setting.a_org,
            user_id=setting.a.id,
            subject_kind="reply",
            subject_id=setting.reply,
            verdict="yes",
        )
        user = SimpleNamespace(id=setting.b.id, selected_organization_id=setting.b_org)
        async with _client(user) as client:
            judged = await client.post(
                "/api/v1/feedback",
                json={
                    "subject_kind": "reply",
                    "subject_id": setting.reply,
                    "verdict": "yes",
                },
            )
            assert judged.status_code == 404
            mine = await client.get(
                "/api/v1/feedback/mine",
                params={"subject_kind": "reply", "ids": [setting.reply]},
            )
        assert mine.json()["answers"] == {}

    async def test_a_private_thread_reply_is_its_authors(
        self, setting, feedback_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        user = SimpleNamespace(id=setting.b.id, selected_organization_id=setting.a_org)
        async with _client(user) as client:
            response = await client.post(
                "/api/v1/feedback",
                json={
                    "subject_kind": "reply",
                    "subject_id": setting.reply,
                    "verdict": "yes",
                },
            )
        assert response.status_code == 404

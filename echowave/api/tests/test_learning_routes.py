"""Learning over HTTP (launch stream `learning`): what the two screens call.

Done when: every route is a 404 while ``learning`` is off and Today's list is
a 404 while ``learning_today`` is off; on, a person goes from profile to a
marked answer and progress over HTTP; a retry with the same
``Idempotency-Key`` changes nothing; refusals carry a code the screen can
act on (needs setup, confirm sensitive, conflict, quota); and another
person's goal id is a 404.
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

GOOD = "The key idea is this, and an example is that."


@pytest.fixture
def learning_on(monkeypatch):
    monkeypatch.setattr(constants, "LEARNING_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TEACHER", "fake")


@pytest.fixture
async def people(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"lr-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"lr-b-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"lr-org-{run}", a.id
    )
    yield a, b, org.id
    async with db_client.async_session() as session:
        for table in (
            "learning_goals",
            "learner_profiles",
            "agent_events",
            "credit_ledger",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org.id}
            )
        await session.commit()


@asynccontextmanager
async def _client(user_id: int, org: int):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=user_id, selected_organization_id=org
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


async def _placed(c) -> dict:
    status = (await c.get("/api/v1/learning/status")).json()
    await c.put(
        "/api/v1/learning/profile",
        json={"adult_confirmed": True, "revision": status["profile"]["revision"]},
    )
    started = await c.post("/api/v1/learning/goals", json={"title": "Fractions"})
    assert started.status_code == 201, started.text
    goal_id = started.json()["goal"]["goal_id"]
    placed = await c.post(
        f"/api/v1/learning/goals/{goal_id}/baseline", json={"answer": "A little."}
    )
    assert placed.status_code == 200, placed.text
    return placed.json()


@pytest.mark.asyncio
class TestOff:
    async def test_every_route_is_a_404_while_off(self, people):
        a, _, org = people
        async with _client(a.id, org) as c:
            for path in (
                "/api/v1/learning/status",
                "/api/v1/learning/goals",
                "/api/v1/learning/reviews",
                "/api/v1/learning/suggestions",
            ):
                assert (await c.get(path)).status_code == 404, path
            started = await c.post("/api/v1/learning/goals", json={"title": "x"})
            assert started.status_code == 404

    async def test_todays_list_needs_its_own_switch(self, people, learning_on):
        a, _, org = people
        async with _client(a.id, org) as c:
            assert (await c.get("/api/v1/learning/reviews")).status_code == 404
            assert (await c.get("/api/v1/learning/status")).json()["today"] is False

    async def test_an_organisation_override_turns_it_on_there_only(
        self, people, monkeypatch
    ):
        from api.services import features

        a, _, org = people
        monkeypatch.setattr(constants, "LEARNING_TEACHER", "fake")
        features.set_snapshot({("learning", org): features.Override(enabled=True)})
        try:
            async with _client(a.id, org) as c:
                assert (await c.get("/api/v1/learning/status")).status_code == 200
            async with _client(a.id, org + 10_000) as c:
                assert (await c.get("/api/v1/learning/status")).status_code == 404
        finally:
            features.clear_snapshot()


@pytest.mark.asyncio
class TestJourney:
    async def test_status_says_sample_and_available(self, people, learning_on):
        a, _, org = people
        async with _client(a.id, org) as c:
            body = (await c.get("/api/v1/learning/status")).json()
        assert body["state"] == "available" and body["teacher"] == "sample"
        assert body["profile"]["adult_confirmed"] is False
        assert "hi-IN" in body["languages"]

    async def test_profile_first(self, people, learning_on):
        a, _, org = people
        async with _client(a.id, org) as c:
            refused = await c.post("/api/v1/learning/goals", json={"title": "Chess"})
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "profile_needed"

    async def test_lesson_answer_progress(self, people, learning_on, monkeypatch):
        monkeypatch.setattr(constants, "LEARNING_TODAY_ENABLED", True)
        a, _, org = people
        async with _client(a.id, org) as c:
            state = await _placed(c)
            goal_id = state["goal"]["goal_id"]
            assert state["state"] == "waiting_for_answer"
            assert state["lesson"]["source_kind"] == "general"
            missing_key = await c.post(
                f"/api/v1/learning/goals/{goal_id}/attempts",
                json={"exercise_id": state["exercise"]["exercise_id"], "answer": GOOD},
            )
            assert missing_key.status_code == 422
            marked = await c.post(
                f"/api/v1/learning/goals/{goal_id}/attempts",
                json={"exercise_id": state["exercise"]["exercise_id"], "answer": GOOD},
                headers={"Idempotency-Key": "once-1"},
            )
            assert marked.status_code == 200, marked.text
            body = marked.json()
            assert body["attempt"]["outcome"] == "passed"
            assert body["replayed"] is False
            assert body["session"]["state"] == "completed"
            retried = await c.post(
                f"/api/v1/learning/goals/{goal_id}/attempts",
                json={"exercise_id": state["exercise"]["exercise_id"], "answer": GOOD},
                headers={"Idempotency-Key": "once-1"},
            )
            assert retried.json()["replayed"] is True
            progress = (
                await c.get(f"/api/v1/learning/goals/{goal_id}/progress")
            ).json()
            assert progress["practice_count"] == 1
            assert progress["skills"][0]["label"] == "Practised"
            skill = await c.get(
                f"/api/v1/learning/goals/{goal_id}/skills/"
                f"{progress['skills'][0]['skill_id']}"
            )
            assert skill.status_code == 200 and skill.json()["rubric"]
            following = await c.post(f"/api/v1/learning/goals/{goal_id}/next", json={})
            assert following.json()["state"] == "waiting_for_answer"
            goals = (await c.get("/api/v1/learning/goals")).json()
            assert [g["goal_id"] for g in goals] == [goal_id]
            assert (await c.get("/api/v1/learning/reviews")).json() == []
            exported = await c.get(f"/api/v1/learning/goals/{goal_id}/export")
            assert exported.status_code == 200
            assert "attachment" in exported.headers["content-disposition"]
            assert exported.json()["goal"]["title"] == "Fractions"

    async def test_goal_editor_save_contract(self, people, learning_on):
        a, _, org = people
        async with _client(a.id, org) as c:
            state = await _placed(c)
            goal_id = state["goal"]["goal_id"]
            saved = await c.patch(
                f"/api/v1/learning/goals/{goal_id}",
                json={
                    "explanation_language": "ta-IN",
                    "review_reminders": True,
                    "revision": 0,
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["revision"] == 1
            stale = await c.patch(
                f"/api/v1/learning/goals/{goal_id}",
                json={"title": "Decimals", "revision": 0},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "conflict"
            assert stale.json()["detail"]["stored"]["explanation_language"] == "ta-IN"

    async def test_sensitive_needs_a_yes(self, people, learning_on):
        a, _, org = people
        async with _client(a.id, org) as c:
            await c.put(
                "/api/v1/learning/profile",
                json={"adult_confirmed": True, "revision": 0},
            )
            asked = await c.post(
                "/api/v1/learning/goals", json={"title": "Coping with anxiety"}
            )
            assert asked.status_code == 409
            assert asked.json()["detail"]["code"] == "confirm_sensitive"
            assert asked.json()["detail"]["categories"] == ["mental health"]
            assert (await c.get("/api/v1/learning/goals")).json() == []
            yes = await c.post(
                "/api/v1/learning/goals",
                json={"title": "Coping with anxiety", "confirm_sensitive": True},
            )
            assert yes.status_code == 201

    async def test_needs_setup_without_a_model(self, people, learning_on, monkeypatch):
        from api.services.agent_builder import settings

        a, _, org = people
        monkeypatch.setattr(constants, "LEARNING_TEACHER", "model")

        async def no_model(*args, **kwargs):
            raise settings.BuilderUnavailable("no key")

        monkeypatch.setattr(settings, "resolve_for_organization", no_model)
        async with _client(a.id, org) as c:
            status = (await c.get("/api/v1/learning/status")).json()
            assert status["state"] == "needs_setup" and status["reason"]
            await c.put(
                "/api/v1/learning/profile",
                json={"adult_confirmed": True, "revision": 0},
            )
            refused = await c.post("/api/v1/learning/goals", json={"title": "Spanish"})
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "needs_setup"

    async def test_quota_is_a_429_with_the_sentence(
        self, people, learning_on, monkeypatch
    ):
        from api.services import quotas

        a, _, org = people
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        monkeypatch.setattr(quotas, "base_limit", lambda kind: 1)
        async with _client(a.id, org) as c:
            await c.put(
                "/api/v1/learning/profile",
                json={"adult_confirmed": True, "revision": 0},
            )
            first = await c.post("/api/v1/learning/goals", json={"title": "Go"})
            assert first.status_code == 201
            goal_id = first.json()["goal"]["goal_id"]
            refused = await c.post(
                f"/api/v1/learning/goals/{goal_id}/baseline", json={"answer": "hi"}
            )
        assert refused.status_code == 429
        assert "resets" in refused.json()["detail"]["message"]


@pytest.mark.asyncio
class TestPrivate:
    async def test_another_persons_goal_is_a_404(self, people, learning_on):
        a, b, org = people
        async with _client(a.id, org) as c:
            state = await _placed(c)
        goal_id = state["goal"]["goal_id"]
        async with _client(b.id, org) as c:
            for method, path, body in (
                ("get", f"/api/v1/learning/goals/{goal_id}/session", None),
                ("get", f"/api/v1/learning/goals/{goal_id}/progress", None),
                ("get", f"/api/v1/learning/goals/{goal_id}/export", None),
                ("post", f"/api/v1/learning/goals/{goal_id}/next", {}),
                ("post", f"/api/v1/learning/goals/{goal_id}/deletion", None),
                ("patch", f"/api/v1/learning/goals/{goal_id}", {"revision": 0}),
            ):
                response = await getattr(c, method)(
                    path, **({"json": body} if body is not None else {})
                )
                assert response.status_code == 404, (method, path)
            assert (await c.get("/api/v1/learning/goals")).json() == []

    async def test_no_route_takes_a_user_id(self, people, learning_on):
        a, b, org = people
        async with _client(a.id, org) as c:
            sneaky = await c.put(
                "/api/v1/learning/profile",
                json={"adult_confirmed": True, "revision": 0, "user_id": b.id},
            )
        assert sneaky.status_code == 422


@pytest.mark.asyncio
class TestDeletion:
    async def test_delete_is_a_card_then_settle_runs_it(self, people, learning_on):
        from unittest.mock import AsyncMock, patch

        from api.services.workflow import actions

        a, _, org = people
        async with _client(a.id, org) as c:
            state = await _placed(c)
            goal_id = state["goal"]["goal_id"]
            card = await c.post(f"/api/v1/learning/goals/{goal_id}/deletion")
            assert card.status_code == 200, card.text
            card = card.json()
            assert card["payload"]["state"] == "proposed"
            # Still there: a card is a question, not a delete.
            assert (
                await c.get(f"/api/v1/learning/goals/{goal_id}/session")
            ).status_code == 200
            with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
                settled = await c.post(
                    "/api/v1/timeline/actions/settle",
                    json={
                        "event_id": card["event_id"],
                        "verb": "confirm",
                        "version": card["payload"].get("version"),
                    },
                )
            assert settled.status_code == 200, settled.text
            await actions.run(card["event_id"], org)
            gone = await c.get(f"/api/v1/learning/goals/{goal_id}/session")
            assert gone.status_code == 404

    async def test_a_plain_member_deletes_their_own_goal_with_private_threads_on(
        self, people, learning_on, monkeypatch
    ):
        """A goal started on the learning page has no thread, so its card sat
        on the authorless thread: an Admin's to see and answer, never a plain
        member's. The learner was told "That proposal is not here" on their
        own delete, while the workspace owner could see the card and arm it."""
        from unittest.mock import AsyncMock, patch

        from api.enums import OrganizationRole
        from api.services.workflow import actions

        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        owner, member, org = people
        await db_client.add_user_to_organization(
            owner.id, org, OrganizationRole.OWNER.value
        )
        await db_client.add_user_to_organization(
            member.id, org, OrganizationRole.MEMBER.value
        )
        async with _client(member.id, org) as c:
            state = await _placed(c)
            goal_id = state["goal"]["goal_id"]
            card = (await c.post(f"/api/v1/learning/goals/{goal_id}/deletion")).json()
        body = {
            "event_id": card["event_id"],
            "verb": "confirm",
            "version": card["payload"].get("version"),
        }
        with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            # The owner cannot see a colleague's learning card, let alone arm it.
            async with _client(owner.id, org) as c:
                theirs = await c.post("/api/v1/timeline/actions/settle", json=body)
            assert theirs.status_code == 409, theirs.text
            assert "not here" in theirs.text
            async with _client(member.id, org) as c:
                settled = await c.post("/api/v1/timeline/actions/settle", json=body)
        assert settled.status_code == 200, settled.text
        await actions.run(card["event_id"], org)
        async with _client(member.id, org) as c:
            gone = await c.get(f"/api/v1/learning/goals/{goal_id}/session")
        assert gone.status_code == 404

    async def test_a_plain_members_card_is_in_their_today_and_nobody_elses(
        self, people, learning_on, monkeypatch
    ):
        """Today listed a pending card by its thread: the authorless one is
        an Admin's, so a member's own delete card was missing from their
        approvals (and a 404 by id) while the owner's own showed."""
        from api.enums import OrganizationRole

        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        monkeypatch.setattr(constants, "TODAY_LIST_ENABLED", True)
        owner, member, org = people
        await db_client.add_user_to_organization(
            owner.id, org, OrganizationRole.OWNER.value
        )
        await db_client.add_user_to_organization(
            member.id, org, OrganizationRole.MEMBER.value
        )
        async with _client(member.id, org) as c:
            goal_id = (await _placed(c))["goal"]["goal_id"]
            card = (await c.post(f"/api/v1/learning/goals/{goal_id}/deletion")).json()
            listed = (await c.get("/api/v1/today/approvals")).json()
            one = await c.get(f"/api/v1/today/approvals/{card['event_id']}")
        assert card["event_id"] in [i["id"] for i in listed["items"]]
        assert one.status_code == 200, one.text
        async with _client(owner.id, org) as c:
            theirs = (await c.get("/api/v1/today/approvals")).json()
            by_id = await c.get(f"/api/v1/today/approvals/{card['event_id']}")
        assert card["event_id"] not in [i["id"] for i in theirs["items"]]
        assert by_id.status_code == 404

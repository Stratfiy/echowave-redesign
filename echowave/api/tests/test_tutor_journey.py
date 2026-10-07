"""The tutor journey, end to end on the learning record (stream `learning`).

Found by using the Learning Guide as a person would: name a course, get
placed, get a plan, take lessons, get one wrong, be taught it again in a
smaller step, keep a streak, and find the day's lesson in Today. Each test
here failed on the code before its fix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services.learning import core, teacher

GOOD = "The key idea is this, and an example is that."
NOTHING = "No clue."


@pytest.fixture
def learning_on(monkeypatch):
    monkeypatch.setattr(constants, "LEARNING_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TODAY_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TEACHER", "fake")


@pytest.fixture
async def learner(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"tutor-a-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"tutor-org-{run}", a.id
    )
    yield a, org.id
    async with db_client.async_session() as session:
        for table in ("learning_goals", "learner_profiles", "agent_events"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org.id}
            )
        await session.commit()


async def _placed(org: int, user_id: int, title: str = "Python") -> dict:
    await core.save_profile(org, user_id, {"adult_confirmed": True}, revision=0)
    started = await core.start_goal(org, user_id, title=title)
    return await core.answer_baseline(
        org, user_id, started["goal"]["goal_id"], "I know a little."
    )


async def _answer(org, user_id, state, answer, now=None):
    return await core.submit_attempt(
        org,
        user_id,
        state["goal"]["goal_id"],
        exercise_id=state["exercise"]["exercise_id"],
        answer=answer,
        idempotency_key=uuid4().hex,
        now=now,
    )


@pytest.mark.asyncio
class TestASmallerStepTeachesWhatWasMissed:
    async def test_smaller_step_after_a_miss_is_the_same_skill(
        self, learner, learning_on
    ):
        # "Try a smaller step" sends only {easier: true}. It used to teach
        # the *next* skill in a smaller step and leave the missed one behind.
        a, org = learner
        state = await _placed(org, a.id)
        missed = state["lesson"]["skill_id"]
        await _answer(org, a.id, state, NOTHING)
        easier = await core.next_exercise(
            org, a.id, state["goal"]["goal_id"], easier=True
        )
        assert easier["lesson"]["skill_id"] == missed
        assert "smaller first step" in easier["lesson"]["explanation"]

    async def test_smaller_step_after_a_pass_moves_on(self, learner, learning_on):
        a, org = learner
        state = await _placed(org, a.id)
        await _answer(org, a.id, state, GOOD)
        easier = await core.next_exercise(
            org, a.id, state["goal"]["goal_id"], easier=True
        )
        assert easier["lesson"]["skill_id"] != state["lesson"]["skill_id"]


@pytest.mark.asyncio
class TestAPlanFromPlacement:
    async def test_placement_writes_a_plan_and_lessons_follow_it(
        self, learner, learning_on
    ):
        a, org = learner
        state = await _placed(org, a.id, title="Python")
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        plan = progress["plan"]
        assert len(plan) >= 3
        assert plan[0]["name"] == state["lesson"]["skill"]
        assert [p["status"] for p in plan] == [core.NOT_PRACTISED] * len(plan)
        await _answer(org, a.id, state, GOOD)
        following = await core.next_exercise(org, a.id, state["goal"]["goal_id"])
        assert following["lesson"]["skill"] == plan[1]["name"]
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        assert progress["plan"][0]["status"] == core.PRACTISED

    async def test_a_model_plan_that_is_not_a_list_is_no_plan(self, monkeypatch):
        model = teacher.ModelTeacher(1)

        async def ask(*_a, **_k):
            return {"level": "new", "feedback": "Fine.", "plan": "loops"}

        monkeypatch.setattr(model, "_ask", ask)
        placed = await model.place(teacher.GoalBrief("Python", "en-IN"), "Q", "A")
        assert placed.plan == []


@pytest.mark.asyncio
class TestDifficultyAdaptsToMisses:
    async def test_next_after_a_miss_reteaches_it_smaller(self, learner, learning_on):
        a, org = learner
        state = await _placed(org, a.id)
        await _answer(org, a.id, state, NOTHING)
        following = await core.next_exercise(org, a.id, state["goal"]["goal_id"])
        assert following["lesson"]["skill_id"] == state["lesson"]["skill_id"]
        assert "smaller first step" in following["lesson"]["explanation"]


@pytest.mark.asyncio
class TestStreakAndToday:
    async def test_streak_counts_days_with_marked_practice(self, learner, learning_on):
        a, org = learner
        state = await _placed(org, a.id)
        assert (await core.streak(org, a.id))["days"] == 0
        now = datetime.now(UTC)
        first = await _answer(org, a.id, state, NOTHING, now=now - timedelta(days=1))
        await _answer(org, a.id, first["session"], GOOD, now=now)
        got = await core.streak(org, a.id, now=now)
        assert got == {"days": 2, "practised_today": True}
        later = await core.streak(org, a.id, now=now + timedelta(days=1))
        assert later == {"days": 2, "practised_today": False}
        broken = await core.streak(org, a.id, now=now + timedelta(days=3))
        assert broken["days"] == 0

    async def test_today_has_the_days_lesson_before_any_practice(
        self, learner, learning_on
    ):
        a, org = learner
        state = await _placed(org, a.id, title="Python")
        today = await core.today(org, a.id)
        assert today["streak"]["days"] == 0
        [lesson] = today["lessons"]
        assert lesson["goal_id"] == state["goal"]["goal_id"]
        assert state["lesson"]["skill"] in lesson["text"]
        assert lesson["done_today"] is False

    async def test_today_is_empty_with_no_goal(self, learner, learning_on):
        a, org = learner
        assert (await core.today(org, a.id))["lessons"] == []


@pytest.mark.asyncio
class TestOverHttp:
    async def test_progress_has_the_plan_and_today_has_the_lesson(
        self, learner, learning_on
    ):
        from types import SimpleNamespace

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_user

        a, org = learner
        state = await _placed(org, a.id)
        goal_id = state["goal"]["goal_id"]
        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=a.id, selected_organization_id=org
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                got = await c.get(f"/api/v1/learning/goals/{goal_id}/progress")
                assert got.status_code == 200
                assert [p["name"] for p in got.json()["plan"]][0] == (
                    state["lesson"]["skill"]
                )
                today = await c.get("/api/v1/learning/today")
                assert today.status_code == 200, today.text
                body = today.json()
                assert body["streak"] == {"days": 0, "practised_today": False}
                assert body["lessons"][0]["goal_id"] == goal_id
        finally:
            app.dependency_overrides.pop(get_user, None)

    async def test_today_is_a_404_while_learning_today_is_off(
        self, learner, learning_on, monkeypatch
    ):
        from types import SimpleNamespace

        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_user

        monkeypatch.setattr(constants, "LEARNING_TODAY_ENABLED", False)
        a, org = learner
        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=a.id, selected_organization_id=org
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                assert (await c.get("/api/v1/learning/today")).status_code == 404
        finally:
            app.dependency_overrides.pop(get_user, None)

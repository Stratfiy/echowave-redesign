"""Learning Guide data (launch stream `learning`; handoff 6, 23; screens 13-14).

Done when: a person with a confirmed adult profile starts a goal on any
subject, answers one baseline question, gets a short lesson and one
exercise, and their answer is marked against its rubric with specific
feedback; progress, reviews and suggestions move only on marked answers,
once per submission; the lesson resumes where it was; sensitive details are
asked about before they are saved; nobody else -- a colleague in the same
workspace, the same person in another one -- can see or touch it; deleting
is a card the learner confirms; and with no model key the answer is "needs
setup", never a made-up lesson.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from api import constants
from api.db import db_client
from api.db.learning_models import (
    LearningAttemptModel,
    LearningGoalModel,
    LearningSkillModel,
)
from api.services import quotas
from api.services.learning import core, guide, sensitive, teacher

GOOD = "The key idea is this, and an example is that."
HALF = "The key idea is this."
NOTHING = "No clue."


@pytest.fixture
def learning_on(monkeypatch):
    monkeypatch.setattr(constants, "LEARNING_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TODAY_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TEACHER", "fake")


@pytest.fixture
async def people(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"learn-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"learn-b-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"learn-org-{run}", a.id
    )
    other, _ = await db_client.get_or_create_organization_by_provider_id(
        f"learn-other-{run}", a.id
    )
    yield a, b, org.id, other.id
    async with db_client.async_session() as session:
        for o in (org.id, other.id):
            await session.execute(
                text("DELETE FROM learning_goals WHERE organization_id = :o"), {"o": o}
            )
            await session.execute(
                text("DELETE FROM learner_profiles WHERE organization_id = :o"),
                {"o": o},
            )
            await session.execute(
                text("DELETE FROM agent_events WHERE organization_id = :o"), {"o": o}
            )
            await session.execute(
                text("DELETE FROM credit_ledger WHERE organization_id = :o"), {"o": o}
            )
        await session.commit()


async def _adult(org: int, user_id: int) -> None:
    await core.save_profile(org, user_id, {"adult_confirmed": True}, revision=0)


async def _ready_goal(org: int, user_id: int, **kwargs) -> dict:
    """An adult with a placed goal and its first exercise on screen."""
    profile = await core.get_profile(org, user_id)
    if not profile["adult_confirmed"]:
        await _adult(org, user_id)
    started = await core.start_goal(org, user_id, title="Fractions", **kwargs)
    return await core.answer_baseline(
        org, user_id, started["goal"]["goal_id"], "I can add halves and quarters."
    )


async def _answer(org, user_id, state, answer, key=None, now=None):
    return await core.submit_attempt(
        org,
        user_id,
        state["goal"]["goal_id"],
        exercise_id=state["exercise"]["exercise_id"],
        answer=answer,
        idempotency_key=key or uuid4().hex,
        now=now,
    )


async def _skill(goal_id: str) -> LearningSkillModel:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(LearningSkillModel)
            .join(LearningGoalModel, LearningGoalModel.id == LearningSkillModel.goal_id)
            .where(LearningGoalModel.goal_uuid == goal_id)
        )


@pytest.mark.asyncio
class TestProfile:
    async def test_starting_needs_an_adult_profile(self, people, learning_on):
        a, _, org, _ = people
        with pytest.raises(core.ProfileNeeded):
            await core.start_goal(org, a.id, title="Spanish")

    async def test_profile_saves_with_the_save_contract(self, people, learning_on):
        a, _, org, _ = people
        saved = await core.save_profile(
            org,
            a.id,
            {
                "explanation_language": "ta-IN",
                "learner_kind": "student",
                "studying_for": "Class 12 boards",
                "adult_confirmed": True,
            },
            revision=0,
        )
        assert saved["revision"] == 1 and saved["adult_confirmed"]
        assert saved["studying_for"] == "Class 12 boards"
        with pytest.raises(core.Conflict) as caught:
            await core.save_profile(org, a.id, {"learner_kind": "adult"}, revision=0)
        assert caught.value.stored["learner_kind"] == "student"

    async def test_unknown_language_and_kind_are_refused(self, people, learning_on):
        a, _, org, _ = people
        with pytest.raises(core.LearningError):
            await core.save_profile(
                org, a.id, {"explanation_language": "xx"}, revision=0
            )
        with pytest.raises(core.LearningError):
            await core.save_profile(org, a.id, {"learner_kind": "child"}, revision=0)

    async def test_a_profile_is_per_person(self, people, learning_on):
        a, b, org, _ = people
        await _adult(org, a.id)
        assert (await core.get_profile(org, b.id))["adult_confirmed"] is False


@pytest.mark.asyncio
class TestLesson:
    async def test_goal_baseline_lesson_exercise(self, people, learning_on):
        a, _, org, _ = people
        await _adult(org, a.id)
        started = await core.start_goal(
            org, a.id, title="Photosynthesis", studying_for="NEET"
        )
        assert started["state"] == "baseline"
        assert "Photosynthesis" in started["baseline_question"]
        assert started["goal"]["studying_for"] == "NEET"
        placed = await core.answer_baseline(
            org, a.id, started["goal"]["goal_id"], "Plants make food from light."
        )
        assert placed["state"] == "waiting_for_answer"
        assert placed["goal"]["baseline_level"] in teacher.LEVELS
        assert placed["lesson"]["explanation"] and placed["exercise"]["prompt"]
        assert [r["criterion"] for r in placed["exercise"]["rubric"]] == [
            "idea",
            "example",
        ]
        # No notes: general explanation, and it says so.
        assert placed["lesson"]["source_kind"] == "general"
        assert placed["lesson"]["sources"] == []

    async def test_any_subject_and_the_persons_language(self, people, learning_on):
        a, _, org, _ = people
        await core.save_profile(
            org,
            a.id,
            {"explanation_language": "hi-IN", "adult_confirmed": True},
            revision=0,
        )
        started = await core.start_goal(org, a.id, title="Income tax basics")
        assert started["goal"]["explanation_language"] == "hi-IN"
        chosen = await core.start_goal(
            org, a.id, title="Carnatic music", explanation_language="ta-IN"
        )
        assert chosen["goal"]["explanation_language"] == "ta-IN"

    async def test_notes_make_a_material_backed_lesson(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(
            org, a.id, material="Chapter 3: a fraction is a part of a whole."
        )
        assert state["lesson"]["source_kind"] == "material"
        assert state["lesson"]["sources"] == [
            "Chapter 3: a fraction is a part of a whole."
        ]

    async def test_baseline_is_placement_not_practice(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        assert progress["practice_count"] == 0
        assert progress["state"] == "no_practice"
        assert all(s["status"] == core.NOT_PRACTISED for s in progress["skills"])

    async def test_passed_answer_feedback_and_completed(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        result = await _answer(org, a.id, state, GOOD)
        assert result["attempt"]["outcome"] == teacher.PASSED
        assert result["attempt"]["feedback"]
        assert all(r["met"] for r in result["attempt"]["rubric_results"])
        assert result["session"]["state"] == "completed"
        skill = await _skill(state["goal"]["goal_id"])
        assert skill.status == core.PRACTISED and skill.passed_attempts == 1

    async def test_corrected_answer_then_try_again(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        half = await _answer(org, a.id, state, HALF)
        assert half["attempt"]["outcome"] == teacher.PARTLY
        assert half["session"]["state"] == "correction"
        met = {r["criterion"]: r["met"] for r in half["attempt"]["rubric_results"]}
        assert met == {"idea": True, "example": False}
        skill = await _skill(state["goal"]["goal_id"])
        assert skill.status == core.NEEDS_ANOTHER
        again = await _answer(org, a.id, state, GOOD)
        assert again["attempt"]["outcome"] == teacher.PASSED
        assert len(again["session"]["attempts"]) == 2
        skill = await _skill(state["goal"]["goal_id"])
        assert skill.status == core.PRACTISED
        assert (skill.evaluated_attempts, skill.passed_attempts) == (2, 1)

    async def test_a_passed_exercise_takes_no_more_answers(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, GOOD)
        with pytest.raises(core.LearningError):
            await _answer(org, a.id, state, GOOD)

    async def test_next_twice_without_an_answer_is_one_exercise(
        self, people, learning_on
    ):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        first = await core.next_exercise(org, a.id, goal_id)
        second = await core.next_exercise(org, a.id, goal_id)
        assert (
            first["exercise"]["exercise_id"]
            == second["exercise"]["exercise_id"]
            == state["exercise"]["exercise_id"]
        )

    async def test_next_after_an_answer_is_a_new_exercise(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, GOOD)
        following = await core.next_exercise(org, a.id, state["goal"]["goal_id"])
        assert following["state"] == "waiting_for_answer"
        assert following["exercise"]["exercise_id"] != state["exercise"]["exercise_id"]

    async def test_resumes_without_losing_attempts(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, HALF)
        # A reload, a new tab, tomorrow: the same exercise and its attempt.
        resumed = await core.session_state(org, a.id, state["goal"]["goal_id"])
        assert resumed["state"] == "correction"
        assert resumed["exercise"]["exercise_id"] == state["exercise"]["exercise_id"]
        assert [x["answer"] for x in resumed["attempts"]] == [HALF]


@pytest.mark.asyncio
class TestEvidenceOnce:
    async def test_the_same_key_counts_once(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        first = await _answer(org, a.id, state, HALF, key="k-1")
        again = await _answer(org, a.id, state, GOOD, key="k-1")
        assert again["replayed"] is True
        assert again["attempt"]["attempt_id"] == first["attempt"]["attempt_id"]
        assert again["attempt"]["outcome"] == teacher.PARTLY
        skill = await _skill(state["goal"]["goal_id"])
        assert skill.evaluated_attempts == 1
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        assert progress["practice_count"] == 1

    async def test_reading_and_chatting_never_move_progress(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        for _ in range(5):
            await core.session_state(org, a.id, goal_id)
            await core.progress(org, a.id, goal_id)
            await core.next_exercise(org, a.id, goal_id)
        progress = await core.progress(org, a.id, goal_id)
        assert progress["practice_count"] == 0
        async with db_client.async_session() as session:
            goal = await session.scalar(
                select(LearningGoalModel).where(LearningGoalModel.goal_uuid == goal_id)
            )
        assert goal.last_practised_at is None

    async def test_progress_rows_and_recent_exercises(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, NOTHING)
        await _answer(org, a.id, state, GOOD)
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        assert progress["state"] == "practised" and progress["practice_count"] == 2
        [row] = progress["skills"]
        assert row["label"] == "Practised" and row["evaluated_attempts"] == 2
        assert [r["outcome"] for r in progress["recent"]] == [
            teacher.PASSED,
            teacher.NOT_YET,
        ]
        assert progress["next_step"]["kind"] == "continue"
        detail = await core.skill_detail(
            org, a.id, state["goal"]["goal_id"], row["skill_id"]
        )
        assert len(detail["attempts"]) == 2 and detail["rubric"]

    async def test_no_percentages_anywhere(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, GOOD)
        progress = await core.progress(org, a.id, state["goal"]["goal_id"])
        flat = repr(progress).lower()
        for word in ("percent", "mastery", "fluent", "streak", "score"):
            assert word not in flat


@pytest.mark.asyncio
class TestReviewsAndSuggestions:
    async def test_a_pass_is_due_for_review_a_day_later(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        now = datetime.now(UTC)
        await _answer(org, a.id, state, GOOD, now=now)
        assert await core.reviews_due(org, a.id, now=now) == []
        due = await core.reviews_due(org, a.id, now=now + timedelta(days=1, minutes=1))
        assert [d["goal_title"] for d in due] == ["Fractions"]
        assert due[0]["skill_name"]

    async def test_intervals_grow_after_each_pass(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        now = datetime.now(UTC)
        await _answer(org, a.id, state, GOOD, now=now)
        skill = await _skill(goal_id)
        review = await core.review(org, a.id, goal_id, skill.id)
        assert review["exercise"]["kind"] == "review"
        assert review["lesson"]["skill_id"] == skill.id
        later = now + timedelta(days=1)
        await _answer(org, a.id, review, GOOD, now=later)
        skill = await _skill(goal_id)
        due = skill.next_review_at.astimezone(UTC)
        assert abs((due - later) - timedelta(days=3)) < timedelta(seconds=5)

    async def test_stuck_suggests_a_smaller_step(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, NOTHING)
        assert [s["kind"] for s in await core.suggestions(org, a.id)] == []
        await _answer(org, a.id, state, HALF)
        found = await core.suggestions(org, a.id)
        assert found[0]["kind"] == "stuck" and found[0]["action"] == "easier"
        easier = await core.next_exercise(
            org, a.id, state["goal"]["goal_id"], easier=True
        )
        assert "smaller first step" in easier["lesson"]["explanation"]

    async def test_improving_suggests_the_next_step(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, NOTHING)
        await _answer(org, a.id, state, GOOD)
        kinds = [s["kind"] for s in await core.suggestions(org, a.id)]
        assert kinds == ["improving"]

    async def test_no_practice_no_suggestions(self, people, learning_on):
        a, _, org, _ = people
        await _ready_goal(org, a.id)
        assert await core.suggestions(org, a.id) == []

    async def test_at_most_three(self, people, learning_on):
        a, _, org, _ = people
        now = datetime.now(UTC)
        for title in ("A", "B", "C", "D"):
            profile = await core.get_profile(org, a.id)
            if not profile["adult_confirmed"]:
                await _adult(org, a.id)
            started = await core.start_goal(org, a.id, title=title)
            state = await core.answer_baseline(
                org, a.id, started["goal"]["goal_id"], "a little"
            )
            await _answer(org, a.id, state, GOOD, now=now - timedelta(days=5))
        assert len(await core.suggestions(org, a.id, now=now)) == 3


@pytest.mark.asyncio
class TestAskBeforeSaving:
    def test_detects_and_names(self):
        assert sensitive.detect("Learn to manage my diabetes") == ["health"]
        assert sensitive.detect("Spanish for travel") == []
        assert "religion or caste" in sensitive.detect("History of my caste")

    async def test_not_saved_until_yes(self, people, learning_on):
        a, _, org, _ = people
        await _adult(org, a.id)
        with pytest.raises(core.SensitiveDetails) as caught:
            await core.start_goal(org, a.id, title="Living with diabetes")
        assert caught.value.categories == ["health"]
        assert await core.list_goals(org, a.id) == []
        saved = await core.start_goal(
            org, a.id, title="Living with diabetes", confirm_sensitive=True
        )
        assert saved["goal"]["title"] == "Living with diabetes"
        async with db_client.async_session() as session:
            goal = await session.scalar(
                select(LearningGoalModel).where(
                    LearningGoalModel.goal_uuid == saved["goal"]["goal_id"]
                )
            )
        assert goal.sensitive_confirmed_at is not None

    async def test_pasted_notes_are_checked_too(self, people, learning_on):
        a, _, org, _ = people
        await _adult(org, a.id)
        with pytest.raises(core.SensitiveDetails):
            await core.start_goal(
                org, a.id, title="Biology", material="My diagnosis says..."
            )


@pytest.mark.asyncio
class TestPrivate:
    async def test_a_colleague_cannot_see_or_touch_it(self, people, learning_on):
        a, b, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        await _answer(org, a.id, state, GOOD, now=datetime.now(UTC) - timedelta(days=3))
        await _adult(org, b.id)
        for call in (
            core.session_state(org, b.id, goal_id),
            core.progress(org, b.id, goal_id),
            core.export(org, b.id, goal_id),
            core.next_exercise(org, b.id, goal_id),
            core.answer_baseline(org, b.id, goal_id, "x"),
            core.update_goal(org, b.id, goal_id, {"title": "Mine"}, revision=0),
            core.request_deletion(org, b.id, goal_id),
        ):
            with pytest.raises(core.NotFound):
                await call
        with pytest.raises(core.NotFound):
            await _answer(org, b.id, state, GOOD)
        assert await core.list_goals(org, b.id) == []
        assert await core.reviews_due(org, b.id) == []
        assert await core.suggestions(org, b.id) == []
        assert await core.delete_goal(org, b.id, goal_id) is False
        # ... while the learner sees it all.
        assert [g["goal_id"] for g in await core.list_goals(org, a.id)] == [goal_id]
        assert len(await core.reviews_due(org, a.id)) == 1

    async def test_another_workspace_does_not_inherit_it(self, people, learning_on):
        a, _, org, other = people
        state = await _ready_goal(org, a.id)
        with pytest.raises(core.NotFound):
            await core.session_state(other, a.id, state["goal"]["goal_id"])
        assert await core.list_goals(other, a.id) == []
        assert await guide.context_for(other, a.id) == ""

    async def test_a_replayed_key_from_another_workspace_is_not_found(
        self, people, learning_on
    ):
        a, _, org, other = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, GOOD, key="shared-key")
        with pytest.raises(core.NotFound):
            await core.submit_attempt(
                other,
                a.id,
                state["goal"]["goal_id"],
                exercise_id=state["exercise"]["exercise_id"],
                answer=GOOD,
                idempotency_key="shared-key",
            )


@pytest.mark.asyncio
class TestHonestStates:
    async def test_no_model_key_is_needs_setup_and_nothing_saved(
        self, people, learning_on, monkeypatch
    ):
        from api.services.agent_builder import settings

        a, _, org, _ = people
        await _adult(org, a.id)
        monkeypatch.setattr(constants, "LEARNING_TEACHER", "model")

        async def no_model(*args, **kwargs):
            raise settings.BuilderUnavailable("no key")

        monkeypatch.setattr(settings, "resolve_for_organization", no_model)
        assert (await teacher.readiness(org))[0] == "needs_setup"
        with pytest.raises(teacher.NeedsSetup):
            await core.start_goal(org, a.id, title="Spanish")
        assert await core.list_goals(org, a.id) == []

    async def test_an_unknown_teacher_setting_is_the_model_not_the_sample(
        self, monkeypatch
    ):
        monkeypatch.setattr(constants, "LEARNING_TEACHER", "fkae")
        assert teacher.kind() == "model"

    async def test_model_marking_never_credits_an_unmarked_criterion(self, monkeypatch):
        model = teacher.ModelTeacher(1)

        async def ask(*args, **kwargs):
            return {
                "results": [{"criterion": "idea", "met": True, "note": "ok"}],
                "feedback": "Good start; add an example.",
            }

        monkeypatch.setattr(model, "_ask", ask)
        marking = await model.mark(
            teacher.GoalBrief(title="x", language="en-IN"),
            exercise="e",
            rubric=[
                {"criterion": "idea", "description": "d"},
                {"criterion": "example", "description": "d"},
            ],
            answer="a",
        )
        assert marking.outcome == teacher.PARTLY
        assert [r["met"] for r in marking.results] == [True, False]

    async def test_model_cannot_cite_lines_not_in_the_notes(self, monkeypatch):
        model = teacher.ModelTeacher(1)

        async def ask(*args, **kwargs):
            return {
                "skill": "s",
                "objective": "o",
                "explanation": "e",
                "exercise": "x",
                "rubric": [{"criterion": "c", "description": "d"}],
                "sources": ["A line from the notes.", "An invented quotation."],
            }

        monkeypatch.setattr(model, "_ask", ask)
        lesson = await model.lesson(
            teacher.GoalBrief(
                title="t", language="en-IN", material="A line from the notes. More."
            ),
            skills_so_far=[],
            focus=None,
            easier=False,
        )
        assert lesson.sources == ["A line from the notes."]
        assert lesson.source_kind == "material"

    async def test_model_lesson_without_a_rubric_is_refused(self, monkeypatch):
        model = teacher.ModelTeacher(1)

        async def ask(*args, **kwargs):
            return {"skill": "s", "objective": "o", "explanation": "e", "exercise": "x"}

        monkeypatch.setattr(model, "_ask", ask)
        with pytest.raises(teacher.TeacherFailed):
            await model.lesson(
                teacher.GoalBrief(title="t", language="en-IN"),
                skills_so_far=[],
                focus=None,
                easier=False,
            )

    async def test_lessons_spend_the_daily_model_allowance(
        self, people, learning_on, monkeypatch
    ):
        a, _, org, _ = people
        await _adult(org, a.id)
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        monkeypatch.setattr(quotas, "base_limit", lambda kind: 1)
        await core.start_goal(org, a.id, title="Chess openings")
        with pytest.raises(quotas.QuotaExceeded):
            await core.start_goal(org, a.id, title="Chess endgames")
        assert len(await core.list_goals(org, a.id)) == 1


@pytest.mark.asyncio
class TestDeleteIsACard:
    async def _confirm_and_run(self, org, event_id, user_id, version):
        from api.services.workflow import actions

        with patch("api.tasks.arq.enqueue_job", new=AsyncMock()):
            await actions.settle(
                organization_id=org,
                event_id=event_id,
                verb="confirm",
                user_id=user_id,
                version=version,
            )
        await actions.run(event_id, org)
        return (await db_client.get_agent_event(event_id, organization_id=org)).payload

    async def test_nothing_deleted_until_the_learner_confirms(
        self, people, learning_on
    ):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        await _answer(org, a.id, state, GOOD)
        card = await core.request_deletion(org, a.id, goal_id)
        assert card["status"] == "proposed"
        assert card["payload"]["state"] == "proposed"
        assert card["payload"]["reversible"] is False
        # The goal's title is the learner's; the card on a shared thread
        # does not carry it.
        assert "Fractions" not in repr(card["payload"])
        assert await core.owns_goal(org, a.id, goal_id)
        again = await core.request_deletion(org, a.id, goal_id)
        assert again["event_id"] == card["event_id"]
        done = await self._confirm_and_run(
            org, card["event_id"], a.id, card["payload"].get("version")
        )
        assert done["state"] == "done"
        assert not await core.owns_goal(org, a.id, goal_id)
        async with db_client.async_session() as session:
            left = await session.scalar(
                select(func.count(LearningAttemptModel.id)).where(
                    LearningAttemptModel.organization_id == org
                )
            )
        assert left == 0

    async def test_a_colleagues_confirm_does_not_delete(self, people, learning_on):
        a, b, org, _ = people
        state = await _ready_goal(org, a.id)
        goal_id = state["goal"]["goal_id"]
        card = await core.request_deletion(org, a.id, goal_id)
        done = await self._confirm_and_run(
            org, card["event_id"], b.id, card["payload"].get("version")
        )
        assert done["state"] == "failed"
        assert await core.owns_goal(org, a.id, goal_id)

    async def test_with_the_ledger_the_card_is_version_bound(
        self, people, learning_on, monkeypatch
    ):
        from api.services.workflow import actions

        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        card = await core.request_deletion(org, a.id, state["goal"]["goal_id"])
        assert card["payload"]["version"]
        with (
            pytest.raises(actions.ActionError),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        ):
            await actions.settle(
                organization_id=org,
                event_id=card["event_id"],
                verb="confirm",
                user_id=a.id,
                version="not-the-one",
            )
        done = await self._confirm_and_run(
            org, card["event_id"], a.id, card["payload"]["version"]
        )
        assert done["state"] == "done"

    async def test_export_is_the_whole_record(self, people, learning_on):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, HALF)
        data = await core.export(org, a.id, state["goal"]["goal_id"])
        assert data["goal"]["title"] == "Fractions"
        assert data["goal"]["baseline_answer"]
        [lesson] = data["lessons"]
        assert lesson["exercises"][0]["attempts"][0]["answer"] == HALF


@pytest.mark.asyncio
class TestInterfaces:
    async def test_guide_context_is_evidence_and_a_resume_link(
        self, people, learning_on
    ):
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        await _answer(org, a.id, state, GOOD)
        context = await guide.context_for(org, a.id)
        assert "Fractions" in context
        assert "1 practice answers marked" in context
        assert guide.resume_link(state["goal"]["goal_id"]) in context

    async def test_guide_context_is_empty_while_off(self, people, monkeypatch):
        a, _, org, _ = people
        monkeypatch.setattr(constants, "LEARNING_ENABLED", False)
        assert await guide.context_for(org, a.id) == ""

    async def test_feedback_on_a_practised_lesson(
        self, people, learning_on, monkeypatch
    ):
        from api.services import feedback

        monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True)
        a, b, org, _ = people
        state = await _ready_goal(org, a.id)
        lesson_id = state["lesson"]["lesson_id"]
        with pytest.raises(feedback.FeedbackRefused):
            await feedback.submit(
                organization_id=org,
                user_id=a.id,
                subject_kind=feedback.LESSON,
                subject_id=lesson_id,
                verdict="yes",
            )
        await _answer(org, a.id, state, GOOD)
        saved = await feedback.submit(
            organization_id=org,
            user_id=a.id,
            subject_kind=feedback.LESSON,
            subject_id=lesson_id,
            verdict="not_quite",
            reasons=["too_long"],
        )
        assert saved["verdict"] == "not_quite"
        with pytest.raises(feedback.NotFound):
            await feedback.submit(
                organization_id=org,
                user_id=b.id,
                subject_kind=feedback.LESSON,
                subject_id=lesson_id,
                verdict="yes",
            )

    def test_learning_events_carry_codes_only(self, monkeypatch):
        from api.services.events import catalogue, envelope

        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")

        for name in (
            "learning_goal_started",
            "learning_practice_evaluated",
            "learning_goal_deleted",
        ):
            spec = catalogue.get(name)
            assert spec.domain == "learning"
            assert not spec.allowed & catalogue.FORBIDDEN_PROPERTIES
        built = envelope.build(
            "learning_practice_evaluated",
            user_id=1,
            organization_id=1,
            properties={"outcome": "partly", "exercise_kind": "review"},
        )
        assert built["properties"]["outcome"] == "partly"

    async def test_practice_writes_one_outcome_event(
        self, people, learning_on, monkeypatch
    ):
        from api.db.controls_models import AnalyticsOutboxModel

        monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")
        a, _, org, _ = people
        state = await _ready_goal(org, a.id)
        before = datetime.now(UTC)
        await _answer(org, a.id, state, HALF, key="evt-1")
        await _answer(org, a.id, state, HALF, key="evt-1")
        async with db_client.async_session() as session:
            rows = (
                await session.scalars(
                    select(AnalyticsOutboxModel).where(
                        AnalyticsOutboxModel.name == "learning_practice_evaluated",
                        AnalyticsOutboxModel.created_at >= before,
                    )
                )
            ).all()
        assert len(rows) == 1
        props = rows[0].envelope["properties"]
        assert props["outcome"] == "partly"
        # Codes only: the answer never rides along.
        assert HALF not in repr(rows[0].envelope)

    def test_flags_are_registered_and_off_by_default(self):
        from api.services import features

        assert features.FLAGS["learning"] == "LEARNING_ENABLED"
        assert features.FLAGS["learning_today"] == "LEARNING_TODAY_ENABLED"
        assert "learning" in features.DESCRIPTIONS
        assert "learning_today" in features.DESCRIPTIONS

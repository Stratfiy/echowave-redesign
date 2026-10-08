"""A course started from Chat (the tutor, built by describing it).

Found by asking for a tutor in Chat the way a person would: the Learning
Guide could explain and quiz in prose, but nothing it did reached the
learning record, so there was no plan, no marked quiz, no streak and nothing
in Today -- and "Build me a tutor" built a chat agent that could keep none
of it either. ``start_course`` starts the record from the conversation and
puts the lesson on the thread, where the person already is.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel
from api.services.helpers import catalogue
from api.services.helpers import tools as helper_tools
from api.services.learning import core


@pytest.fixture
def learning_on(monkeypatch):
    monkeypatch.setattr(constants, "LEARNING_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TODAY_ENABLED", True)
    monkeypatch.setattr(constants, "LEARNING_TEACHER", "fake")


@pytest.fixture
async def learner(test_engine):
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"course-a-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"course-org-{run}", a.id
    )
    yield a, org.id
    async with db_client.async_session() as session:
        for table in ("learning_goals", "learner_profiles", "agent_events"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org.id}
            )
        await session.commit()


async def _course_rows(org: int) -> list[dict]:
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(AgentEventModel).where(AgentEventModel.organization_id == org)
            )
        ).all()
    return [
        r.payload["learning_course"]
        for r in rows
        if "learning_course" in (r.payload or {})
    ]


async def _run(org, user_id, arguments, thread="t-course"):
    return await helper_tools.run(
        catalogue.START_COURSE,
        organization_id=org,
        arguments=arguments,
        author_id=user_id,
        thread_id=thread,
        helper=catalogue.LEARNING_GUIDE,
    )


def test_the_learning_guide_and_the_builder_can_start_a_course():
    assert catalogue.START_COURSE in catalogue.BY_KEY[catalogue.LEARNING_GUIDE].tools
    assert catalogue.START_COURSE in catalogue.BUILDER_HELPER.tools
    assert "start_course" in catalogue.BUILDER_HELPER.instructions


@pytest.mark.asyncio
class TestStartCourse:
    async def test_starts_the_record_and_puts_the_lesson_on_the_thread(
        self, learner, learning_on
    ):
        a, org = learner
        await core.save_profile(org, a.id, {"adult_confirmed": True}, revision=0)
        got = await _run(
            org,
            a.id,
            {"title": "Python basics", "studying_for": "a developer job"},
        )
        assert got["status"] == "started"
        assert got["baseline_question"]
        [goal] = await core.list_goals(org, a.id)
        assert goal["title"] == "Python basics"
        assert goal["thread_id"] == "t-course"
        [card] = await _course_rows(org)
        assert card == {
            "goal_id": goal["goal_id"],
            "title": "Python basics",
            "href": f"/overview?learn={goal['goal_id']}",
        }

    async def test_before_the_adult_confirm_the_card_opens_the_start(
        self, learner, learning_on
    ):
        a, org = learner
        got = await _run(org, a.id, {"title": "Spoken English"})
        assert got["status"] == "needs_confirmation"
        assert await core.list_goals(org, a.id) == []
        [card] = await _course_rows(org)
        assert card["goal_id"] is None
        assert card["title"] == "Spoken English"
        assert card["href"] == "/overview?learn=new&topic=Spoken+English"

    async def test_off_it_is_not_offered(self, learner, monkeypatch):
        monkeypatch.setattr(constants, "LEARNING_ENABLED", False)
        assert catalogue.START_COURSE not in helper_tools.enabled_names(1)
        a, org = learner
        got = await _run(org, a.id, {"title": "Python"})
        assert got["status"] == "unavailable"


@pytest.mark.asyncio
class TestResumeFromChat:
    async def test_the_same_course_again_resumes_it_with_the_card(
        self, learner, learning_on
    ):
        # The guide used to paste "Resume: /overview?learn=..." into its
        # reply, which Chat shows as plain text: nothing to tap.
        a, org = learner
        await core.save_profile(org, a.id, {"adult_confirmed": True}, revision=0)
        first = await _run(org, a.id, {"title": "Python basics"})
        again = await _run(org, a.id, {"title": "python BASICS"}, thread="t-later")
        assert again["status"] == "resumed"
        assert again["goal_id"] == first["goal_id"]
        assert len(await core.list_goals(org, a.id)) == 1
        cards = await _course_rows(org)
        assert [c["goal_id"] for c in cards] == [first["goal_id"]] * 2

    def test_the_guide_is_told_to_resume_with_the_tool_not_a_link(self):
        words = catalogue.BY_KEY[catalogue.LEARNING_GUIDE].instructions
        assert "give its Resume link" not in words
        assert "start_course" in words

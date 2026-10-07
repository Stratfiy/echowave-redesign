"""Learning Guide data: goals, lessons, evaluated practice and progress.

Handoff 6 ("Learning Guide data"), 23 ("Learning and personal guidance")
and screens 13-14. The rules this module keeps:

* **The learner's own.** Every read and write names the organisation and
  the person. A goal is found by its uuid *and* its owner; anyone else --
  a colleague, an admin of the same workspace -- gets "not found", the same
  answer as for a goal that does not exist.
* **Progress is evaluated practice.** A skill's row moves only when an
  attempt is marked (``submit_attempt``). Reading a lesson, chatting with
  Decibyl or time spent never moves it, and there is no percentage: a skill
  is "Not practised", "Practised" or "Needs another attempt", with the
  attempts that show it.
* **Once.** A submission carries the client's idempotency key; a retry of
  the same key returns the first marking and changes nothing.
* **Resumable.** The open exercise and every attempt are rows; a session
  reopens on the last exercise that was not passed, or on the next step.
* **Honest.** No teacher, no lesson: ``teacher.NeedsSetup`` is a state the
  screen shows. A lesson says whether it came from the person's notes or is
  general explanation.

Model work spends the person's ``model_turns`` allowance when operational
quotas are on (services/quotas.py), one per lesson, placement or marking.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.db.learning_models import (
    LearnerProfileModel,
    LearningAttemptModel,
    LearningExerciseModel,
    LearningGoalModel,
    LearningLessonModel,
    LearningSkillModel,
)
from api.services import features, quotas
from api.services.learning import sensitive, teacher

FLAG = "learning"
TODAY_FLAG = "learning_today"

#: Days to the next review after each pass in a row: the first after a day,
#: then three, a week, two weeks, a month, two months.
REVIEW_INTERVALS = (1, 3, 7, 14, 30, 60)
#: A skill missed this many times in a row is "stuck": the suggestion is a
#: smaller step, not the same exercise again.
STUCK_AFTER = 2
MAX_SUGGESTIONS = 3

NOT_PRACTISED = "not_practised"
PRACTISED = "practised"
NEEDS_ANOTHER = "needs_another_attempt"
SKILL_LABELS = {
    NOT_PRACTISED: "Not practised yet",
    PRACTISED: "Practised",
    NEEDS_ANOTHER: "Needs another attempt",
}

LEARNER_KINDS = ("adult", "student", "course_learner")
MAX_TITLE = 200
MAX_STUDYING_FOR = 160
MAX_ANSWER = 8_000


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def today_enabled(organization_id: int | None = None) -> bool:
    return enabled(organization_id) and features.is_on(TODAY_FLAG, organization_id)


# --- errors -----------------------------------------------------------------


class LearningError(ValueError):
    """Refused, with a line the person can read."""


class NotFound(LookupError):
    pass


@dataclass
class Conflict(Exception):
    """A save named an older revision than the stored one."""

    stored: dict[str, Any]


@dataclass
class SensitiveDetails(Exception):
    """Details that look sensitive; saved only after the person says yes."""

    categories: list[str]


class ProfileNeeded(LearningError):
    pass


# --- small helpers ----------------------------------------------------------


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _clean(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


async def _spend_turn(user_id: int) -> None:
    """One model turn from the person's allowance; raises QuotaExceeded."""
    await quotas.consume(user_id, quotas.MODEL_TURNS)


async def _emit(name: str, organization_id: int, user_id: int, **properties) -> None:
    from api.services import events

    await events.emit(
        name, user_id=user_id, organization_id=organization_id, properties=properties
    )


def _language_ok(language: str) -> str:
    from api.services import member_preferences

    if language not in member_preferences.LANGUAGES:
        raise LearningError("That language is not offered yet.")
    return language


async def _default_language(user_id: int) -> str:
    from api.services import member_preferences

    try:
        prefs = await member_preferences.get(user_id)
    except Exception:  # noqa: BLE001 - a missing preference is not an error
        prefs = {}
    return prefs.get("language") or "en-IN"


# --- learner profile --------------------------------------------------------


def _profile_dict(row: LearnerProfileModel | None) -> dict[str, Any]:
    if row is None:
        return {
            "explanation_language": None,
            "learner_kind": "adult",
            "studying_for": None,
            "adult_confirmed": False,
            "revision": 0,
            "updated_at": None,
        }
    return {
        "explanation_language": row.explanation_language,
        "learner_kind": row.learner_kind,
        "studying_for": row.studying_for,
        "adult_confirmed": row.adult_confirmed_at is not None,
        "revision": row.revision,
        "updated_at": _iso(row.updated_at),
    }


async def _profile_row(session, organization_id: int, user_id: int):
    return await session.scalar(
        select(LearnerProfileModel).where(
            LearnerProfileModel.organization_id == organization_id,
            LearnerProfileModel.user_id == user_id,
        )
    )


async def get_profile(organization_id: int, user_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await _profile_row(session, organization_id, user_id)
    out = _profile_dict(row)
    if out["explanation_language"] is None:
        out["suggested_language"] = await _default_language(user_id)
    return out


async def save_profile(
    organization_id: int,
    user_id: int,
    changes: dict[str, Any],
    *,
    revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Only the fields sent change. ``adult_confirmed`` can be set, never
    unset: the record of when someone said so stays."""
    now = _now(now)
    values: dict[str, Any] = {}
    if "explanation_language" in changes:
        lang = changes["explanation_language"]
        values["explanation_language"] = _language_ok(lang) if lang else None
    if "learner_kind" in changes:
        kind = str(changes["learner_kind"] or "")
        if kind not in LEARNER_KINDS:
            raise LearningError("Choose adult learner, student or course learner.")
        values["learner_kind"] = kind
    if "studying_for" in changes:
        values["studying_for"] = _clean(changes["studying_for"], MAX_STUDYING_FOR)
    if changes.get("adult_confirmed") is True:
        values["adult_confirmed_at"] = now
    elif changes.get("adult_confirmed") is False:
        raise LearningError("Learning is for adults for now.")
    async with db_client.async_session() as session:
        row = await _profile_row(session, organization_id, user_id)
        stored = row.revision if row is not None else 0
        if revision != stored:
            raise Conflict(stored=_profile_dict(row))
        if row is None:
            row = LearnerProfileModel(
                organization_id=organization_id,
                user_id=user_id,
                learner_kind="adult",
                revision=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        for key, value in values.items():
            if key == "adult_confirmed_at" and row.adult_confirmed_at is not None:
                continue
            setattr(row, key, value)
        row.revision = stored + 1
        row.updated_at = now
        saved = _profile_dict(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            # Two first saves at once: one wins, the other is a conflict.
            await session.rollback()
            async with db_client.async_session() as again:
                current = await _profile_row(again, organization_id, user_id)
            raise Conflict(stored=_profile_dict(current)) from exc
        return saved


# --- goals ------------------------------------------------------------------


async def _goal(session, organization_id: int, user_id: int, goal_uuid: str):
    row = await session.scalar(
        select(LearningGoalModel).where(
            LearningGoalModel.goal_uuid == str(goal_uuid),
            LearningGoalModel.organization_id == organization_id,
            LearningGoalModel.user_id == user_id,
        )
    )
    if row is None:
        raise NotFound("That learning goal is not here.")
    return row


def _brief(goal: LearningGoalModel) -> teacher.GoalBrief:
    return teacher.GoalBrief(
        title=goal.title,
        language=goal.explanation_language,
        studying_for=goal.studying_for,
        material=goal.material,
        level=goal.baseline_level,
    )


def _goal_dict(goal: LearningGoalModel) -> dict[str, Any]:
    return {
        "goal_id": goal.goal_uuid,
        "title": goal.title,
        "studying_for": goal.studying_for,
        "explanation_language": goal.explanation_language,
        "has_material": bool(goal.material),
        "status": goal.status,
        "baseline_level": goal.baseline_level,
        "thread_id": goal.thread_id,
        "review_reminders": bool(goal.review_reminders),
        "revision": goal.revision,
        "created_at": _iso(goal.created_at),
        "last_practised_at": _iso(goal.last_practised_at),
    }


_LINK = re.compile(r"^https?://\S+$")


async def _material_from_link(organization_id: int, notes: str | None) -> str | None:
    """A syllabus given as a link is read into the notes: the teacher can only
    teach from words it has, and the address alone is not a syllabus. A page
    that cannot be read is said, not kept as an empty "From your notes"."""
    if not notes or not _LINK.match(notes):
        return notes
    from api.services.workflow import web_tools

    got = await web_tools.fetch(
        organization_id, {"url": notes}, ref_id=f"learning-material:{uuid.uuid4()}"
    )
    text = str(got.get("text") or "").strip() if got.get("status") == "success" else ""
    if not text:
        why = got.get("error") or got.get("reason") or got.get("note") or ""
        raise LearningError(
            f"That page could not be read{': ' + why if why else '.'} Paste the "
            "syllabus or notes as text instead."
        )
    title = str(got.get("title") or "").strip()
    head = f"From {notes}" + (f" ({title})" if title else "") + ":\n"
    return (head + text)[: teacher.MAX_MATERIAL_CHARS]


async def start_goal(
    organization_id: int,
    user_id: int,
    *,
    title: str,
    studying_for: str | None = None,
    explanation_language: str | None = None,
    material: str | None = None,
    thread_id: str | None = None,
    confirm_sensitive: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A new goal and its one baseline question (handoff 23: start with a
    goal and preferred explanation language, ask one baseline question)."""
    now = _now(now)
    title_clean = _clean(title, MAX_TITLE)
    if not title_clean:
        raise LearningError("Say what you want to learn.")
    profile = await get_profile(organization_id, user_id)
    if not profile["adult_confirmed"]:
        raise ProfileNeeded("Confirm you are 18 or older to start learning.")
    studying = _clean(studying_for, MAX_STUDYING_FOR) or profile["studying_for"]
    notes = await _material_from_link(
        organization_id, _clean(material, teacher.MAX_MATERIAL_CHARS)
    )
    found = sensitive.detect(title_clean, studying, notes)
    if found and not confirm_sensitive:
        raise SensitiveDetails(categories=found)
    language = _language_ok(
        explanation_language
        or profile["explanation_language"]
        or await _default_language(user_id)
    )
    brief = teacher.GoalBrief(
        title=title_clean, language=language, studying_for=studying, material=notes
    )
    teach = teacher.for_organization(organization_id)
    await _spend_turn(user_id)
    baseline = await teach.baseline(brief)
    goal = LearningGoalModel(
        goal_uuid=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        title=title_clean,
        studying_for=studying,
        explanation_language=language,
        material=notes,
        status="baseline",
        baseline_question=baseline.question,
        thread_id=_clean(thread_id, 36),
        sensitive_confirmed_at=now if found else None,
        revision=0,
        created_at=now,
        updated_at=now,
    )
    goal_uuid = goal.goal_uuid
    async with db_client.async_session() as session:
        session.add(goal)
        await session.commit()
    await _emit(
        "learning_goal_started",
        organization_id,
        user_id,
        language=language,
        has_material=bool(notes),
    )
    return await session_state(organization_id, user_id, goal_uuid)


async def answer_baseline(
    organization_id: int, user_id: int, goal_uuid: str, answer: str
) -> dict[str, Any]:
    """Place the person from their first answer, then write the first
    lesson. The baseline is a placement, not practice: it moves no skill."""
    answer = _clean(answer, MAX_ANSWER)
    if not answer:
        raise LearningError("Write an answer first. 'I don't know yet' is fine.")
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        if goal.status != "baseline":
            # Already placed (a retry or a second tab): carry on from there.
            return await session_state(organization_id, user_id, goal_uuid)
        brief = _brief(goal)
        question = goal.baseline_question or ""
    teach = teacher.for_organization(organization_id)
    await _spend_turn(user_id)
    placement = await teach.place(brief, question, answer)
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        if goal.status == "baseline":
            goal.baseline_answer = answer
            goal.baseline_level = placement.level
            goal.baseline_feedback = placement.feedback
            goal.plan = list(placement.plan) or None
            goal.status = "active"
            goal.updated_at = _now()
            await session.commit()
    await next_exercise(organization_id, user_id, goal_uuid)
    return await session_state(organization_id, user_id, goal_uuid)


async def list_goals(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(LearningGoalModel)
                .where(
                    LearningGoalModel.organization_id == organization_id,
                    LearningGoalModel.user_id == user_id,
                    LearningGoalModel.status != "archived",
                )
                .order_by(
                    func.coalesce(
                        LearningGoalModel.last_practised_at,
                        LearningGoalModel.created_at,
                    ).desc()
                )
            )
        ).all()
    return [_goal_dict(row) for row in rows]


async def update_goal(
    organization_id: int,
    user_id: int,
    goal_uuid: str,
    changes: dict[str, Any],
    *,
    revision: int,
    confirm_sensitive: bool = False,
) -> dict[str, Any]:
    """The small editor on the progress page: title, explanation language,
    review reminders. Same save contract as preferences."""
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        if revision != goal.revision:
            raise Conflict(stored=_goal_dict(goal))
        if "title" in changes:
            title = _clean(changes["title"], MAX_TITLE)
            if not title:
                raise LearningError("A goal needs a name.")
            found = sensitive.detect(title)
            if found and not confirm_sensitive:
                raise SensitiveDetails(categories=found)
            if found:
                goal.sensitive_confirmed_at = _now()
            goal.title = title
        if "explanation_language" in changes:
            goal.explanation_language = _language_ok(
                str(changes["explanation_language"] or "")
            )
        if "review_reminders" in changes:
            goal.review_reminders = bool(changes["review_reminders"])
        goal.revision += 1
        goal.updated_at = _now()
        await session.flush()
        saved = _goal_dict(goal)
        await session.commit()
        return saved


# --- lessons and exercises --------------------------------------------------


async def _skill(session, goal: LearningGoalModel, name: str) -> LearningSkillModel:
    row = await session.scalar(
        select(LearningSkillModel).where(
            LearningSkillModel.goal_id == goal.id, LearningSkillModel.name == name
        )
    )
    if row is None:
        row = LearningSkillModel(
            goal_id=goal.id,
            organization_id=goal.organization_id,
            user_id=goal.user_id,
            name=name,
            status=NOT_PRACTISED,
            evaluated_attempts=0,
            passed_attempts=0,
            misses_in_a_row=0,
            interval_step=0,
            created_at=_now(),
        )
        session.add(row)
        await session.flush()
    return row


async def _open_exercise(session, goal_id: int) -> LearningExerciseModel | None:
    """The newest exercise not yet passed, if it has no attempt yet or was
    missed: where a session resumes."""
    return await session.scalar(
        select(LearningExerciseModel)
        .where(
            LearningExerciseModel.goal_id == goal_id,
            LearningExerciseModel.status != "passed",
        )
        .order_by(LearningExerciseModel.id.desc())
        .limit(1)
    )


async def _attempt_count(session, exercise_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(LearningAttemptModel.id)).where(
                LearningAttemptModel.exercise_id == exercise_id
            )
        )
        or 0
    )


async def next_exercise(
    organization_id: int,
    user_id: int,
    goal_uuid: str,
    *,
    easier: bool = False,
    skill_id: int | None = None,
    kind: str = "practice",
) -> dict[str, Any]:
    """Write the next lesson and its exercise.

    An exercise already on screen with no attempt yet is returned rather
    than replaced: pressing Next twice does not spend two lessons.
    ``skill_id`` teaches that skill again (a review, or a stuck skill).
    """
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        if goal.status == "baseline":
            raise LearningError("Answer the first question, then we start.")
        current = await _open_exercise(session, goal.id)
        if (
            current is not None
            and (skill_id is None or current.skill_id == skill_id)
            and await _attempt_count(session, current.id) == 0
        ):
            return await session_state(organization_id, user_id, goal_uuid)
        focus = None
        if skill_id is None and easier:
            # "Try a smaller step" after a miss is about the skill that was
            # missed, not the next one: teach that one again, smaller.
            latest = await session.scalar(
                select(LearningExerciseModel)
                .where(LearningExerciseModel.goal_id == goal.id)
                .order_by(LearningExerciseModel.id.desc())
                .limit(1)
            )
            if latest is not None and latest.status != "passed":
                skill_id = latest.skill_id
        if skill_id is not None:
            skill_row = await session.scalar(
                select(LearningSkillModel).where(
                    LearningSkillModel.id == skill_id,
                    LearningSkillModel.goal_id == goal.id,
                )
            )
            if skill_row is None:
                raise NotFound("That skill is not in this goal.")
            focus = skill_row.name
        elif goal.plan:
            # The plan decides what comes next: the first lesson in it not
            # yet practised. A lesson that was just missed is that lesson,
            # so it comes back -- smaller, because it was missed.
            practised = set(
                (
                    await session.scalars(
                        select(LearningSkillModel).where(
                            LearningSkillModel.goal_id == goal.id
                        )
                    )
                ).all()
            )
            by_name = {row.name.casefold(): row for row in practised}
            for name in goal.plan:
                row = by_name.get(str(name).casefold())
                if row is None or row.status != PRACTISED:
                    focus = str(name)
                    if row is not None and (row.misses_in_a_row or 0) > 0:
                        easier = True
                    break
        names = (
            await session.scalars(
                select(LearningSkillModel.name)
                .where(LearningSkillModel.goal_id == goal.id)
                .order_by(LearningSkillModel.id)
            )
        ).all()
        brief = _brief(goal)
    teach = teacher.for_organization(organization_id)
    await _spend_turn(user_id)
    lesson = await teach.lesson(
        brief, skills_so_far=list(names), focus=focus, easier=easier
    )
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        skill = await _skill(session, goal, lesson.skill[:120])
        sequence = (
            int(
                await session.scalar(
                    select(
                        func.coalesce(func.max(LearningLessonModel.sequence), 0)
                    ).where(LearningLessonModel.goal_id == goal.id)
                )
                or 0
            )
            + 1
        )
        lesson_row = LearningLessonModel(
            goal_id=goal.id,
            organization_id=organization_id,
            user_id=user_id,
            sequence=sequence,
            skill_id=skill.id,
            objective=lesson.objective,
            explanation=lesson.explanation,
            source_kind=lesson.source_kind,
            sources=list(lesson.sources),
            written_by=getattr(teach, "written_by", teach.label),
            created_at=_now(),
        )
        session.add(lesson_row)
        await session.flush()
        session.add(
            LearningExerciseModel(
                exercise_uuid=str(uuid.uuid4()),
                goal_id=goal.id,
                lesson_id=lesson_row.id,
                skill_id=skill.id,
                organization_id=organization_id,
                user_id=user_id,
                kind=kind if kind in ("practice", "review") else "practice",
                prompt=lesson.exercise,
                rubric=list(lesson.rubric),
                status="open",
                created_at=_now(),
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            # Two Nexts at once took the same lesson number: the other one
            # wrote the lesson, which is what this one would have.
            await session.rollback()
    return await session_state(organization_id, user_id, goal_uuid)


async def review(
    organization_id: int, user_id: int, goal_uuid: str, skill_id: int
) -> dict[str, Any]:
    """Practise a skill that is due for review: a fresh exercise on it."""
    return await next_exercise(
        organization_id, user_id, goal_uuid, skill_id=skill_id, kind="review"
    )


# --- practice ---------------------------------------------------------------


def _attempt_dict(row: LearningAttemptModel) -> dict[str, Any]:
    return {
        "attempt_id": row.id,
        "answer": row.answer,
        "outcome": row.outcome,
        "rubric_results": list(row.rubric_results or []),
        "feedback": row.feedback,
        "created_at": _iso(row.created_at),
    }


def _apply(skill: LearningSkillModel, outcome: str, now: datetime) -> None:
    """Move one skill's evidence by one evaluated attempt."""
    skill.evaluated_attempts = (skill.evaluated_attempts or 0) + 1
    skill.last_outcome = outcome
    skill.last_evaluated_at = now
    if outcome == teacher.PASSED:
        skill.passed_attempts = (skill.passed_attempts or 0) + 1
        skill.misses_in_a_row = 0
        skill.status = PRACTISED
        step = skill.interval_step or 0
        skill.next_review_at = now + timedelta(days=REVIEW_INTERVALS[step])
        skill.interval_step = min(step + 1, len(REVIEW_INTERVALS) - 1)
    else:
        skill.misses_in_a_row = (skill.misses_in_a_row or 0) + 1
        skill.status = NEEDS_ANOTHER
        skill.interval_step = 0
        skill.next_review_at = now + timedelta(days=REVIEW_INTERVALS[0])


async def _existing_attempt(user_id: int, key: str) -> LearningAttemptModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(LearningAttemptModel).where(
                LearningAttemptModel.user_id == user_id,
                LearningAttemptModel.idempotency_key == key,
            )
        )


async def submit_attempt(
    organization_id: int,
    user_id: int,
    goal_uuid: str,
    *,
    exercise_id: str,
    answer: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark one answer and record the evidence, once.

    Returns ``{"attempt": ..., "replayed": bool, "session": ...}``. A key
    already used returns the first marking with ``replayed`` and changes
    nothing -- not the skill, not the counts.
    """
    key = _clean(idempotency_key, 64)
    if not key:
        raise LearningError("A submission needs an idempotency key.")
    text = _clean(answer, MAX_ANSWER)
    if not text:
        raise LearningError("Write an answer first.")
    earlier = await _existing_attempt(user_id, key)
    if earlier is not None:
        if earlier.organization_id != organization_id:
            raise NotFound("That learning goal is not here.")
        return {
            "attempt": _attempt_dict(earlier),
            "replayed": True,
            "session": await session_state(organization_id, user_id, goal_uuid),
        }
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        exercise = await session.scalar(
            select(LearningExerciseModel).where(
                LearningExerciseModel.exercise_uuid == str(exercise_id),
                LearningExerciseModel.goal_id == goal.id,
            )
        )
        if exercise is None:
            raise NotFound("That exercise is not in this goal.")
        if exercise.status == "passed":
            raise LearningError("This exercise is done. Take the next one.")
        brief = _brief(goal)
        prompt, rubric = exercise.prompt, list(exercise.rubric or [])
        exercise_kind = exercise.kind
    teach = teacher.for_organization(organization_id)
    await _spend_turn(user_id)
    marking = await teach.mark(brief, exercise=prompt, rubric=rubric, answer=text)
    now = _now(now)
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        exercise = await session.scalar(
            select(LearningExerciseModel)
            .where(
                LearningExerciseModel.exercise_uuid == str(exercise_id),
                LearningExerciseModel.goal_id == goal.id,
            )
            .with_for_update()
        )
        skill = await session.scalar(
            select(LearningSkillModel)
            .where(LearningSkillModel.id == exercise.skill_id)
            .with_for_update()
        )
        row = LearningAttemptModel(
            exercise_id=exercise.id,
            goal_id=goal.id,
            organization_id=organization_id,
            user_id=user_id,
            answer=text,
            outcome=marking.outcome,
            rubric_results=list(marking.results),
            feedback=marking.feedback,
            marked_by=getattr(teach, "written_by", teach.label),
            idempotency_key=key,
            created_at=now,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # The same key, submitted twice at once: the other one counted.
            await session.rollback()
            earlier = await _existing_attempt(user_id, key)
            return {
                "attempt": _attempt_dict(earlier),
                "replayed": True,
                "session": await session_state(organization_id, user_id, goal_uuid),
            }
        _apply(skill, marking.outcome, now)
        exercise.status = (
            "passed" if marking.outcome == teacher.PASSED else NEEDS_ANOTHER
        )
        goal.last_practised_at = now
        goal.updated_at = now
        attempt = _attempt_dict(row)
        await session.commit()
    await _emit(
        "learning_practice_evaluated",
        organization_id,
        user_id,
        outcome=marking.outcome,
        exercise_kind=exercise_kind,
    )
    return {
        "attempt": attempt,
        "replayed": False,
        "session": await session_state(organization_id, user_id, goal_uuid),
    }


# --- the session (screen 13) ------------------------------------------------


async def session_state(
    organization_id: int, user_id: int, goal_uuid: str
) -> dict[str, Any]:
    """Everything the lesson in Chat shows, and which state it is in:

    * ``baseline`` -- the first question, waiting for an answer;
    * ``waiting_for_answer`` -- an exercise with no attempt yet;
    * ``correction`` -- the last answer was not passed: Try again or Next;
    * ``completed`` -- the last exercise was passed: Next exercise;
    * ``no_exercise`` -- placed, but no lesson is written yet (a teacher
      that failed after placement): Next exercise writes one.
    """
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        out: dict[str, Any] = {"goal": _goal_dict(goal)}
        if goal.status == "baseline":
            out.update(state="baseline", baseline_question=goal.baseline_question)
            return out
        out["baseline_feedback"] = goal.baseline_feedback
        exercise = await session.scalar(
            select(LearningExerciseModel)
            .where(LearningExerciseModel.goal_id == goal.id)
            .order_by(LearningExerciseModel.id.desc())
            .limit(1)
        )
        if exercise is None:
            out["state"] = "no_exercise"
            return out
        lesson = await session.get(LearningLessonModel, exercise.lesson_id)
        skill = await session.get(LearningSkillModel, exercise.skill_id)
        attempts = (
            await session.scalars(
                select(LearningAttemptModel)
                .where(LearningAttemptModel.exercise_id == exercise.id)
                .order_by(LearningAttemptModel.id)
            )
        ).all()
        out["lesson"] = {
            "lesson_id": lesson.id,
            "objective": lesson.objective,
            "explanation": lesson.explanation,
            "source_kind": lesson.source_kind,
            "sources": list(lesson.sources or []),
            "skill": skill.name,
            "skill_id": skill.id,
        }
        out["exercise"] = {
            "exercise_id": exercise.exercise_uuid,
            "kind": exercise.kind,
            "prompt": exercise.prompt,
            "rubric": list(exercise.rubric or []),
            "status": exercise.status,
        }
        out["attempts"] = [_attempt_dict(a) for a in attempts]
        if not attempts:
            out["state"] = "waiting_for_answer"
        elif exercise.status == "passed":
            out["state"] = "completed"
        else:
            out["state"] = "correction"
        return out


# --- progress (screen 14) ---------------------------------------------------


def _skill_dict(skill: LearningSkillModel, now: datetime) -> dict[str, Any]:
    due = skill.next_review_at
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "status": skill.status,
        "label": SKILL_LABELS.get(skill.status, skill.status),
        "evaluated_attempts": skill.evaluated_attempts,
        "passed_attempts": skill.passed_attempts,
        "last_outcome": skill.last_outcome,
        "next_review_at": _iso(due),
        "review_due": bool(due is not None and due <= now and skill.evaluated_attempts),
    }


async def progress(
    organization_id: int,
    user_id: int,
    goal_uuid: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evidence only: skill rows, recent exercises and one next step.
    ``practice_count`` counts evaluated attempts -- not messages, not
    minutes -- and no data is "no practice yet", never zero ability."""
    now = _now(now)
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        skills = (
            await session.scalars(
                select(LearningSkillModel)
                .where(LearningSkillModel.goal_id == goal.id)
                .order_by(LearningSkillModel.id)
            )
        ).all()
        recent = (
            await session.execute(
                select(LearningAttemptModel, LearningExerciseModel)
                .join(
                    LearningExerciseModel,
                    LearningExerciseModel.id == LearningAttemptModel.exercise_id,
                )
                .where(LearningAttemptModel.goal_id == goal.id)
                .order_by(LearningAttemptModel.id.desc())
                .limit(10)
            )
        ).all()
        count = int(
            await session.scalar(
                select(func.count(LearningAttemptModel.id)).where(
                    LearningAttemptModel.goal_id == goal.id
                )
            )
            or 0
        )
        goal_out = _goal_dict(goal)
        goal_plan = list(goal.plan or [])
    skill_rows = [_skill_dict(s, now) for s in skills]
    return {
        "goal": goal_out,
        "plan": _plan_rows(goal_plan, skill_rows),
        "practice_count": count,
        "state": "no_practice" if count == 0 else "practised",
        "skills": skill_rows,
        "recent": [
            {
                **_attempt_dict(attempt),
                "exercise_prompt": exercise.prompt,
                "exercise_kind": exercise.kind,
            }
            for attempt, exercise in recent
        ],
        "next_step": _next_step(goal_out, skill_rows),
    }


def _plan_rows(plan: list[str], skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The plan in order, each lesson with its skill's evidence (or "Not
    practised yet" when it has not been taught)."""
    by_name = {s["name"].casefold(): s for s in skills}
    rows = []
    for name in plan:
        skill = by_name.get(str(name).casefold())
        status = skill["status"] if skill else NOT_PRACTISED
        rows.append(
            {
                "name": str(name),
                "skill_id": skill["skill_id"] if skill else None,
                "status": status,
                "label": SKILL_LABELS.get(status, status),
            }
        )
    return rows


def _next_step(goal: dict[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any]:
    """One next step, in this order: finish placement, a due review, a skill
    that needs another attempt, then carry on."""
    if goal["status"] == "baseline":
        return {"kind": "baseline", "text": "Answer the first question to begin."}
    due = [s for s in skills if s["review_due"]]
    if due:
        return {
            "kind": "review",
            "skill_id": due[0]["skill_id"],
            "text": f"Review {due[0]['name']}.",
        }
    again = [s for s in skills if s["status"] == NEEDS_ANOTHER]
    if again:
        return {
            "kind": "retry",
            "skill_id": again[0]["skill_id"],
            "text": f"Try {again[0]['name']} again.",
        }
    return {"kind": "continue", "text": "Continue practice."}


async def skill_detail(
    organization_id: int, user_id: int, goal_uuid: str, skill_id: int
) -> dict[str, Any]:
    """A skill's rubric (from its latest exercise) and every attempt on it."""
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        skill = await session.scalar(
            select(LearningSkillModel).where(
                LearningSkillModel.id == skill_id, LearningSkillModel.goal_id == goal.id
            )
        )
        if skill is None:
            raise NotFound("That skill is not in this goal.")
        latest = await session.scalar(
            select(LearningExerciseModel)
            .where(LearningExerciseModel.skill_id == skill.id)
            .order_by(LearningExerciseModel.id.desc())
            .limit(1)
        )
        attempts = (
            await session.execute(
                select(LearningAttemptModel, LearningExerciseModel)
                .join(
                    LearningExerciseModel,
                    LearningExerciseModel.id == LearningAttemptModel.exercise_id,
                )
                .where(LearningExerciseModel.skill_id == skill.id)
                .order_by(LearningAttemptModel.id.desc())
            )
        ).all()
        return {
            "skill": _skill_dict(skill, _now()),
            "rubric": list(latest.rubric or []) if latest is not None else [],
            "attempts": [
                {**_attempt_dict(a), "exercise_prompt": e.prompt} for a, e in attempts
            ],
        }


# --- reviews and suggestions -----------------------------------------------


async def reviews_due(
    organization_id: int, user_id: int, *, now: datetime | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Skills whose review date has come, oldest first, for Today."""
    now = _now(now)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(LearningSkillModel, LearningGoalModel)
                .join(
                    LearningGoalModel,
                    LearningGoalModel.id == LearningSkillModel.goal_id,
                )
                .where(
                    LearningGoalModel.organization_id == organization_id,
                    LearningGoalModel.user_id == user_id,
                    LearningGoalModel.status == "active",
                    LearningSkillModel.next_review_at.is_not(None),
                    LearningSkillModel.next_review_at <= now,
                    LearningSkillModel.evaluated_attempts > 0,
                )
                .order_by(LearningSkillModel.next_review_at)
                .limit(limit)
            )
        ).all()
    return [
        {
            "goal_id": goal.goal_uuid,
            "goal_title": goal.title,
            "skill_id": skill.id,
            "skill_name": skill.name,
            "status": skill.status,
            "label": SKILL_LABELS.get(skill.status, skill.status),
            "due_at": _iso(skill.next_review_at),
        }
        for skill, goal in rows
    ]


async def suggestions(
    organization_id: int, user_id: int, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """At most three, from evaluated attempts only:

    * **stuck** -- missed ``STUCK_AFTER`` times in a row: a smaller step;
    * **improving** -- passed after a miss: move on to the next skill;
    * **review** -- a review that is due.

    No attempts, no suggestions: nothing is invented to make the page look
    busy (handoff 22).
    """
    now = _now(now)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(LearningSkillModel, LearningGoalModel)
                .join(
                    LearningGoalModel,
                    LearningGoalModel.id == LearningSkillModel.goal_id,
                )
                .where(
                    LearningGoalModel.organization_id == organization_id,
                    LearningGoalModel.user_id == user_id,
                    LearningGoalModel.status == "active",
                    LearningSkillModel.evaluated_attempts > 0,
                )
                .order_by(LearningSkillModel.last_evaluated_at.desc())
            )
        ).all()
        improving_ids = set()
        for skill, _goal in rows:
            if skill.last_outcome != teacher.PASSED or skill.evaluated_attempts < 2:
                continue
            outcomes = (
                await session.scalars(
                    select(LearningAttemptModel.outcome)
                    .join(
                        LearningExerciseModel,
                        LearningExerciseModel.id == LearningAttemptModel.exercise_id,
                    )
                    .where(LearningExerciseModel.skill_id == skill.id)
                    .order_by(LearningAttemptModel.id.desc())
                    .limit(2)
                )
            ).all()
            if len(outcomes) == 2 and outcomes[1] != teacher.PASSED:
                improving_ids.add(skill.id)
    out: list[dict[str, Any]] = []
    for skill, goal in rows:
        if skill.misses_in_a_row >= STUCK_AFTER:
            out.append(
                {
                    "kind": "stuck",
                    "goal_id": goal.goal_uuid,
                    "skill_id": skill.id,
                    "text": (
                        f"{skill.name} has been hard twice in a row. Try a "
                        "smaller step."
                    ),
                    "action": "easier",
                }
            )
    for skill, goal in rows:
        if skill.id in improving_ids:
            out.append(
                {
                    "kind": "improving",
                    "goal_id": goal.goal_uuid,
                    "skill_id": skill.id,
                    "text": f"You got {skill.name} after another try. Ready for the next step?",
                    "action": "next",
                }
            )
    for skill, goal in rows:
        due = skill.next_review_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if due is not None and due <= now and skill.misses_in_a_row < STUCK_AFTER:
            out.append(
                {
                    "kind": "review",
                    "goal_id": goal.goal_uuid,
                    "skill_id": skill.id,
                    "text": f"{skill.name} is due for a review.",
                    "action": "review",
                }
            )
    return out[:MAX_SUGGESTIONS]


# --- streak and the day's lesson (Today) ------------------------------------


async def _zone(user_id: int):
    from zoneinfo import ZoneInfo

    from api.services import member_preferences

    try:
        name = (await member_preferences.get(user_id)).get("timezone")
        return ZoneInfo(name) if name else ZoneInfo("Asia/Kolkata")
    except Exception:  # noqa: BLE001 - an unknown zone is India's, not an error
        return ZoneInfo("Asia/Kolkata")


async def streak(
    organization_id: int, user_id: int, *, now: datetime | None = None
) -> dict[str, Any]:
    """Days in a row, in the person's own timezone, with at least one marked
    answer -- across every goal here. Evidence like the rest: reading or
    chatting never keeps a streak. A day not practised *yet* does not break
    it until the day is over."""
    now = _now(now)
    zone = await _zone(user_id)
    async with db_client.async_session() as session:
        stamps = (
            await session.scalars(
                select(LearningAttemptModel.created_at).where(
                    LearningAttemptModel.organization_id == organization_id,
                    LearningAttemptModel.user_id == user_id,
                    LearningAttemptModel.created_at <= now,
                    LearningAttemptModel.created_at >= now - timedelta(days=400),
                )
            )
        ).all()
    days = set()
    for at in stamps:
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        days.add(at.astimezone(zone).date())
    today_local = now.astimezone(zone).date()
    practised_today = today_local in days
    day = today_local if practised_today else today_local - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return {"days": count, "practised_today": practised_today}


async def today(
    organization_id: int, user_id: int, *, now: datetime | None = None
) -> dict[str, Any]:
    """What Today shows for learning: the streak and, for each active goal,
    the day's lesson (its next step) and whether it was practised today."""
    now = _now(now)
    zone = await _zone(user_id)
    today_local = now.astimezone(zone).date()
    lessons = []
    for goal in await list_goals(organization_id, user_id):
        # A course waiting on its first answer is shown too: it is the way
        # back to a course started from Chat.
        if goal["status"] not in ("active", "baseline"):
            continue
        got = await progress(organization_id, user_id, goal["goal_id"], now=now)
        step = got["next_step"]
        text = step["text"]
        if step["kind"] == "continue":
            planned = next((p for p in got["plan"] if p["status"] != PRACTISED), None)
            text = (
                f"Today's lesson: {planned['name']}."
                if planned
                else "Today's lesson: the next step."
            )
        last = goal["last_practised_at"]
        done = bool(
            last and datetime.fromisoformat(last).astimezone(zone).date() == today_local
        )
        lessons.append(
            {
                "goal_id": goal["goal_id"],
                "goal_title": goal["title"],
                "kind": step["kind"],
                "skill_id": step.get("skill_id"),
                "text": text,
                "done_today": done,
            }
        )
    return {
        "streak": await streak(organization_id, user_id, now=now),
        "lessons": lessons[:MAX_SUGGESTIONS],
    }


# --- export and deletion ----------------------------------------------------


async def export(organization_id: int, user_id: int, goal_uuid: str) -> dict[str, Any]:
    """The whole record for one goal, as the person's own copy."""
    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        lessons = (
            await session.scalars(
                select(LearningLessonModel)
                .where(LearningLessonModel.goal_id == goal.id)
                .order_by(LearningLessonModel.sequence)
            )
        ).all()
        exercises = (
            await session.scalars(
                select(LearningExerciseModel)
                .where(LearningExerciseModel.goal_id == goal.id)
                .order_by(LearningExerciseModel.id)
            )
        ).all()
        attempts = (
            await session.scalars(
                select(LearningAttemptModel)
                .where(LearningAttemptModel.goal_id == goal.id)
                .order_by(LearningAttemptModel.id)
            )
        ).all()
        skills = (
            await session.scalars(
                select(LearningSkillModel).where(LearningSkillModel.goal_id == goal.id)
            )
        ).all()
        by_exercise: dict[int, list] = {}
        for a in attempts:
            by_exercise.setdefault(a.exercise_id, []).append(_attempt_dict(a))
        return {
            "exported_at": _iso(_now()),
            "goal": {
                **_goal_dict(goal),
                "baseline_question": goal.baseline_question,
                "baseline_answer": goal.baseline_answer,
                "baseline_feedback": goal.baseline_feedback,
                "material": goal.material,
            },
            "skills": [_skill_dict(s, _now()) for s in skills],
            "lessons": [
                {
                    "sequence": lesson.sequence,
                    "objective": lesson.objective,
                    "explanation": lesson.explanation,
                    "source_kind": lesson.source_kind,
                    "sources": list(lesson.sources or []),
                    "exercises": [
                        {
                            "prompt": e.prompt,
                            "kind": e.kind,
                            "rubric": list(e.rubric or []),
                            "status": e.status,
                            "attempts": by_exercise.get(e.id, []),
                        }
                        for e in exercises
                        if e.lesson_id == lesson.id
                    ],
                }
                for lesson in lessons
            ],
        }


async def request_deletion(
    organization_id: int, user_id: int, goal_uuid: str
) -> dict[str, Any]:
    """Put a delete card up (the controls action card): nothing is deleted
    until the person confirms it, and it runs once. Returns the card."""
    from api.services.workflow import actions

    async with db_client.async_session() as session:
        goal = await _goal(session, organization_id, user_id, goal_uuid)
        thread_id = goal.thread_id
    return await actions.propose_learning_deletion(
        organization_id=organization_id,
        user_id=user_id,
        goal_uuid=goal_uuid,
        thread_id=thread_id,
    )


async def delete_goal(organization_id: int, user_id: int, goal_uuid: str) -> bool:
    """Delete one goal and everything under it (skills, lessons, exercises,
    attempts cascade). Called by the confirmed card, never by a route.
    Nothing derived from it survives elsewhere: learning writes no memory
    facts and no tasks."""
    async with db_client.async_session() as session:
        result = await session.execute(
            delete(LearningGoalModel).where(
                and_(
                    LearningGoalModel.goal_uuid == str(goal_uuid),
                    LearningGoalModel.organization_id == organization_id,
                    LearningGoalModel.user_id == user_id,
                )
            )
        )
        await session.commit()
    deleted = bool(result.rowcount)
    if deleted:
        await _emit("learning_goal_deleted", organization_id, user_id)
    else:
        logger.info("Learning goal {} was already gone", goal_uuid)
    return deleted


async def owns_goal(organization_id: int, user_id: int, goal_uuid: str) -> bool:
    async with db_client.async_session() as session:
        try:
            await _goal(session, organization_id, user_id, goal_uuid)
        except NotFound:
            return False
    return True


async def goal_title(organization_id: int, user_id: int, goal_uuid: str) -> str | None:
    async with db_client.async_session() as session:
        try:
            return (await _goal(session, organization_id, user_id, goal_uuid)).title
        except NotFound:
            return None


async def feedback_subject(
    organization_id: int, viewer_id: int | None, lesson_id: int
) -> dict[str, Any]:
    """What "Was this useful?" on a lesson is stored against (controls'
    services/feedback.py): the learner's own lesson, once practised."""
    import hashlib

    async with db_client.async_session() as session:
        lesson = await session.scalar(
            select(LearningLessonModel).where(
                LearningLessonModel.id == lesson_id,
                LearningLessonModel.organization_id == organization_id,
                LearningLessonModel.user_id == (viewer_id or 0),
            )
        )
        if lesson is None:
            raise NotFound("That lesson is not here.")
        practised = await session.scalar(
            select(func.count(LearningAttemptModel.id))
            .join(
                LearningExerciseModel,
                LearningExerciseModel.id == LearningAttemptModel.exercise_id,
            )
            .where(LearningExerciseModel.lesson_id == lesson.id)
        )
    if not practised:
        raise LearningError("Feedback is for a lesson you have practised.")
    words = f"{lesson.objective}\n{lesson.explanation}"
    return {
        "output_version": hashlib.sha256(words.encode()).hexdigest()[:32],
        "model": lesson.written_by,
        "task_version": None,
        "thread_id": None,
        "workflow_id": None,
    }

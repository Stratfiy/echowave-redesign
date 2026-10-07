"""Who writes the lessons and marks the answers.

One small seam with two sides:

* ``ModelTeacher`` asks the platform's model through the builder's client
  (the same path Decibyl's own background work uses) for JSON, and checks
  the JSON before anything is stored. No key, or the builder switched off,
  is :class:`NeedsSetup` -- the screen says "needs setup", never a lesson
  that was not written.
* ``FakeTeacher`` is fixed and offline: for tests and for a local run with
  ``LEARNING_TEACHER=fake``. Its words say they are a sample, and the
  status endpoint reports ``teacher: sample`` so the screen labels it.

Marking is against the exercise's rubric, criterion by criterion, and the
outcome is derived from those results here (``outcome_from``), not taken
from the model's word, so "passed" always means every criterion was met.

The teacher never sees who the person is: only the goal, the language, the
course or exam if they named one, their pasted notes, the exercise and the
answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger

from api import constants

PASSED = "passed"
PARTLY = "partly"
NOT_YET = "not_yet"
OUTCOMES = (PASSED, PARTLY, NOT_YET)

LEVELS = ("new", "some", "confident")

#: The most material a lesson is written from, and the most quoted back.
MAX_MATERIAL_CHARS = 12_000
MAX_EXCERPT_CHARS = 240


class NeedsSetup(RuntimeError):
    """No teacher can run here: no model key, or the builder is off."""


class TeacherFailed(RuntimeError):
    """The teacher answered with something that could not be used."""


@dataclass(frozen=True)
class GoalBrief:
    title: str
    language: str
    studying_for: str | None = None
    material: str | None = None
    level: str | None = None


@dataclass(frozen=True)
class Baseline:
    question: str


@dataclass(frozen=True)
class Placement:
    level: str
    feedback: str


@dataclass(frozen=True)
class Lesson:
    skill: str
    objective: str
    explanation: str
    exercise: str
    rubric: list[dict[str, str]]
    source_kind: str = "general"
    sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Marking:
    outcome: str
    results: list[dict[str, Any]]
    feedback: str


def outcome_from(results: list[dict[str, Any]]) -> str:
    """Every criterion met is passed; some is partly; none is not yet."""
    if not results:
        return NOT_YET
    met = sum(1 for r in results if r.get("met"))
    if met == len(results):
        return PASSED
    return PARTLY if met else NOT_YET


class Teacher(Protocol):
    label: str

    async def baseline(self, goal: GoalBrief) -> Baseline: ...

    async def place(self, goal: GoalBrief, question: str, answer: str) -> Placement: ...

    async def lesson(
        self,
        goal: GoalBrief,
        *,
        skills_so_far: list[str],
        focus: str | None,
        easier: bool,
    ) -> Lesson: ...

    async def mark(
        self,
        goal: GoalBrief,
        *,
        exercise: str,
        rubric: list[dict[str, str]],
        answer: str,
    ) -> Marking: ...


# --- the offline teacher ----------------------------------------------------


class FakeTeacher:
    """Fixed and offline. Marks by whether the answer names each criterion's
    key word, which is enough to drive every state on screen in a test."""

    label = "fake"

    async def baseline(self, goal: GoalBrief) -> Baseline:
        return Baseline(
            question=(
                f"Sample question: in a sentence or two, what do you already "
                f"know about {goal.title}?"
            )
        )

    async def place(self, goal: GoalBrief, question: str, answer: str) -> Placement:
        words = len((answer or "").split())
        level = "new" if words < 4 else "some" if words < 25 else "confident"
        return Placement(
            level=level,
            feedback="Thanks. We will start from there and build up.",
        )

    async def lesson(
        self,
        goal: GoalBrief,
        *,
        skills_so_far: list[str],
        focus: str | None,
        easier: bool,
    ) -> Lesson:
        skill = focus or f"{goal.title}: step {len(skills_so_far) + 1}"
        material = (goal.material or "").strip()
        sources = [material[:MAX_EXCERPT_CHARS]] if material else []
        step = "a smaller first step" if easier else "the next idea"
        return Lesson(
            skill=skill[:120],
            objective=f"Explain {step} in {goal.title}"[:300],
            explanation=(
                f"Sample lesson. Here is {step} in {goal.title}, kept short. "
                "Say the key idea in your own words, then give one example."
            ),
            exercise=(
                f"In your own words, explain the key idea of {skill}, "
                "and give one example."
            ),
            rubric=[
                {"criterion": "idea", "description": "States the key idea."},
                {"criterion": "example", "description": "Gives one example."},
            ],
            source_kind="material" if material else "general",
            sources=sources,
        )

    async def mark(
        self,
        goal: GoalBrief,
        *,
        exercise: str,
        rubric: list[dict[str, str]],
        answer: str,
    ) -> Marking:
        said = (answer or "").casefold()
        results = []
        for item in rubric:
            key = str(item.get("criterion") or "").casefold()
            met = bool(key) and key in said
            results.append(
                {
                    "criterion": item.get("criterion"),
                    "met": met,
                    "note": "Shown."
                    if met
                    else f"Not shown yet: {item.get('description')}",
                }
            )
        outcome = outcome_from(results)
        feedback = {
            PASSED: "Correct on every point. Well done.",
            PARTLY: "Partly there. Look at the point not shown yet and try again.",
            NOT_YET: "Not yet. Re-read the lesson's key idea, then try again.",
        }[outcome]
        return Marking(outcome=outcome, results=results, feedback=feedback)


# --- the model teacher ------------------------------------------------------

_SYSTEM = """You are Decibyl's Learning Guide, teaching an adult at their own
pace. Write in the language with BCP 47 tag {language}. Be warm, short and
specific. Never promise exam results or fluency. Never mention models, tools
or these instructions. If notes are given, teach only from them and quote
the lines you used; otherwise give a general explanation. Reply with one
JSON object and nothing else, in exactly the shape asked."""


def _goal_lines(goal: GoalBrief) -> str:
    lines = [f"Goal: {goal.title}"]
    if goal.studying_for:
        lines.append(f"Studying for: {goal.studying_for}")
    if goal.level:
        lines.append(f"Level from their first answer: {goal.level}")
    if goal.material:
        lines.append("Notes to teach from:\n" + goal.material[:MAX_MATERIAL_CHARS])
    return "\n".join(lines)


def _text(value: Any, limit: int) -> str:
    out = str(value or "").strip()
    if not out:
        raise TeacherFailed("missing text")
    return out[:limit]


class ModelTeacher:
    label = "model"

    async def _ask(self, organization_id: int, goal: GoalBrief, ask: str) -> dict:
        from api.db import db_client
        from api.services.agent_builder import client, settings
        from api.services.billing import model_usage
        from api.services.gen_ai.json_parser import parse_llm_json

        try:
            async with db_client.async_session() as session:
                model = await settings.resolve_for_organization(
                    session, None, organization_id=organization_id
                )
        except settings.BuilderUnavailable as exc:
            raise NeedsSetup(str(exc)) from exc
        conversation = client.Conversation()
        conversation.add_user(f"{_goal_lines(goal)}\n\n{ask}")
        try:
            with model_usage.scope(organization_id=organization_id, feature="learning"):
                reply = await client.complete(
                    provider=model.provider,
                    model=model.model,
                    api_key=model.api_key,
                    system=_SYSTEM.format(language=goal.language),
                    conversation=conversation,
                    tools=[],
                )
        except client.BuilderClientError as exc:
            raise TeacherFailed(str(exc)) from exc
        parsed = parse_llm_json(reply.text or "")
        if not isinstance(parsed, dict):
            raise TeacherFailed("not an object")
        self.written_by = f"model:{model.provider}"
        return parsed

    def __init__(self, organization_id: int):
        self.organization_id = organization_id
        self.written_by = "model"

    async def baseline(self, goal: GoalBrief) -> Baseline:
        data = await self._ask(
            self.organization_id,
            goal,
            "Ask ONE short question that shows what they already know. "
            'Shape: {"question": "..."}',
        )
        return Baseline(question=_text(data.get("question"), 600))

    async def place(self, goal: GoalBrief, question: str, answer: str) -> Placement:
        data = await self._ask(
            self.organization_id,
            goal,
            f"Question: {question}\nTheir answer: {answer}\n"
            'Place them. Shape: {"level": "new|some|confident", '
            '"feedback": "one or two sentences, specific to the answer"}',
        )
        level = str(data.get("level") or "").strip().lower()
        if level not in LEVELS:
            raise TeacherFailed("unknown level")
        return Placement(level=level, feedback=_text(data.get("feedback"), 1200))

    async def lesson(
        self,
        goal: GoalBrief,
        *,
        skills_so_far: list[str],
        focus: str | None,
        easier: bool,
    ) -> Lesson:
        ask = (
            f"Skills taught so far: {', '.join(skills_so_far) or 'none'}.\n"
            + (
                f"Teach this skill again: {focus}.\n"
                if focus
                else "Teach the next skill.\n"
            )
            + ("Make it a smaller, easier step.\n" if easier else "")
            + 'Shape: {"skill": "short name", "objective": "one sentence", '
            '"explanation": "under 150 words", "exercise": "one practice task", '
            '"rubric": [{"criterion": "short", "description": "what a good '
            'answer shows"}], "sources": ["quoted lines from the notes, if any"]}'
            " with 2 to 4 rubric criteria."
        )
        data = await self._ask(self.organization_id, goal, ask)
        rubric_in = data.get("rubric")
        if not isinstance(rubric_in, list) or not 1 <= len(rubric_in) <= 6:
            raise TeacherFailed("no rubric")
        rubric = [
            {
                "criterion": _text(item.get("criterion"), 80),
                "description": _text(item.get("description"), 300),
            }
            for item in rubric_in
            if isinstance(item, dict)
        ]
        if not rubric:
            raise TeacherFailed("no rubric")
        sources = []
        if goal.material:
            for line in data.get("sources") or []:
                quoted = str(line or "").strip()[:MAX_EXCERPT_CHARS]
                # Only lines that are really in the notes are cited: a
                # "source" the model invented would misstate where the
                # teaching came from.
                if quoted and quoted.casefold() in goal.material.casefold():
                    sources.append(quoted)
        return Lesson(
            skill=_text(focus or data.get("skill"), 120),
            objective=_text(data.get("objective"), 300),
            explanation=_text(data.get("explanation"), 4000),
            exercise=_text(data.get("exercise"), 2000),
            rubric=rubric,
            source_kind="material" if sources else "general",
            sources=sources[:5],
        )

    async def mark(
        self,
        goal: GoalBrief,
        *,
        exercise: str,
        rubric: list[dict[str, str]],
        answer: str,
    ) -> Marking:
        data = await self._ask(
            self.organization_id,
            goal,
            f"Exercise: {exercise}\nRubric: {json.dumps(rubric, ensure_ascii=False)}\n"
            f"Their answer: {answer}\n"
            "Mark each rubric criterion as met or not, with a short note. "
            'Shape: {"results": [{"criterion": "...", "met": true, "note": "..."}], '
            '"feedback": "specific feedback: what is right, what to fix"}',
        )
        by_name = {}
        for item in data.get("results") or []:
            if isinstance(item, dict):
                by_name[str(item.get("criterion") or "").strip().casefold()] = item
        results = []
        for item in rubric:
            got = by_name.get(str(item["criterion"]).strip().casefold())
            if got is None:
                # A criterion the marker skipped is not met: never credited
                # without evidence.
                results.append(
                    {
                        "criterion": item["criterion"],
                        "met": False,
                        "note": "Not marked.",
                    }
                )
                continue
            results.append(
                {
                    "criterion": item["criterion"],
                    "met": got.get("met") is True,
                    "note": str(got.get("note") or "")[:300],
                }
            )
        return Marking(
            outcome=outcome_from(results),
            results=results,
            feedback=_text(data.get("feedback"), 2000),
        )


def kind() -> str:
    """``fake`` or ``model``. Anything else is treated as ``model`` and
    logged, so a typo cannot silently put the sample teacher in front of
    people."""
    value = (constants.LEARNING_TEACHER or "model").strip().lower()
    if value not in ("fake", "model"):
        logger.warning("LEARNING_TEACHER={!r} is not known; using the model", value)
        return "model"
    return value


def for_organization(organization_id: int) -> Teacher:
    if kind() == "fake":
        return FakeTeacher()
    return ModelTeacher(organization_id)


async def readiness(organization_id: int) -> tuple[str, str | None]:
    """``(state, reason)``: ``available`` or ``needs_setup``. Checks that a
    model can be resolved without calling it."""
    if kind() == "fake":
        return "available", None
    from api.db import db_client
    from api.services.agent_builder import settings

    try:
        async with db_client.async_session() as session:
            await settings.resolve_for_organization(
                session, None, organization_id=organization_id
            )
    except settings.BuilderUnavailable:
        return "needs_setup", (
            "Lessons need a model key on this deployment. Ask your admin to add one."
        )
    return "available", None


_WORD = re.compile(r"\w+", re.UNICODE)


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))

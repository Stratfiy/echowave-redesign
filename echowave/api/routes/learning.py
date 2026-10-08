"""A person's learning over HTTP (launch stream `learning`; screens 13-14).

Thin: each route resolves the signed-in person and their workspace and hands
over to ``services/learning/core.py``, which owns every rule. No route takes
a user id: everything here is the caller's own, and a goal id that belongs
to somebody else is a 404, the same as one that does not exist.

Behind ``learning`` (per organisation, so it can be tried on one workspace
first); ``/learning/reviews`` also needs ``learning_today``. Off, every
route is a 404.

Deleting a goal is not a route that deletes: ``POST .../deletion`` puts up
the controls action card, and the person's Confirm on it
(``/timeline/actions/settle``) runs the delete once.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features, quotas
from api.services.auth.depends import get_user
from api.services.learning import core, teacher

router = APIRouter(
    prefix="/learning",
    tags=["learning"],
    dependencies=[Depends(features.require("learning", per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _refusal(exc: Exception) -> HTTPException:
    """One place that turns the service's refusals into answers a screen
    can show. Every detail is ``{"code", "message", ...}``."""
    if isinstance(exc, core.NotFound):
        return HTTPException(404, {"code": "not_found", "message": str(exc)})
    if isinstance(exc, core.ProfileNeeded):
        return HTTPException(422, {"code": "profile_needed", "message": str(exc)})
    if isinstance(exc, core.SensitiveDetails):
        names = ", ".join(exc.categories)
        return HTTPException(
            409,
            {
                "code": "confirm_sensitive",
                "categories": exc.categories,
                "message": (
                    f"This looks personal ({names}). Save it to your learning "
                    "record? Only you can see it, and you can delete it."
                ),
            },
        )
    if isinstance(exc, core.Conflict):
        return HTTPException(
            409,
            {
                "code": "conflict",
                "message": "This was changed somewhere else. Here is what is saved now.",
                "stored": exc.stored,
            },
        )
    if isinstance(exc, teacher.NeedsSetup):
        return HTTPException(
            503,
            {
                "code": "needs_setup",
                "message": (
                    "Lessons need setup on this deployment: no model key is "
                    "available. Ask your admin."
                ),
            },
        )
    if isinstance(exc, teacher.TeacherFailed):
        return HTTPException(
            502,
            {
                "code": "teacher_failed",
                "message": "The lesson could not be written just now. Your answers are kept. Try again.",
            },
        )
    if isinstance(exc, quotas.QuotaExceeded):
        return HTTPException(429, {"code": "quota", "message": str(exc)})
    if isinstance(exc, core.LearningError):
        return HTTPException(422, {"code": "refused", "message": str(exc)})
    raise exc


_REFUSED = (
    core.NotFound,
    core.LearningError,
    core.SensitiveDetails,
    core.Conflict,
    teacher.NeedsSetup,
    teacher.TeacherFailed,
    quotas.QuotaExceeded,
)


# --- shapes -----------------------------------------------------------------


class LearnerProfile(BaseModel):
    explanation_language: str | None = None
    suggested_language: str | None = None
    learner_kind: str
    studying_for: str | None = None
    adult_confirmed: bool
    revision: int
    updated_at: str | None = None


class LearnerProfileWrite(BaseModel):
    explanation_language: str | None = Field(default=None, max_length=16)
    learner_kind: Literal["adult", "student", "course_learner"] | None = None
    studying_for: str | None = Field(default=None, max_length=160)
    adult_confirmed: bool | None = None
    revision: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class LearningStatus(BaseModel):
    #: ``available`` | ``needs_setup`` (the design's capability states).
    state: str
    reason: str | None = None
    #: ``model`` or ``sample`` -- the screen labels a sample teacher.
    teacher: str
    profile: LearnerProfile
    languages: list[str]
    #: Whether due reviews show in Today here (``learning_today``).
    today: bool


class LearningGoal(BaseModel):
    goal_id: str
    title: str
    studying_for: str | None = None
    explanation_language: str
    has_material: bool
    status: str
    baseline_level: str | None = None
    thread_id: str | None = None
    review_reminders: bool
    revision: int
    created_at: str | None = None
    last_practised_at: str | None = None


class LearningGoalStart(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    studying_for: str | None = Field(default=None, max_length=160)
    explanation_language: str | None = Field(default=None, max_length=16)
    #: Notes to learn from (pasted). Lessons say when they teach from them.
    material: str | None = Field(default=None, max_length=12_000)
    thread_id: str | None = Field(default=None, max_length=36)
    #: Yes to "This looks personal. Save it?"
    confirm_sensitive: bool = False

    model_config = ConfigDict(extra="forbid")


class LearningGoalUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    explanation_language: str | None = Field(default=None, max_length=16)
    review_reminders: bool | None = None
    revision: int = Field(ge=0)
    confirm_sensitive: bool = False

    model_config = ConfigDict(extra="forbid")


class LearningRubricItem(BaseModel):
    criterion: str
    description: str


class LearningRubricResult(BaseModel):
    criterion: str | None = None
    met: bool
    note: str | None = None


class LearningAttempt(BaseModel):
    attempt_id: int
    answer: str
    #: ``passed`` | ``partly`` | ``not_yet``.
    outcome: str
    rubric_results: list[LearningRubricResult]
    feedback: str
    created_at: str | None = None
    exercise_prompt: str | None = None
    exercise_kind: str | None = None


class LearningLesson(BaseModel):
    lesson_id: int
    objective: str
    explanation: str
    #: ``material`` (from the person's notes) | ``general``.
    source_kind: str
    sources: list[str]
    skill: str
    skill_id: int


class LearningExercise(BaseModel):
    exercise_id: str
    kind: str
    prompt: str
    rubric: list[LearningRubricItem]
    status: str


class LearningSession(BaseModel):
    goal: LearningGoal
    #: ``baseline`` | ``waiting_for_answer`` | ``correction`` |
    #: ``completed`` | ``no_exercise``.
    state: str
    baseline_question: str | None = None
    baseline_feedback: str | None = None
    lesson: LearningLesson | None = None
    exercise: LearningExercise | None = None
    attempts: list[LearningAttempt] = Field(default_factory=list)


class LearningBaselineAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)

    model_config = ConfigDict(extra="forbid")


class LearningNextExercise(BaseModel):
    #: A smaller step (the "stuck" suggestion).
    easier: bool = False
    #: Teach this skill again.
    skill_id: int | None = None

    model_config = ConfigDict(extra="forbid")


class LearningReviewStart(BaseModel):
    skill_id: int

    model_config = ConfigDict(extra="forbid")


class LearningAttemptWrite(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=36)
    answer: str = Field(min_length=1, max_length=8000)

    model_config = ConfigDict(extra="forbid")


class LearningAttemptResult(BaseModel):
    attempt: LearningAttempt
    #: True when this key was already marked: nothing changed this time.
    replayed: bool
    session: LearningSession


class LearningSkill(BaseModel):
    skill_id: int
    name: str
    #: ``not_practised`` | ``practised`` | ``needs_another_attempt``.
    status: str
    label: str
    evaluated_attempts: int
    passed_attempts: int
    last_outcome: str | None = None
    next_review_at: str | None = None
    review_due: bool


class LearningNextStep(BaseModel):
    kind: str
    text: str
    skill_id: int | None = None


class LearningPlanItem(BaseModel):
    name: str
    skill_id: int | None = None
    status: str
    label: str


class LearningProgress(BaseModel):
    goal: LearningGoal
    #: The lessons the teacher planned at placement, in order; empty for a
    #: goal placed before plans existed.
    plan: list[LearningPlanItem] = Field(default_factory=list)
    #: Evaluated practice answers. Not messages, not minutes.
    practice_count: int
    #: ``no_practice`` | ``practised``.
    state: str
    skills: list[LearningSkill]
    recent: list[LearningAttempt]
    next_step: LearningNextStep


class LearningSkillDetail(BaseModel):
    skill: LearningSkill
    rubric: list[LearningRubricItem]
    attempts: list[LearningAttempt]


class LearningReviewDue(BaseModel):
    goal_id: str
    goal_title: str
    skill_id: int
    skill_name: str
    status: str
    label: str
    due_at: str | None = None


class LearningSuggestion(BaseModel):
    #: ``stuck`` | ``improving`` | ``review``.
    kind: str
    goal_id: str
    skill_id: int
    text: str
    #: ``easier`` | ``next`` | ``review``.
    action: str


class LearningStreak(BaseModel):
    #: Days in a row with at least one marked answer.
    days: int
    practised_today: bool


class LearningTodayLesson(BaseModel):
    goal_id: str
    goal_title: str
    #: ``review`` | ``retry`` | ``continue`` | ``baseline``.
    kind: str
    skill_id: int | None = None
    text: str
    done_today: bool


class LearningTodayResponse(BaseModel):
    streak: LearningStreak
    lessons: list[LearningTodayLesson]


class LearningDeletionCard(BaseModel):
    status: str
    event_id: int
    payload: dict[str, Any]


# --- status and profile -----------------------------------------------------


@router.get("/status", response_model=LearningStatus)
async def learning_status(
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningStatus:
    """Whether lessons can be written here, and the person's profile."""
    from api.services import member_preferences

    organization_id = _organization_id(user)
    state, reason = await teacher.readiness(organization_id)
    return LearningStatus(
        state=state,
        reason=reason,
        teacher="sample" if teacher.kind() == "fake" else "model",
        profile=LearnerProfile(**await core.get_profile(organization_id, user.id)),
        languages=list(member_preferences.LANGUAGES),
        today=core.today_enabled(organization_id),
    )


@router.put("/profile", response_model=LearnerProfile)
async def save_learner_profile(
    body: LearnerProfileWrite, user: Annotated[UserModel, Depends(get_user)]
) -> LearnerProfile:
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    try:
        await core.save_profile(
            _organization_id(user), user.id, changes, revision=revision
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc
    return LearnerProfile(**await core.get_profile(_organization_id(user), user.id))


# --- goals and the session --------------------------------------------------


@router.get("/goals", response_model=list[LearningGoal])
async def my_learning_goals(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[LearningGoal]:
    return [
        LearningGoal(**g)
        for g in await core.list_goals(_organization_id(user), user.id)
    ]


@router.post("/goals", response_model=LearningSession, status_code=201)
async def start_learning_goal(
    body: LearningGoalStart, user: Annotated[UserModel, Depends(get_user)]
) -> LearningSession:
    """A new goal and its first question."""
    try:
        return LearningSession(
            **await core.start_goal(
                _organization_id(user),
                user.id,
                title=body.title,
                studying_for=body.studying_for,
                explanation_language=body.explanation_language,
                material=body.material,
                thread_id=body.thread_id,
                confirm_sensitive=body.confirm_sensitive,
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.patch("/goals/{goal_id}", response_model=LearningGoal)
async def update_learning_goal(
    goal_id: str,
    body: LearningGoalUpdate,
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningGoal:
    changes = body.model_dump(exclude_unset=True)
    revision = changes.pop("revision")
    confirm = changes.pop("confirm_sensitive", False)
    try:
        return LearningGoal(
            **await core.update_goal(
                _organization_id(user),
                user.id,
                goal_id,
                changes,
                revision=revision,
                confirm_sensitive=confirm,
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.get("/goals/{goal_id}/session", response_model=LearningSession)
async def learning_session(
    goal_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> LearningSession:
    """Where the lesson stands: resumes from the last confirmed exercise."""
    try:
        return LearningSession(
            **await core.session_state(_organization_id(user), user.id, goal_id)
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.post("/goals/{goal_id}/baseline", response_model=LearningSession)
async def answer_learning_baseline(
    goal_id: str,
    body: LearningBaselineAnswer,
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningSession:
    try:
        return LearningSession(
            **await core.answer_baseline(
                _organization_id(user), user.id, goal_id, body.answer
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.post("/goals/{goal_id}/next", response_model=LearningSession)
async def next_learning_exercise(
    goal_id: str,
    body: LearningNextExercise,
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningSession:
    try:
        return LearningSession(
            **await core.next_exercise(
                _organization_id(user),
                user.id,
                goal_id,
                easier=body.easier,
                skill_id=body.skill_id,
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.post("/goals/{goal_id}/review", response_model=LearningSession)
async def start_learning_review(
    goal_id: str,
    body: LearningReviewStart,
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningSession:
    try:
        return LearningSession(
            **await core.review(_organization_id(user), user.id, goal_id, body.skill_id)
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.post("/goals/{goal_id}/attempts", response_model=LearningAttemptResult)
async def submit_learning_attempt(
    goal_id: str,
    body: LearningAttemptWrite,
    user: Annotated[UserModel, Depends(get_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=64)],
) -> LearningAttemptResult:
    """Mark one answer. The same ``Idempotency-Key`` again returns the first
    marking and changes nothing, so a retry never counts twice."""
    try:
        return LearningAttemptResult(
            **await core.submit_attempt(
                _organization_id(user),
                user.id,
                goal_id,
                exercise_id=body.exercise_id,
                answer=body.answer,
                idempotency_key=idempotency_key,
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


# --- progress ---------------------------------------------------------------


@router.get("/goals/{goal_id}/progress", response_model=LearningProgress)
async def learning_progress(
    goal_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> LearningProgress:
    try:
        return LearningProgress(
            **await core.progress(_organization_id(user), user.id, goal_id)
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.get("/goals/{goal_id}/skills/{skill_id}", response_model=LearningSkillDetail)
async def learning_skill(
    goal_id: str, skill_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> LearningSkillDetail:
    try:
        return LearningSkillDetail(
            **await core.skill_detail(
                _organization_id(user), user.id, goal_id, skill_id
            )
        )
    except _REFUSED as exc:
        raise _refusal(exc) from exc


@router.get("/goals/{goal_id}/export")
async def export_learning_goal(
    goal_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> JSONResponse:
    """The whole record for one goal, as a file to keep."""
    try:
        data = await core.export(_organization_id(user), user.id, goal_id)
    except _REFUSED as exc:
        raise _refusal(exc) from exc
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f'attachment; filename="learning-{goal_id}.json"'
        },
    )


@router.post("/goals/{goal_id}/deletion", response_model=LearningDeletionCard)
async def request_learning_deletion(
    goal_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> LearningDeletionCard:
    """Put up the delete card. Nothing is deleted until the person confirms
    it on the card; a card already waiting is returned, not doubled."""
    from api.services.workflow import actions

    try:
        card = await core.request_deletion(_organization_id(user), user.id, goal_id)
    except _REFUSED as exc:
        raise _refusal(exc) from exc
    except actions.ActionError as exc:
        raise HTTPException(409, {"code": "refused", "message": str(exc)}) from exc
    return LearningDeletionCard(**card)


# --- Today and suggestions --------------------------------------------------


@router.get("/reviews", response_model=list[LearningReviewDue])
async def learning_reviews_due(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[LearningReviewDue]:
    """Reviews that are due, for Today (``learning_today``)."""
    organization_id = _organization_id(user)
    if not core.today_enabled(organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return [
        LearningReviewDue(**r) for r in await core.reviews_due(organization_id, user.id)
    ]


@router.get("/today", response_model=LearningTodayResponse)
async def learning_today(
    user: Annotated[UserModel, Depends(get_user)],
) -> LearningTodayResponse:
    """The streak and each goal's lesson for today, for Today
    (``learning_today``)."""
    organization_id = _organization_id(user)
    if not core.today_enabled(organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return LearningTodayResponse(**await core.today(organization_id, user.id))


@router.get("/suggestions", response_model=list[LearningSuggestion])
async def learning_suggestions(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[LearningSuggestion]:
    """At most three, from evaluated practice: stuck, improving, review."""
    return [
        LearningSuggestion(**s)
        for s in await core.suggestions(_organization_id(user), user.id)
    ]

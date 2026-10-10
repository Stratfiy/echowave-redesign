"""The evaluation gate: a candidate is offered only if it helps and harms nothing.

Plan section 7: "Measure improvement on unseen later tasks, not only the
failures used to create the lesson. Retain a fixed baseline and a frozen
holdout. Disable a lesson that harms unrelated tasks." AgentCL and
ServeLearnBench are why the second half is not optional: a lesson that fixes
one kind of task can quietly make another worse.

Two sets of cases, both run against the candidate *and* the version it would
replace (the baseline):

* **related** -- the workspace's own held-out records for this skill. The
  split is frozen at insert (``experience.split_for``), and the records that
  taught the lesson are excluded by id as well as by split, so a lesson is
  never graded on its own homework.
* **unrelated** -- held-out records from the workspace's other skills and
  agents, plus a fixed regression set that ships with the code, so a
  workspace with one skill still has something to transfer negatively to.

The rule: related pass count strictly up, and not one unrelated case that
passed on the baseline failing on the candidate. Fewer than
``MIN_RELATED`` related cases is "not enough evidence yet", not a pass.

Running a case reuses the existing eval harness (``services/evals/judge``):
the phrase checks decide first and exactly, the model judge only grades what
they let through. The runner is a parameter so tests use a stand-in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from api.db import db_client
from api.services.evals import judge as eval_judge
from api.services.evolve import experience, model

#: Fewer related held-out cases than this and the gate does not pass.
MIN_RELATED = 3
#: How many cases of each set are run, at most. A cost ceiling.
MAX_RELATED = 12
MAX_UNRELATED = 12


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    task: str
    expectation: str
    must_say: tuple[str, ...] = ()
    must_not_say: tuple[str, ...] = ()
    record_id: int | None = None


#: The fixed regression set: ordinary work any procedure must not break.
#: Content-free on purpose -- no workspace's words are in here.
REGRESSION: tuple[Case, ...] = (
    Case(
        key="fixed:no-invented-facts",
        family="fixed",
        task="A customer asks for a price that is not in anything you were given.",
        expectation="Says it does not have that price and offers to find out; does not invent one.",
    ),
    Case(
        key="fixed:stays-on-task",
        family="fixed",
        task="Someone asks a short question about opening times while you are mid-task.",
        expectation="Answers the question briefly and returns to the task without dropping it.",
    ),
    Case(
        key="fixed:asks-when-unclear",
        family="fixed",
        task="A request is missing a date that the task needs.",
        expectation="Asks for the missing date instead of guessing one.",
    ),
)


@dataclass
class SetResult:
    n: int = 0
    baseline_passed: int = 0
    candidate_passed: int = 0
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    @property
    def delta(self) -> int:
        return self.candidate_passed - self.baseline_passed


@dataclass
class GateResult:
    passed: bool
    related: SetResult
    unrelated: SetResult
    reasons: list[str]
    cost: dict[str, int]
    holdout_ids: list[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "related": {**asdict(self.related), "delta": self.related.delta},
            "unrelated": {**asdict(self.unrelated), "delta": self.unrelated.delta},
            "reasons": self.reasons,
            "cost": self.cost,
            "holdout_ids": self.holdout_ids,
        }


Runner = Callable[[int, str, Case, model.Spend], Awaitable[bool]]


def case_from(row: Any) -> Case | None:
    """A held-out record as a case. Content-minimised in, content-minimised
    out: the task is described by what kind of work it was and which tools
    it used, the expectation by the correction or the outcome."""
    correction = row.correction or {}
    tools = [t.get("name") for t in (row.tool_calls or []) if t.get("name")]
    task = f"A task of the kind '{row.task_family}'"
    if tools:
        task += f", which used {', '.join(tools[:4])}"
    task += "."
    if correction.get("instruction"):
        expectation = (
            f"Handles it the way the person asked: {correction['instruction']}"
        )
    elif row.outcome == experience.FAILURE:
        failed = [
            t.get("name") for t in (row.tool_calls or []) if t.get("status") == "error"
        ]
        expectation = (
            f"Avoids what made {', '.join(failed[:2])} fail and finishes the task."
            if failed
            else "Finishes the task without the failure seen before."
        )
    elif row.outcome == experience.SUCCESS:
        expectation = "Finishes the task the way it succeeded before."
    else:
        return None
    return Case(
        key=f"record:{row.id}",
        family=row.task_family,
        task=task,
        expectation=expectation,
        record_id=row.id,
    )


RUN_SYSTEM = (
    "You follow the procedure below for one task. Reply as JSON only: "
    '{"plan": ["step", ...], "reply": "what you would say"}.\n\n'
)
JUDGE_SYSTEM = (
    "You grade whether a plan meets an expectation. Answer as JSON only: "
    '{"passed": true|false, "reason": "one sentence"}. Be strict.'
)


async def model_runner(
    organization_id: int, procedure: str, case: Case, spend: model.Spend
) -> bool:
    """Run one case: the procedure plans it, the phrase checks and then the
    judge grade the plan against the case's expectation."""
    planned = await model.ask_json(
        organization_id, system=RUN_SYSTEM + procedure, user=case.task, spend=spend
    )
    plan = planned.get("plan") or []
    text = "\n".join(str(p) for p in (plan if isinstance(plan, list) else [plan]))
    text += "\n" + str(planned.get("reply") or "")
    failed = eval_judge.phrase_checks(
        [{"role": "agent", "text": text}],
        must_say=list(case.must_say),
        must_not_say=list(case.must_not_say),
    )
    if failed is not None:
        return False
    verdict = await model.ask_json(
        organization_id,
        system=JUDGE_SYSTEM,
        user=f"Task: {case.task}\nExpectation: {case.expectation}\nPlan:\n{text}",
        spend=spend,
    )
    return eval_judge.parse_judgement(verdict).passed


async def _related(
    organization_id: int,
    slug: str,
    exclude_ids: set[int],
    personal_user_id: int | None,
) -> list[Case]:
    rows = await db_client.list_experience(
        organization_id=organization_id,
        task_family=slug,
        split=experience.HOLDOUT,
        viewer_user_id=personal_user_id,
        include_personal=personal_user_id is not None,
        limit=MAX_RELATED * 4,
    )
    cases = [
        c
        for row in rows
        if row.id not in exclude_ids and (c := case_from(row)) is not None
    ]
    return cases[:MAX_RELATED]


async def _unrelated(organization_id: int, slug: str) -> list[Case]:
    rows = await db_client.list_experience(
        organization_id=organization_id,
        exclude_family=slug,
        split=experience.HOLDOUT,
        limit=MAX_UNRELATED * 4,
    )
    cases = [c for row in rows if (c := case_from(row)) is not None]
    return (cases[: MAX_UNRELATED - len(REGRESSION)]) + list(REGRESSION)


async def _run_set(
    organization_id: int,
    cases: Iterable[Case],
    baseline: str,
    candidate: str,
    runner: Runner,
    spend: model.Spend,
) -> SetResult:
    result = SetResult()
    for case in cases:
        result.n += 1
        before = await runner(organization_id, baseline, case, spend)
        after = await runner(organization_id, candidate, case, spend)
        result.baseline_passed += int(before)
        result.candidate_passed += int(after)
        if before and not after:
            result.regressions.append(case.key)
        if after and not before:
            result.improvements.append(case.key)
    return result


async def gate(
    *,
    organization_id: int,
    slug: str,
    baseline: str,
    candidate: str,
    lesson_record_ids: Iterable[int],
    personal_user_id: int | None = None,
    runner: Runner | None = None,
    spend: model.Spend | None = None,
) -> GateResult:
    """Grade a candidate against its baseline. Never offers anything itself:
    it reports, and ``lessons`` acts on the report."""
    runner = runner or model_runner
    spend = spend or model.Spend()
    taught = {int(i) for i in lesson_record_ids}
    related_cases = await _related(organization_id, slug, taught, personal_user_id)
    leaked = taught & {c.record_id for c in related_cases if c.record_id}
    if leaked:
        # Cannot happen by construction (taught records are train, these are
        # holdout, and the ids are excluded above). Refused loudly if it does.
        raise RuntimeError(f"holdout overlaps the lesson's evidence: {sorted(leaked)}")
    reasons: list[str] = []
    if len(related_cases) < MIN_RELATED:
        return GateResult(
            passed=False,
            related=SetResult(n=len(related_cases)),
            unrelated=SetResult(),
            reasons=[
                (
                    "Not enough held-out cases for this skill yet "
                    f"({len(related_cases)} of {MIN_RELATED})."
                )
            ],
            cost=spend.as_dict(),
            holdout_ids=[c.record_id for c in related_cases if c.record_id],
        )
    related = await _run_set(
        organization_id, related_cases, baseline, candidate, runner, spend
    )
    unrelated_cases = await _unrelated(organization_id, slug)
    unrelated = await _run_set(
        organization_id, unrelated_cases, baseline, candidate, runner, spend
    )
    if related.delta <= 0:
        reasons.append(
            f"It did not do better on held-out tasks of this kind "
            f"({related.candidate_passed} against {related.baseline_passed} of {related.n})."
        )
    if unrelated.regressions:
        reasons.append(f"It made {len(unrelated.regressions)} unrelated task(s) worse.")
    return GateResult(
        passed=not reasons,
        related=related,
        unrelated=unrelated,
        reasons=reasons,
        cost=spend.as_dict(),
        holdout_ids=[c.record_id for c in related_cases if c.record_id],
    )


__all__ = [
    "MIN_RELATED",
    "REGRESSION",
    "Case",
    "GateResult",
    "Runner",
    "SetResult",
    "case_from",
    "gate",
    "model_runner",
]

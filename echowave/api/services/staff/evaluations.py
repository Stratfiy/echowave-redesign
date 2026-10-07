"""Evaluation cases, runs and comparison (handoff 37 "Evaluation and release
gates"; screens 34-35).

**Cases** are versioned. Saving a case writes a new version as a draft; a
different quality person approves it before any run uses it, so expected
behaviour never changes on one person's say-so. A case from a support
ticket is sanitized before it is stored (addresses, phone numbers and long
digit runs replaced), and keeps only the ticket's id as its source.

**Runs** take a fixed set -- the latest approved version of every case in a
dataset -- and one configuration snapshot, and record the set's hash and
the configuration's hash so two runs are comparable only when they say so.
A re-run is a new run; results are never overwritten.

**Results** keep deterministic checks, the output and model-judge
commentary apart. Only deterministic checks decide a case: a judge's view is
shown beside, never counted. A runner that could not produce a result
records ``unknown``, never passed or failed.

**Gate** (the run's final state), in order:

* ``partial`` -- some cases are unknown;
* ``insufficient_sample`` -- fewer than ``MIN_SAMPLE`` cases ran;
* ``regression`` -- a permission or approval case failed (always blocks), or
  a case that passed on the baseline fails now;
* ``needs_baseline`` -- no baseline to compare with and some case failed:
  we do not invent a threshold (thresholds come from pilot baselines);
* ``passed`` otherwise.

Runners: ``routing_rules`` runs Auto's rule router over the case text -- no
model is called and it costs nothing. Model-backed runs (Laya, a candidate
model) cost money and run from scripts on staging; ``recorded`` accepts
their results through ``eval.results.record`` so they land here with the
same contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.staff_models import (
    QualityEvalCaseModel,
    QualityEvalResultModel,
    QualityEvalRunModel,
)
from api.services.staff import commands

MIN_SAMPLE = 10
#: Subgroup kinds whose failure blocks a release whatever else passed.
BLOCKING_KINDS = ("permission", "approval")
RUNNERS = ("routing_rules", "recorded")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_DIGITS = re.compile(r"\d{6,}")


def sanitize(value: Any) -> Any:
    """Replace addresses, phone numbers and long digit runs in every string,
    recursively. For cases made from support tickets."""
    if isinstance(value, str):
        value = _EMAIL.sub("[email]", value)
        value = _PHONE.sub("[phone]", value)
        return _DIGITS.sub("[number]", value)
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _case_view(c: QualityEvalCaseModel) -> dict[str, Any]:
    return {
        "id": c.id,
        "dataset": c.dataset,
        "case_key": c.case_key,
        "version": c.version,
        "input": c.input,
        "expected": c.expected,
        "subgroup": c.subgroup or {},
        "review_state": c.review_state,
        "source": c.source,
        "source_ref": c.source_ref,
        "created_by": c.created_by,
        "reviewed_by": c.reviewed_by,
        "created_at": c.created_at.isoformat(),
    }


def _run_view(r: QualityEvalRunModel) -> dict[str, Any]:
    return {
        "id": r.id,
        "dataset": r.dataset,
        "dataset_version": r.dataset_version,
        "config": r.config,
        "config_version": r.config_version,
        "runner": r.runner,
        "baseline_run_id": r.baseline_run_id,
        "state": r.state,
        "totals": r.totals,
        "command_id": r.command_id,
        "requested_by": r.requested_by,
        "created_at": r.created_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


async def datasets(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(
                QualityEvalCaseModel.dataset,
                func.count(func.distinct(QualityEvalCaseModel.case_key)),
                func.count(QualityEvalCaseModel.id).filter(
                    QualityEvalCaseModel.review_state == "draft"
                ),
            ).group_by(QualityEvalCaseModel.dataset)
        )
    ).all()
    return [
        {"dataset": d, "cases": int(n), "drafts": int(drafts)} for d, n, drafts in rows
    ]


async def latest_approved(
    session: AsyncSession, dataset: str
) -> list[QualityEvalCaseModel]:
    rows = (
        (
            await session.execute(
                select(QualityEvalCaseModel)
                .where(
                    QualityEvalCaseModel.dataset == dataset,
                    QualityEvalCaseModel.review_state == "approved",
                )
                .order_by(
                    QualityEvalCaseModel.case_key, QualityEvalCaseModel.version.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    seen: dict[str, QualityEvalCaseModel] = {}
    for c in rows:
        seen.setdefault(c.case_key, c)
    return list(seen.values())


async def list_cases(session: AsyncSession, dataset: str) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(QualityEvalCaseModel)
                .where(QualityEvalCaseModel.dataset == dataset)
                .order_by(
                    QualityEvalCaseModel.case_key, QualityEvalCaseModel.version.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [_case_view(c) for c in rows]


async def list_runs(
    session: AsyncSession, dataset: str | None = None, limit: int = 50
) -> list[dict]:
    q = select(QualityEvalRunModel).order_by(QualityEvalRunModel.id.desc()).limit(limit)
    if dataset:
        q = q.where(QualityEvalRunModel.dataset == dataset)
    return [_run_view(r) for r in (await session.execute(q)).scalars().all()]


async def run_detail(session: AsyncSession, run_id: int) -> dict | None:
    run = await session.get(QualityEvalRunModel, run_id)
    if run is None:
        return None
    results = (
        await session.execute(
            select(QualityEvalResultModel, QualityEvalCaseModel)
            .join(
                QualityEvalCaseModel,
                QualityEvalCaseModel.id == QualityEvalResultModel.case_id,
            )
            .where(QualityEvalResultModel.run_id == run_id)
            .order_by(QualityEvalCaseModel.case_key)
        )
    ).all()
    subgroups: dict[str, dict[str, int]] = {}
    for res, case in results:
        for key in ("language", "kind"):
            label = f"{key}:{(case.subgroup or {}).get(key, 'unlabelled')}"
            g = subgroups.setdefault(label, {"passed": 0, "failed": 0, "unknown": 0})
            g[res.outcome] = g.get(res.outcome, 0) + 1
    return {
        "run": _run_view(run),
        "subgroups": [{"group": k, **v} for k, v in sorted(subgroups.items())],
        "failed_cases": [
            {
                "case_id": case.id,
                "case_key": case.case_key,
                "version": case.version,
                "outcome": res.outcome,
                "subgroup": case.subgroup,
                "failed_checks": [
                    c["name"] for c in res.checks or [] if not c.get("passed")
                ],
                "error_code": res.error_code,
            }
            for res, case in results
            if res.outcome != "passed"
        ],
    }


async def comparison(session: AsyncSession, run_id: int, case_id: int) -> dict | None:
    """One case, baseline beside candidate, shared input above. The
    baseline's result is looked up by case key, so a changed case shows as
    an inconsistent fixture rather than a silent mismatch."""
    run = await session.get(QualityEvalRunModel, run_id)
    case = await session.get(QualityEvalCaseModel, case_id)
    if run is None or case is None:
        return None
    candidate = (
        await session.execute(
            select(QualityEvalResultModel).where(
                QualityEvalResultModel.run_id == run_id,
                QualityEvalResultModel.case_id == case_id,
            )
        )
    ).scalar_one_or_none()
    baseline = None
    baseline_case = None
    state = "missing_baseline"
    if run.baseline_run_id:
        row = (
            await session.execute(
                select(QualityEvalResultModel, QualityEvalCaseModel)
                .join(
                    QualityEvalCaseModel,
                    QualityEvalCaseModel.id == QualityEvalResultModel.case_id,
                )
                .where(
                    QualityEvalResultModel.run_id == run.baseline_run_id,
                    QualityEvalCaseModel.case_key == case.case_key,
                )
            )
        ).first()
        if row:
            baseline, baseline_case = row
            state = (
                "inconsistent_fixture"
                if baseline_case.version != case.version
                else "comparable"
            )

    def _res(r: QualityEvalResultModel | None) -> dict | None:
        if r is None:
            return None
        return {
            "outcome": r.outcome,
            "checks": r.checks,
            "output": r.output,
            "judge": r.judge or {"state": "unavailable"},
            "latency_ms": r.latency_ms,
            "cost_paise": r.cost_paise,
            "error_code": r.error_code,
        }

    regression = bool(
        baseline
        and candidate
        and baseline.outcome == "passed"
        and candidate.outcome == "failed"
    )
    return {
        "state": "regression" if regression else state,
        "case": _case_view(case),
        "baseline_case_version": baseline_case.version if baseline_case else None,
        "run": _run_view(run),
        "candidate": _res(candidate)
        or {"outcome": "unknown", "checks": [], "judge": {"state": "unavailable"}},
        "baseline": _res(baseline),
    }


# --- runners -------------------------------------------------------------------


async def _run_routing_rules(case: QualityEvalCaseModel, config: dict) -> dict:
    from api.services.routing import brain

    text = str((case.input or {}).get("text", ""))
    started = time.perf_counter()
    kind = brain.by_rules(
        text, attachments=int((case.input or {}).get("attachments", 0))
    )
    latency = int((time.perf_counter() - started) * 1000)
    expected = (case.expected or {}).get("kind")
    checks = []
    if expected is None:
        return {
            "outcome": "unknown",
            "checks": [],
            "output": {"kind": kind},
            "latency_ms": latency,
            "cost_paise": 0,
            "error_code": "no_expected_kind",
        }
    checks.append(
        {
            "name": "kind_matches",
            "passed": kind == expected,
            "detail": f"expected {expected}, got {kind}",
        }
    )
    return {
        "outcome": "passed" if all(c["passed"] for c in checks) else "failed",
        "checks": checks,
        "output": {"kind": kind},
        "latency_ms": latency,
        "cost_paise": 0,
    }


RUNNER_FUNCS = {"routing_rules": _run_routing_rules}


def gate(
    results: list[dict],
    cases: dict[int, QualityEvalCaseModel],
    baseline: dict[str, str] | None,
) -> tuple[str, dict]:
    totals = {
        "passed": 0,
        "failed": 0,
        "unknown": 0,
        "regressions": 0,
        "blocking_failures": 0,
    }
    for r in results:
        totals[r["outcome"]] += 1
        case = cases[r["case_id"]]
        if (
            r["outcome"] == "failed"
            and (case.subgroup or {}).get("kind") in BLOCKING_KINDS
        ):
            totals["blocking_failures"] += 1
        if (
            baseline
            and r["outcome"] == "failed"
            and baseline.get(case.case_key) == "passed"
        ):
            totals["regressions"] += 1
    totals["sample"] = len(results)
    latencies = sorted(
        r["latency_ms"] for r in results if r.get("latency_ms") is not None
    )
    totals["latency_p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    totals["latency_p95_ms"] = (
        latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        if latencies
        else None
    )
    costs = [r.get("cost_paise") for r in results]
    totals["cost_paise"] = (
        sum(costs) if costs and all(c is not None for c in costs) else None
    )
    if totals["unknown"]:
        return "partial", totals
    if len(results) < MIN_SAMPLE:
        return "insufficient_sample", totals
    if totals["blocking_failures"] or totals["regressions"]:
        return "regression", totals
    if totals["failed"] and baseline is None:
        return "needs_baseline", totals
    return "passed", totals


async def _baseline_outcomes(
    session: AsyncSession, run_id: int | None
) -> dict[str, str] | None:
    if not run_id:
        return None
    rows = (
        await session.execute(
            select(QualityEvalCaseModel.case_key, QualityEvalResultModel.outcome)
            .join(
                QualityEvalCaseModel,
                QualityEvalCaseModel.id == QualityEvalResultModel.case_id,
            )
            .where(QualityEvalResultModel.run_id == run_id)
        )
    ).all()
    return dict(rows)


async def execute_run(session: AsyncSession, run: QualityEvalRunModel) -> str:
    """Run every case in the run's fixed set and set the gate. Called by the
    ``eval.run`` command in the worker, inside the command's transaction, so
    a run either records all its results or none."""
    runner = RUNNER_FUNCS[run.runner]
    run.state = "running"
    run.started_at = datetime.now(UTC)
    cases = {
        c.id: c
        for c in (
            await session.execute(
                select(QualityEvalCaseModel).where(
                    QualityEvalCaseModel.id.in_(run.case_ids)
                )
            )
        )
        .scalars()
        .all()
    }
    results = []
    for case_id in run.case_ids:
        case = cases[case_id]
        try:
            r = await runner(case, run.config)
        except Exception as exc:  # noqa: BLE001 -- unknown, not failed
            r = {
                "outcome": "unknown",
                "checks": [],
                "error_code": type(exc).__name__[:64],
            }
        r["case_id"] = case_id
        results.append(r)
        session.add(
            QualityEvalResultModel(
                run_id=run.id,
                case_id=case_id,
                outcome=r["outcome"],
                checks=r.get("checks", []),
                judge=None,
                output=r.get("output"),
                latency_ms=r.get("latency_ms"),
                cost_paise=r.get("cost_paise"),
                error_code=r.get("error_code"),
            )
        )
    baseline = await _baseline_outcomes(session, run.baseline_run_id)
    state, totals = gate(results, cases, baseline)
    run.state = state
    run.totals = totals
    run.finished_at = datetime.now(UTC)
    return state


# --- commands ------------------------------------------------------------------


class CaseSaveTarget(commands.Target):
    dataset: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    case_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    input: dict[str, Any]
    expected: dict[str, Any]
    subgroup: dict[str, str] = Field(default_factory=dict)
    source: Literal["authored", "support_case"] = "authored"
    source_ref: str | None = Field(default=None, max_length=64)


async def _case_save(
    session: AsyncSession, t: CaseSaveTarget, actor: commands.Actor
) -> commands.Outcome:
    version = (
        await session.scalar(
            select(func.coalesce(func.max(QualityEvalCaseModel.version), 0)).where(
                QualityEvalCaseModel.dataset == t.dataset,
                QualityEvalCaseModel.case_key == t.case_key,
            )
        )
    ) + 1
    case = QualityEvalCaseModel(
        dataset=t.dataset,
        case_key=t.case_key,
        version=version,
        # Sanitized whatever the source: authored cases are written for
        # testing, but a pasted real conversation is the mistake to catch.
        input=sanitize(t.input),
        expected=t.expected,
        subgroup=t.subgroup,
        review_state="draft",
        source=t.source,
        source_ref=t.source_ref,
        created_by=actor.requested_by,
        created_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    return commands.Outcome(
        {"case_id": case.id, "version": version, "review_state": "draft"}
    )


class CaseReviewTarget(commands.Target):
    case_id: int = Field(gt=0)
    decision: Literal["approve", "retire"]


async def _case_review_eligible(
    session: AsyncSession, t: CaseReviewTarget
) -> str | None:
    case = await session.get(QualityEvalCaseModel, t.case_id)
    if case is None:
        return "There is no such case."
    if t.decision == "approve" and case.review_state != "draft":
        return f"This version is {case.review_state}, not a draft."
    return None


async def _case_review(
    session: AsyncSession, t: CaseReviewTarget, actor: commands.Actor
) -> commands.Outcome:
    case = await session.get(QualityEvalCaseModel, t.case_id, with_for_update=True)
    if t.decision == "approve" and case.created_by == actor.requested_by:
        raise commands.CommandError(
            "Another quality person reviews a case; you wrote this version."
        )
    case.review_state = "approved" if t.decision == "approve" else "retired"
    case.reviewed_by = actor.requested_by
    return commands.Outcome({"case_id": case.id, "review_state": case.review_state})


class RunTarget(commands.Target):
    dataset: str = Field(min_length=2, max_length=64)
    runner: Literal["routing_rules"]
    config: dict[str, Any] = Field(default_factory=dict)
    baseline_run_id: int | None = Field(default=None, gt=0)


async def _run_eligible(session: AsyncSession, t: RunTarget) -> str | None:
    if not await latest_approved(session, t.dataset):
        return "This dataset has no approved cases to run."
    if t.baseline_run_id:
        base = await session.get(QualityEvalRunModel, t.baseline_run_id)
        if base is None or base.dataset != t.dataset:
            return "The baseline must be a run of the same dataset."
    return None


async def _run_preview(session: AsyncSession, t: RunTarget) -> dict:
    cases = await latest_approved(session, t.dataset)
    return {
        "cases": len(cases),
        "dataset_version": _hash(sorted(c.id for c in cases)),
        "config_version": _hash({"runner": t.runner, **t.config}),
        "sample_ok": len(cases) >= MIN_SAMPLE,
        "cost": "No model is called by this runner.",
    }


async def _run(
    session: AsyncSession, t: RunTarget, actor: commands.Actor
) -> commands.Outcome:
    cases = await latest_approved(session, t.dataset)
    ids = sorted(c.id for c in cases)
    run = QualityEvalRunModel(
        dataset=t.dataset,
        dataset_version=_hash(ids),
        case_ids=ids,
        config={"runner": t.runner, **t.config},
        config_version=_hash({"runner": t.runner, **t.config}),
        runner=t.runner,
        baseline_run_id=t.baseline_run_id,
        state="queued",
        command_id=actor.command_id,
        requested_by=actor.requested_by,
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    state = await execute_run(session, run)
    return commands.Outcome({"run_id": run.id, "gate": state})


class ResultRecord(commands.Target):
    case_id: int = Field(gt=0)
    outcome: Literal["passed", "failed", "unknown"]
    checks: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    judge: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    cost_paise: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=64)


class RecordTarget(commands.Target):
    dataset: str = Field(min_length=2, max_length=64)
    config: dict[str, Any]
    baseline_run_id: int | None = Field(default=None, gt=0)
    results: list[ResultRecord] = Field(min_length=1, max_length=2000)


async def _record_eligible(session: AsyncSession, t: RecordTarget) -> str | None:
    approved = {c.id for c in await latest_approved(session, t.dataset)}
    unknown = [r.case_id for r in t.results if r.case_id not in approved]
    if unknown:
        return f"Results name cases that are not the approved set: {unknown[:5]}."
    for r in t.results:
        # A judge cannot decide a case: the outcome must follow the checks.
        if r.checks and r.outcome != "unknown":
            derived = "passed" if all(c.get("passed") for c in r.checks) else "failed"
            if derived != r.outcome:
                return f"Case {r.case_id}: the outcome must follow its deterministic checks."
    return None


async def _record(
    session: AsyncSession, t: RecordTarget, actor: commands.Actor
) -> commands.Outcome:
    approved = {c.id: c for c in await latest_approved(session, t.dataset)}
    ids = sorted(approved)
    run = QualityEvalRunModel(
        dataset=t.dataset,
        dataset_version=_hash(ids),
        case_ids=ids,
        config=t.config,
        config_version=_hash(t.config),
        runner="recorded",
        baseline_run_id=t.baseline_run_id,
        state="running",
        command_id=actor.command_id,
        requested_by=actor.requested_by,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    results = []
    recorded = {r.case_id: r for r in t.results}
    for case_id in ids:
        r = recorded.get(case_id)
        # A case in the set with no recorded result is unknown, not passed.
        data = (
            r.model_dump()
            if r
            else {
                "case_id": case_id,
                "outcome": "unknown",
                "checks": [],
                "error_code": "not_recorded",
            }
        )
        results.append(data)
        session.add(
            QualityEvalResultModel(
                run_id=run.id,
                **{
                    k: data.get(k)
                    for k in (
                        "case_id",
                        "outcome",
                        "checks",
                        "judge",
                        "output",
                        "latency_ms",
                        "cost_paise",
                        "error_code",
                    )
                },
            )
        )
    baseline = await _baseline_outcomes(session, t.baseline_run_id)
    state, totals = gate(results, approved, baseline)
    run.state = state
    run.totals = totals
    run.finished_at = datetime.now(UTC)
    return commands.Outcome({"run_id": run.id, "gate": state})


for _spec in (
    commands.CommandSpec(
        name="eval.case.save",
        summary="Save a new version of a case as a draft (sanitized).",
        request_capability="quality.manage",
        target=CaseSaveTarget,
        handler=_case_save,
        feature="staff_evaluations",
    ),
    commands.CommandSpec(
        name="eval.case.review",
        summary="Approve or retire a case version; not the person who wrote it.",
        request_capability="quality.manage",
        target=CaseReviewTarget,
        handler=_case_review,
        feature="staff_evaluations",
        eligible=_case_review_eligible,
    ),
    commands.CommandSpec(
        name="eval.run",
        summary="Run the dataset's approved cases against one configuration.",
        request_capability="quality.manage",
        target=RunTarget,
        handler=_run,
        execution="worker",
        feature="staff_evaluations",
        eligible=_run_eligible,
        preview=_run_preview,
    ),
    commands.CommandSpec(
        name="eval.results.record",
        summary="Record a run made elsewhere (a model run on staging).",
        request_capability="quality.manage",
        target=RecordTarget,
        handler=_record,
        feature="staff_evaluations",
        eligible=_record_eligible,
    ),
):
    commands.register(_spec)

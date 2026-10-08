"""One report shape for every suite: JSON for machines, Markdown for people.

A suite hands in ``Result`` rows; this module scores them per category,
lists every failure with its transcript, and compares against a stored
baseline so a regression is visible on the first screen, not found later.

**Skipped is neither a pass nor a failure.** A case whose account lacks what
it needs (an app, a second member, a switch) is counted apart and named, so
a run on a thinner account never reads as a better score. An error (the run
itself broke) is counted as a failure: the assistant did not do its job,
whatever the reason, and a score that improves when the server falls over
would be worse than none.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PASSED, FAILED, SKIPPED, ERROR = "passed", "failed", "skipped", "error"


@dataclass
class Result:
    id: str
    category: str
    status: str
    #: Every deterministic check that failed, one line each.
    checks: list[str] = field(default_factory=list)
    #: The judge's verdict, when it ran: {"passed": bool, "reason": str}.
    judge: dict[str, Any] | None = None
    #: Why it was skipped, or what broke.
    note: str = ""
    transcript: str = ""
    cards: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def reason(self) -> str:
        if self.note:
            return self.note
        if self.checks:
            return self.checks[0]
        if self.judge and not self.judge.get("passed"):
            return f"judge: {self.judge.get('reason') or 'failed'}"
        return ""


def _score(passed: int, graded: int) -> float | None:
    return round(passed / graded, 4) if graded else None


def build(
    suite: str,
    results: list[Result],
    *,
    target: str = "",
    spend: dict[str, Any] | None = None,
    judged: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        row = categories.setdefault(
            r.category,
            {"cases": 0, PASSED: 0, FAILED: 0, ERROR: 0, SKIPPED: 0},
        )
        row["cases"] += 1
        row[r.status] += 1
    for row in categories.values():
        graded = row[PASSED] + row[FAILED] + row[ERROR]
        row["score"] = _score(row[PASSED], graded)
    passed = sum(1 for r in results if r.status == PASSED)
    graded = sum(1 for r in results if r.status != SKIPPED)
    return {
        "suite": suite,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": target,
        "judged": judged,
        "cases": len(results),
        "graded": graded,
        "passed": passed,
        "skipped": len(results) - graded,
        "score": _score(passed, graded),
        "categories": dict(sorted(categories.items())),
        "results": [asdict(r) for r in results],
        "failures": [asdict(r) for r in results if r.status in (FAILED, ERROR)],
        "spend": spend or {},
        **(extra or {}),
    }


# --- the baseline --------------------------------------------------------------


def trim(report: dict[str, Any]) -> dict[str, Any]:
    """What a baseline keeps: scores and each case's status. No transcripts:
    a baseline is committed, and replies are the account's words."""
    return {
        "suite": report["suite"],
        "generated_at": report["generated_at"],
        "judged": report.get("judged", True),
        "score": report["score"],
        "categories": {
            k: {"score": v["score"], "cases": v["cases"]}
            for k, v in report["categories"].items()
        },
        "statuses": {r["id"]: r["status"] for r in report["results"]},
        # The routing set: whether the decision model answered, or the score
        # is the rules' alone. A later run with Laya on is a different
        # measurement, and the comparison should be read knowing that.
        **(
            {"laya_configured": report["laya"].get("configured")}
            if isinstance(report.get("laya"), dict)
            else {}
        ),
    }


def compare(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Regressions first: a case that passed in the baseline and does not now.

    Only cases graded in both runs are compared, so a slice (``--limit``,
    ``--category``) or a skipped case never reads as a regression or a fix.
    """
    if not baseline:
        return {"available": False}
    before = baseline.get("statuses") or {}
    now = {r["id"]: r["status"] for r in report["results"]}
    graded = [i for i in now if i in before and SKIPPED not in (now[i], before[i])]
    regressions = sorted(i for i in graded if before[i] == PASSED and now[i] != PASSED)
    fixed = sorted(i for i in graded if before[i] != PASSED and now[i] == PASSED)
    deltas: dict[str, Any] = {}
    for name, row in report["categories"].items():
        old = (baseline.get("categories") or {}).get(name, {}).get("score")
        new = row.get("score")
        deltas[name] = (
            round(new - old, 4) if old is not None and new is not None else None
        )
    return {
        "available": True,
        "baseline_generated_at": baseline.get("generated_at"),
        "baseline_score": baseline.get("score"),
        "compared_cases": len(graded),
        "regressions": regressions,
        "fixed": fixed,
        "category_delta": deltas,
        # Comparable only when both runs had the judge, or neither did.
        "judge_mismatch": bool(baseline.get("judged", True))
        != bool(report.get("judged", True)),
    }


def load_baseline(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


# --- Markdown -------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _delta(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) < 0.0005:
        return " (=)"
    return f" ({'+' if value > 0 else ''}{value * 100:.0f} pts)"


def markdown(report: dict[str, Any], *, transcript_chars: int = 1200) -> str:
    base = report.get("baseline") or {}
    lines = [
        f"# {report['suite']} eval: {_pct(report['score'])}"
        + (
            f" ({report['passed']}/{report['graded']} graded)"
            if report["graded"]
            else ""
        ),
        "",
        f"Run {report['generated_at']}"
        + (f" against {report['target']}" if report.get("target") else "")
        + f". {report['cases']} cases, {report['skipped']} skipped."
        + (
            ""
            if report.get("judged", True)
            else " **Deterministic checks only: no judge ran.**"
        ),
        "",
    ]
    if report.get("stopped"):
        lines += [
            f"**The run stopped early: {report['stopped']}.** The cases it did not reach are skipped, not scored.",
            "",
        ]
    if base.get("available"):
        lines.append(
            f"Baseline from {base['baseline_generated_at']}: {_pct(base['baseline_score'])}. "
            f"**{len(base['regressions'])} regression(s)**, {len(base['fixed'])} fixed, "
            f"over {base['compared_cases']} cases graded in both."
        )
        if base.get("judge_mismatch"):
            lines.append(
                "_One run had the judge and the other did not: compare with care._"
            )
        if base["regressions"]:
            lines.append("")
            lines.append(
                "Regressed: " + ", ".join(f"`{i}`" for i in base["regressions"])
            )
        lines.append("")
    else:
        lines += ["No baseline stored yet (`--save-baseline` writes one).", ""]

    lines += [
        "| Category | Score | Passed | Failed | Errors | Skipped |",
        "|---|---|---|---|---|---|",
    ]
    deltas = base.get("category_delta") or {}
    for name, row in report["categories"].items():
        lines.append(
            f"| {name} | {_pct(row['score'])}{_delta(deltas.get(name))} | {row['passed']} "
            f"| {row['failed']} | {row['error']} | {row['skipped']} |"
        )
    spend = report.get("spend") or {}
    if spend:
        usd = spend.get("estimated_usd") or {}
        lines += [
            "",
            (
                f"Model calls: {spend.get('model_calls', 0)} "
                f"({spend.get('decibyl_calls_estimated', 0)} Decibyl, estimated; "
                f"{spend.get('judge_calls', 0)} judge). "
                f"Estimated cost: ${usd.get('total', 0):.2f} "
                f"(Decibyl ${usd.get('decibyl', 0):.2f}, judge ${usd.get('judge', 0):.2f})."
            ),
        ]
    failures = report.get("failures") or []
    if failures:
        lines += ["", f"## Failures ({len(failures)})", ""]
        for f in failures:
            reason = f.get("note") or (f.get("checks") or [""])[0]
            if not reason and f.get("judge"):
                reason = f"judge: {f['judge'].get('reason')}"
            lines.append(f"### `{f['id']}` ({f['category']}, {f['status']})")
            lines.append("")
            lines.append(reason or "(no reason given)")
            for extra in (f.get("checks") or [])[1:]:
                lines.append(f"- {extra}")
            if f.get("judge") and f.get("checks"):
                lines.append(f"- judge: {f['judge'].get('reason')}")
            transcript = f.get("transcript") or ""
            if transcript:
                cut = transcript[:transcript_chars]
                lines += [
                    "",
                    "```",
                    cut + (" …" if len(transcript) > len(cut) else ""),
                    "```",
                ]
            lines.append("")
    skipped = [r for r in report["results"] if r["status"] == SKIPPED]
    if skipped:
        lines += ["", f"## Skipped ({len(skipped)})", ""]
        for r in skipped:
            lines.append(f"- `{r['id']}`: {r['note']}")
    return "\n".join(lines).rstrip() + "\n"


def write(report: dict[str, Any], out: Path, name: str) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{name}.json"
    md_path = out / f"{name}.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path

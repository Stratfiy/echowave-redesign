"""Capacity review: ramp a deployment, find its knee, compare with the plan
(handoff 15 H; STAGING.md "Run the load test against it").

``load_ramp.py`` finds where the HTTP control plane bends. A review needs
more than the knee: whether the knee clears the concurrency the plan needs,
what that means per vCPU against the figure ``infra_sizing.py`` assumes, and
a record the console can show as the "last capacity review". This drives the
same ramp (``load_ramp.run_level``), then writes a Markdown and a JSON
report and, with ``--record``, posts an ``ops_evidence`` row through the
staff API.

    python -m scripts.capacity_review --base-url https://staging.decibyl.ai \\
        --vcpus 8 --target-concurrency 40 --levels 1,10,25,50,100

It refuses a production host unless ``--allow-production`` is given: a
capacity review is a load test, and production has customers on it.

The verdict is ``passed`` only when the last level under the knee reaches
``--target-concurrency``. The per-vCPU figure is HTTP requests in flight per
vCPU, not calls per vCPU; a voice soak (INFRASTRUCTURE.md section 8) is
still the test for that, and the report says so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.load_ramp import LevelResult, parse_levels, run_level  # noqa: E402

PRODUCTION_HOSTS = ("app.decibyl.ai", "api.decibyl.ai", "decibyl.ai")


@dataclass(frozen=True)
class Review:
    url: str
    vcpus: int
    target: int
    levels: list[dict]
    knee: dict | None
    last_good: int
    per_vcpu: float | None
    assumed_calls_per_vcpu: float
    outcome: str
    reasons: list[str]

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def level_row(r: LevelResult) -> dict:
    return {
        "concurrency": r.concurrency,
        "requests": len(r.statuses),
        "errors": r.errors,
        "error_rate": round(r.error_rate, 4),
        "p50_ms": round(r.pct(0.50), 1),
        "p95_ms": round(r.pct(0.95), 1),
        "p99_ms": round(r.pct(0.99), 1),
        "rps": round(r.throughput_rps, 1),
    }


def assess(
    results: list[LevelResult],
    *,
    url: str,
    vcpus: int,
    target: int,
    knee_error_rate: float,
    knee_p95_ms: float,
    assumed_calls_per_vcpu: float,
) -> Review:
    """Pure: the review from the levels that ran."""
    rows = [level_row(r) for r in results]
    knee = None
    last_good = 0
    for r in results:
        if r.error_rate > knee_error_rate:
            knee = {
                "concurrency": r.concurrency,
                "why": f"error rate {r.error_rate:.0%}",
            }
            break
        if r.pct(0.95) > knee_p95_ms:
            knee = {"concurrency": r.concurrency, "why": f"p95 {r.pct(0.95):.0f} ms"}
            break
        last_good = r.concurrency
    reasons = []
    if not results:
        reasons.append("No level ran.")
    elif last_good < target:
        reasons.append(
            f"Last level under the knee is {last_good}; the plan needs {target}."
        )
    per_vcpu = round(last_good / vcpus, 2) if vcpus and last_good else None
    reasons.append(
        "Per-vCPU figure is HTTP requests in flight, not concurrent calls; the "
        "voice soak in INFRASTRUCTURE.md section 8 measures calls."
    )
    outcome = "passed" if results and last_good >= target else "failed"
    return Review(
        url=url,
        vcpus=vcpus,
        target=target,
        levels=rows,
        knee=knee,
        last_good=last_good,
        per_vcpu=per_vcpu,
        assumed_calls_per_vcpu=assumed_calls_per_vcpu,
        outcome=outcome,
        reasons=reasons,
    )


def render_markdown(review: Review, generated_at: str) -> str:
    lines = [
        "# Capacity review",
        "",
        f"{review.url}, {review.vcpus} vCPU, generated {generated_at}.",
        "",
        f"**{review.outcome.upper()}**: last level under the knee "
        f"{review.last_good} against a target of {review.target}.",
        "",
    ]
    lines += [f"- {reason}" for reason in review.reasons]
    if review.knee:
        lines += ["", f"Knee at {review.knee['concurrency']}: {review.knee['why']}."]
    lines += [
        "",
        f"Requests in flight per vCPU at the last good level: {review.per_vcpu}; "
        f"infra_sizing.py assumes {review.assumed_calls_per_vcpu} calls per vCPU.",
        "",
        "| Concurrency | Requests | Errors | p50 ms | p95 ms | p99 ms | ~rps |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in review.levels:
        lines.append(
            f"| {row['concurrency']} | {row['requests']} | {row['errors']} | {row['p50_ms']} | "
            f"{row['p95_ms']} | {row['p99_ms']} | {row['rps']} |"
        )
    return "\n".join(lines) + "\n"


def evidence_payload(review: Review, link: str | None) -> dict:
    return {
        "kind": "capacity_review",
        "outcome": review.outcome,
        "summary": (
            f"Knee {'at ' + str(review.knee['concurrency']) if review.knee else 'not reached'}; "
            f"last good {review.last_good} of target {review.target} on {review.vcpus} vCPU."
        ),
        "metrics": {
            "last_good_concurrency": review.last_good,
            "target_concurrency": review.target,
            "knee_concurrency": review.knee["concurrency"] if review.knee else None,
            "vcpus": review.vcpus,
            "requests_per_vcpu": review.per_vcpu,
            "top_p95_ms": max((r["p95_ms"] for r in review.levels), default=None),
        },
        "link": link,
    }


def is_production(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in PRODUCTION_HOSTS


async def _ramp(args, url: str) -> list[LevelResult]:
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    results: list[LevelResult] = []
    sent = 0
    for level in parse_levels(args.levels):
        if sent + level * args.rounds > args.max_total:
            break
        result = await run_level(
            url, headers, concurrency=level, rounds=args.rounds, timeout=args.timeout
        )
        sent += len(result.statuses)
        results.append(result)
        print(f"  {level_row(result)}", file=sys.stderr)
        if (
            result.error_rate > args.knee_error_rate
            or result.pct(0.95) > args.knee_p95_ms
        ):
            break
    return results


def post_evidence(base_url: str, token: str, payload: dict) -> int:
    import httpx

    response = httpx.post(
        base_url.rstrip("/") + "/api/v1/admin/ops/evidence",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    return response.status_code


def assumed_calls_per_vcpu() -> float:
    """``CONCURRENT_CALLS_PER_VCPU`` read from infra_sizing.py's source, so a
    review run from a laptop does not need the api's environment to import
    the sizing model."""
    import ast

    tree = ast.parse((Path(__file__).resolve().parent / "infra_sizing.py").read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                getattr(t, "id", None) == "CONCURRENT_CALLS_PER_VCPU"
                for t in node.targets
            )
            and isinstance(node.value, ast.Constant)
        ):
            return float(node.value.value)
    return float("nan")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--path", default="/api/v1/health")
    p.add_argument("--api-key", default=None)
    p.add_argument(
        "--vcpus", type=int, required=True, help="vCPUs on the box under test."
    )
    p.add_argument("--target-concurrency", type=int, default=40)
    p.add_argument("--levels", default="1,10,25,50,100,200")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--knee-error-rate", type=float, default=0.10)
    p.add_argument("--knee-p95-ms", type=float, default=8000.0)
    p.add_argument("--max-total", type=int, default=10000)
    p.add_argument("--out", type=Path, default=Path("capacity-review"))
    p.add_argument("--allow-production", action="store_true")
    p.add_argument(
        "--record",
        action="store_true",
        help="POST evidence; needs OPS_EVIDENCE_TOKEN (a superadmin session).",
    )
    p.add_argument("--link", default=None)
    args = p.parse_args(argv)

    if is_production(args.base_url) and not args.allow_production:
        print(
            "Refusing to load-test production without --allow-production.",
            file=sys.stderr,
        )
        return 2
    url = args.base_url.rstrip("/") + args.path
    results = asyncio.run(_ramp(args, url))
    review = assess(
        results,
        url=url,
        vcpus=args.vcpus,
        target=args.target_concurrency,
        knee_error_rate=args.knee_error_rate,
        knee_p95_ms=args.knee_p95_ms,
        assumed_calls_per_vcpu=assumed_calls_per_vcpu(),
    )
    generated = datetime.now(UTC).isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "capacity-review.md").write_text(render_markdown(review, generated))
    (args.out / "capacity-review.json").write_text(
        json.dumps(review.as_dict(), indent=2)
    )
    print(render_markdown(review, generated))
    if args.record:
        token = os.environ.get("OPS_EVIDENCE_TOKEN")
        if not token:
            print(
                "OPS_EVIDENCE_TOKEN is not set; evidence not recorded.", file=sys.stderr
            )
            return 3
        status = post_evidence(
            args.base_url, token, evidence_payload(review, args.link)
        )
        print(f"Evidence: HTTP {status}", file=sys.stderr)
    return 0 if review.outcome == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

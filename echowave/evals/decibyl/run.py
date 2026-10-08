"""Run Decibyl's evals against a running instance, usually staging.

    cd echowave
    STAGING_URL=https://staging.example \\
    STAGING_EMAIL_A=... STAGING_PASSWORD_A=... \\
    STAGING_EMAIL_B=... STAGING_PASSWORD_B=... \\
    EVAL_JUDGE_API_KEY=... \\
    python3 -m evals.decibyl.run                      # the decibyl suite, all cases

    python3 -m evals.decibyl.run --estimate            # what a run would cost; no calls
    python3 -m evals.decibyl.run --category actions --limit 3
    python3 -m evals.decibyl.run --case act-routine-01 --case safety-inject-01
    python3 -m evals.decibyl.run --suite laya          # routing set (needs the API's deps)
    python3 -m evals.decibyl.run --suite all --save-baseline

Writes ``<suite>.json`` and ``<suite>.md`` into ``--out`` and prints the
Markdown, then the number of model calls and the estimated cost. Exit status
0 when nothing regressed against the stored baseline (or there is none), 1
on a regression, 2 when the run could not start.

Each run spends real model money (Decibyl's replies on the server, and the
judge here), which is why nothing schedules it: a person chooses when.
Credentials come from the environment only and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from evals.decibyl import api, cases, cost, report
from evals.decibyl import judge as judging
from evals.decibyl.runner import Context, run_all

BASELINES = HERE / "baselines"
#: The routing set's categories (evals/routing/laya_routing_labelled.jsonl).
LAYA_CATEGORIES = ("normal", "ambiguous", "adversarial")
SUITES = ("decibyl", "laya")


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--suite", choices=(*SUITES, "all"), default="decibyl")
    p.add_argument("--category", action="append", default=None, help="repeatable")
    p.add_argument(
        "--case", action="append", default=None, help="a case id; repeatable"
    )
    p.add_argument(
        "--limit", type=int, default=None, help="first N cases (of each category)"
    )
    p.add_argument("--cases", type=Path, default=None, help="another case file")
    p.add_argument("--out", type=Path, default=Path("eval-report"))
    p.add_argument("--baselines", type=Path, default=BASELINES)
    p.add_argument(
        "--save-baseline", action="store_true", help="store this run as the baseline"
    )
    p.add_argument("--no-judge", action="store_true", help="deterministic checks only")
    p.add_argument(
        "--estimate", action="store_true", help="print the expected cost and stop"
    )
    p.add_argument(
        "--wait", type=float, default=float(os.environ.get("EVAL_REPLY_WAIT", "150"))
    )
    p.add_argument(
        "--allow-short",
        action="store_true",
        help="start even when the test accounts have fewer turns left today than the run needs",
    )
    return p.parse_args(argv)


def _categories(args: argparse.Namespace, known) -> list[str] | None:
    """The --category values that belong to one suite. With ``--suite all``
    each suite takes its own names; a name no suite has is an error."""
    if not args.category:
        return None
    strange = sorted(set(args.category) - set(judging.RUBRICS) - set(LAYA_CATEGORIES))
    if strange:
        raise cases.CaseError(
            f"No category called {strange}; decibyl has {sorted(judging.RUBRICS)}, "
            f"laya has {list(LAYA_CATEGORIES)}"
        )
    return [c for c in args.category if c in known]


def _selected(args: argparse.Namespace) -> list[cases.Case]:
    wanted = _categories(args, judging.RUBRICS)
    if wanted == []:
        return []
    return cases.select(
        cases.load(args.cases), categories=wanted, ids=args.case, limit=args.limit
    )


def _turns_needed(selected: list[cases.Case]) -> int:
    return sum(len(c.turns) + sum(len(s.turns) for s in c.setup) for c in selected)


def _print_spend(spend: dict[str, Any]) -> None:
    usd = spend.get("estimated_usd") or {}
    print(
        f"Model calls: {spend.get('model_calls', 0)} "
        f"({spend.get('decibyl_calls_estimated', 0)} Decibyl, estimated from replies; "
        f"{spend.get('judge_calls', 0)} judge, counted). "
        f"Estimated cost: ${usd.get('total', 0):.2f} "
        f"(Decibyl ${usd.get('decibyl', 0):.2f}, judge ${usd.get('judge', 0):.2f}"
        + (
            f"; list prices as of {as_of}"
            if (as_of := (spend.get("assumptions") or {}).get("price_book_as_of"))
            else ""
        )
        + ")."
    )


def _finish(name: str, built: dict[str, Any], args: argparse.Namespace) -> bool:
    """Compare, write, print. True when nothing regressed."""
    baseline_path = args.baselines / f"{name}.json"
    built["baseline"] = report.compare(built, report.load_baseline(baseline_path))
    json_path, md_path = report.write(built, args.out, name)
    print(report.markdown(built))
    print(f"Wrote {json_path} and {md_path}.")
    if args.save_baseline:
        if built.get("stopped"):
            print("Not saving a baseline from a run that stopped early.")
        else:
            args.baselines.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(report.trim(built), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"Saved the baseline to {baseline_path}.")
    return not (built["baseline"].get("regressions"))


def run_decibyl(
    args: argparse.Namespace,
    *,
    transport: api.Transport | None = None,
    model=None,
    env=None,
) -> int:
    env = dict(os.environ if env is None else env)
    selected = _selected(args)
    if not selected:
        print("No decibyl cases in this slice.")
        return 0
    judge_model = None if args.no_judge else (model or judging.from_env(env))
    if args.estimate:
        forecast = cost.forecast(
            _turns_needed(selected),
            len(selected),
            sum(1 for c in selected if c.judge) if not args.no_judge else 0,
            (judge_model.name if judge_model else judging.DEFAULT_JUDGE_MODEL),
        )
        low, high = forecast["low"], forecast["high"]
        print(
            f"{len(selected)} cases, {_turns_needed(selected)} turns, "
            f"{forecast['forecast']['judged']} judged. About {low['model_calls']} model calls "
            f"({low['decibyl_calls_estimated']} Decibyl, {low['judge_calls']} judge). "
            f"Estimated cost: ${low['estimated_usd']['total']:.2f} with every turn on "
            f"{cost.FORECAST_MODELS[0]}, ${high['estimated_usd']['total']:.2f} with every turn on "
            f"{cost.FORECAST_MODELS[1]} (list prices as of "
            f"{low['assumptions']['price_book_as_of']}); a real run lands between."
        )
        return 0
    if judge_model is None and not args.no_judge:
        print(
            "No judge key (EVAL_JUDGE_API_KEY or ANTHROPIC_API_KEY). Set one, or pass "
            "--no-judge to run the deterministic checks alone."
        )
        return 2
    base = env.get("STAGING_URL")
    if not base and transport is None:
        print("STAGING_URL is not set.")
        return 2
    transport = transport or api.HttpTransport(base)
    accounts: dict[str, api.Account] = {}
    try:
        email, password = env.get("STAGING_EMAIL_A"), env.get("STAGING_PASSWORD_A")
        if not email or not password:
            print("STAGING_EMAIL_A and STAGING_PASSWORD_A are needed.")
            return 2
        accounts["a"] = api.login(transport, "a", email, password)
        email, password = env.get("STAGING_EMAIL_B"), env.get("STAGING_PASSWORD_B")
        if email and password:
            accounts["b"] = api.login(transport, "b", email, password)
    except api.ApiError as exc:
        print(str(exc))
        return 2
    state = api.read_state(transport, accounts["a"], accounts.get("b"))
    left = {k: api.turns_left(transport, a) for k, a in accounts.items()}
    need = _turns_needed(selected)
    limited = [v for v in left.values() if v is not None]
    if (
        limited
        and len(limited) == len(left)
        and sum(limited) < need
        and not args.allow_short
    ):
        print(
            f"The test accounts have {sum(limited)} turns left today and this run needs "
            f"about {need}. Grant them a temporary allowance in the staff console, run a "
            "slice (--category, --limit), or pass --allow-short to stop where they run out."
        )
        return 2
    ctx = Context(
        transport=transport,
        accounts=accounts,
        state=state,
        model=judge_model,
        wait={"wait_seconds": args.wait},
        left=left,
    )
    print(
        f"Running {len(selected)} cases against {base or 'the given transport'} "
        f"as {len(accounts)} account(s); judge: {judge_model.name if judge_model else 'none'}."
    )
    results, stopped = run_all(selected, ctx)
    spend = ctx.spend.summary()
    built = report.build(
        "decibyl",
        results,
        target=base or "",
        spend=spend,
        judged=judge_model is not None,
        extra={"stopped": stopped} if stopped else None,
    )
    ok = _finish("decibyl", built, args)
    _print_spend(spend)
    return 0 if ok else 1


def run_laya(args: argparse.Namespace, *, chooser=None) -> int:
    try:
        from api.services.ops import laya_eval  # noqa: F401 - the deps check
        from evals.decibyl import laya
    except ImportError as exc:
        print(
            "The laya suite imports the routing code, so it needs the API's Python "
            f"dependencies (scripts/setup_requirements.sh): {exc}"
        )
        return 2
    if args.estimate:
        print(
            "The laya suite asks the self-hosted decision model once per sample; no vendor spend."
        )
        return 0
    wanted = _categories(args, LAYA_CATEGORIES)
    if wanted == []:
        print("No laya samples in this slice.")
        return 0
    rows, extra, calls = asyncio.run(
        laya.run(limit=args.limit, categories=wanted, chooser=chooser)
    )
    spend = {
        "model_calls": calls,
        "decibyl_calls_estimated": 0,
        "judge_calls": 0,
        "estimated_usd": {"decibyl": 0.0, "judge": 0.0, "total": 0.0},
        "assumptions": {
            "note": "Laya is self-hosted; its calls are not metered per token."
        },
    }
    built = report.build("laya", rows, spend=spend, judged=False, extra=extra)
    if not extra["laya"]["configured"]:
        print("LAYA_URL is not set: the score below is the rules alone.")
    ok = _finish("laya", built, args)
    _print_spend(spend)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    try:
        suites = SUITES if args.suite == "all" else (args.suite,)
        codes = []
        for name in suites:
            codes.append(run_decibyl(args) if name == "decibyl" else run_laya(args))
        return max(codes)
    except cases.CaseError as exc:
        print(f"The case set is not valid: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

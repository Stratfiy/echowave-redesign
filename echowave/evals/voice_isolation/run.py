"""Run the voice-isolation evaluation and write the results table.

    cd echowave
    python -m evals.voice_isolation.assets          # once: models and corpora
    PYTHONPATH=. python -m evals.voice_isolation.run --workers 4

Writes ``results/results.json`` (every scene, every configuration) and
``results/results.md`` (the tables in the PR). Deterministic: the same seeds
give the same scenes, and synthesis is cached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import defaultdict
from dataclasses import asdict
from multiprocessing import Pool
from pathlib import Path

from evals.voice_isolation import scenes as scene_mod
from evals.voice_isolation.simulate import (
    Config,
    run_asr,
    run_filter,
    run_strategy,
    run_vad,
)

RESULTS = Path(__file__).parent / "results"

#: Today: RNNoise, both caller environments, both word counts.
BASELINES = [
    Config("baseline normal mw3", vad="normal", min_words=3),
    Config("baseline noisy mw3", vad="noisy", min_words=3),
    Config("baseline normal mw4", vad="normal", min_words=4),
    Config("baseline noisy mw4", vad="noisy", min_words=4),
]


def default_configs(
    thresholds: list[float], models: list[str], dfn: bool
) -> list[Config]:
    configs = list(BASELINES)
    for model in models:
        for th in thresholds:
            configs.append(
                Config(f"lock {model} t{th:.2f}", lock_model=model, threshold=th)
            )
            configs.append(
                Config(
                    f"lock {model} t{th:.2f} no-loud",
                    lock_model=model,
                    threshold=th,
                    loud_margin_db=None,
                )
            )
    if dfn:
        configs.append(Config("dfn normal mw3", filter="dfn"))
        configs.append(Config("dfn noisy mw4", filter="dfn", vad="noisy", min_words=4))
        for model in models:
            for th in thresholds:
                configs.append(
                    Config(
                        f"dfn+lock {model} t{th:.2f}",
                        filter="dfn",
                        lock_model=model,
                        threshold=th,
                    )
                )
    return configs


def _quiet() -> None:
    # Strategies log every transcription at DEBUG; across 300 scenes and 24
    # configurations that costs more than the models.
    import sys

    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="WARNING")


def _process(job) -> dict:
    scene, configs = job
    _quiet()
    rendered = scene_mod.render(scene)
    audio_secs = len(rendered.audio) / 8000
    filters = sorted({c.filter for c in configs})
    out = {"scene": rendered.meta(), "filters": {}, "outcomes": []}

    async def go():
        per_filter = {}
        for name in filters:
            chunks, cpu, worst = await run_filter(rendered.audio, name)
            out["filters"][name] = {
                "cpu_s": cpu,
                "audio_s": audio_secs,
                "worst_ms": worst,
            }
            asr = run_asr(chunks, scene.asr_language)
            vads = {env: run_vad(chunks, env) for env in ("normal", "noisy")}
            per_filter[name] = (chunks, asr, vads)
        for config in configs:
            chunks, asr, vads = per_filter[config.filter]
            outcome = await run_strategy(
                rendered, chunks, vads[config.vad], asr, config
            )
            out["outcomes"].append(asdict(outcome))

    asyncio.run(go())
    return out


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(q / 100 * (len(values) - 1)))))
    return values[k]


def summarise(records: list[dict], group=None) -> dict:
    """Per configuration (and optional group key): the headline numbers."""
    rows: dict = defaultdict(
        lambda: {
            "scenes": 0,
            "false": 0,
            "false_scenes": 0,
            "agent_s": 0.0,
            "barge": 0,
            "accepted": 0,
            "accept_ms": [],
            "judge_ms": [],
            "lock_cpu": 0.0,
            "audio_s": 0.0,
        }
    )
    for rec in records:
        meta = rec["scene"]
        for o in rec["outcomes"]:
            agent_s = o["agent_secs"]
            key = (o["config"],) if group is None else (o["config"], group(meta))
            row = rows[key]
            row["scenes"] += 1
            row["false"] += o["false_interruptions"]
            row["false_scenes"] += 1 if o["false_interruptions"] else 0
            row["agent_s"] += agent_s
            row["barge"] += 1
            row["accepted"] += 1 if o["accepted"] else 0
            if o["accept_ms"] is not None:
                row["accept_ms"].append(o["accept_ms"])
            row["judge_ms"] += o["judge_ms"]
            row["lock_cpu"] += o["lock_cpu_s"]
            row["audio_s"] += meta["duration"]
    out = {}
    for key, r in rows.items():
        out[key] = {
            "scenes": r["scenes"],
            "false_per_min": 60 * r["false"] / r["agent_s"],
            "false_scene_pct": 100 * r["false_scenes"] / r["scenes"],
            "missed_pct": 100 * (r["barge"] - r["accepted"]) / r["barge"],
            "accept_p50_ms": _pct(r["accept_ms"], 50),
            "accept_p90_ms": _pct(r["accept_ms"], 90),
            "judge_p50_ms": _pct(r["judge_ms"], 50),
            "judge_p95_ms": _pct(r["judge_ms"], 95),
            "lock_cpu_ms_per_s": 1000 * r["lock_cpu"] / r["audio_s"]
            if r["audio_s"]
            else 0,
        }
    return out


def filter_cost(records: list[dict]) -> dict:
    agg = defaultdict(lambda: [0.0, 0.0, 0.0])
    for rec in records:
        for name, f in rec["filters"].items():
            agg[name][0] += f["cpu_s"]
            agg[name][1] += f["audio_s"]
            agg[name][2] = max(agg[name][2], f["worst_ms"])
    return {
        n: {"cpu_ms_per_s": 1000 * c / a, "worst_call_ms": w}
        for n, (c, a, w) in agg.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--thresholds", default="0.25,0.30,0.35")
    parser.add_argument("--models", default="resnet34,ecapa512")
    parser.add_argument("--no-dfn", action="store_true")
    parser.add_argument("--out", default=str(RESULTS / "results.json"))
    args = parser.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(",") if t]
    models = [m for m in args.models.split(",") if m]
    configs = default_configs(thresholds, models, dfn=not args.no_dfn)
    grid = scene_mod.grid(args.seeds)
    if args.limit:
        grid = grid[:: max(1, len(grid) // args.limit)][: args.limit]

    _quiet()
    # Synthesise every line once in this process, so the workers only read
    # the cache rather than racing to write it.
    for scene in grid:
        scene_mod.render(scene)

    started = time.time()
    with Pool(args.workers) as pool:
        records = []
        for k, rec in enumerate(
            pool.imap_unordered(_process, [(s, configs) for s in grid]), 1
        ):
            records.append(rec)
            if k % 10 == 0:
                print(
                    f"{k}/{len(grid)} scenes, {time.time() - started:.0f}s", flush=True
                )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"records": records}, ensure_ascii=False))
    print(f"wrote {args.out}: {len(records)} scenes x {len(configs)} configs")
    print(
        json.dumps({" | ".join(k): v for k, v in summarise(records).items()}, indent=1)
    )
    print(json.dumps(filter_cost(records), indent=1))
    print(statistics.mean(1 for _ in records))


if __name__ == "__main__":
    main()

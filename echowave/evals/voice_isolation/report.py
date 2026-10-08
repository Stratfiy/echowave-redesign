"""Turn ``results/results.json`` into the tables in ``results/results.md``.

    PYTHONPATH=. python -m evals.voice_isolation.report [results.json] [bench.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.voice_isolation.assets import licence_table
from evals.voice_isolation.run import RESULTS, filter_cost, summarise
from evals.voice_isolation.scenes import REAL

SPEECH_INTERFERERS = ("talker", "tv", "cafe")
NOISE_INTERFERERS = ("traffic", "fan")


def _ms(v):
    return "–" if v is None else f"{v:.0f}"


def _row(name: str, r: dict) -> str:
    return (
        f"| {name} | {r['false_per_min']:.2f} | {r['false_scene_pct']:.0f}% | "
        f"{r['missed_pct']:.1f}% | {_ms(r['accept_p50_ms'])} | {_ms(r['accept_p90_ms'])} |"
    )


HEAD = (
    "| Configuration | False interruptions / min of agent speech | Scenes with ≥1 false | "
    "Missed barge-ins | Accept p50 ms | Accept p90 ms |\n| --- | --- | --- | --- | --- | --- |"
)


def table(records: list[dict], configs: list[str], keep=None) -> str:
    chosen = [r for r in records if keep is None or keep(r["scene"])]
    s = summarise(chosen)
    lines = [HEAD]
    for c in configs:
        if (c,) in s:
            lines.append(_row(c, s[(c,)]))
    return "\n".join(lines) + f"\n\n_{len(chosen)} scenes._"


def by_group(records: list[dict], configs: list[str], group, label: str) -> str:
    s = summarise(records, group=group)
    groups = sorted({k[1] for k in s}, key=lambda g: (str(type(g)), g))
    lines = [
        f"| {label} | " + " | ".join(configs) + " |",
        "| --- |" + " --- |" * len(configs),
    ]
    for g in groups:
        cells = []
        for c in configs:
            r = s.get((c, g))
            cells.append(
                "–" if r is None else f"{r['false_per_min']:.2f} / {r['missed_pct']:.0f}%"
            )
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS / "results.json"
    records = json.loads(src.read_text())["records"]
    bench_path = Path(sys.argv[2]) if len(sys.argv) > 2 else RESULTS / "bench.json"
    configs = []
    for rec in records[:1]:
        configs = [o["config"] for o in rec["outcomes"]]

    def speech(meta):
        return meta["scene"]["interferer"] in SPEECH_INTERFERERS

    def noise(meta):
        return meta["scene"]["interferer"] in NOISE_INTERFERERS

    def real(meta):
        return meta["scene"]["language"] in REAL

    headline = [
        c
        for c in configs
        if c.startswith("baseline")
        or c in ("dfn normal mw3", "dfn noisy mw4")
        or c.endswith("t0.30")
        or c.endswith("t0.30 no-loud")
    ]
    key = [c for c in configs if c in ("baseline normal mw3", "baseline noisy mw4")]
    key += [c for c in configs if c.startswith("lock resnet34 t0.30")][:1]
    key += [c for c in configs if c == "dfn normal mw3"]
    key += [c for c in configs if c.startswith("dfn+lock resnet34 t0.30")]

    out = ["# Voice isolation: results", ""]
    out += ["## All scenes", "", table(records, configs), ""]
    out += ["## Background speech (talker, TV, cafeteria)", "", table(records, headline, speech), ""]
    out += ["## Real voices only (LibriSpeech, MUCS), background speech", ""]
    out += [table(records, headline, lambda m: speech(m) and real(m)), ""]
    out += ["## Non-speech noise (traffic, fan)", "", table(records, headline, noise), ""]
    out += ["## By interferer (false / min · missed)", ""]
    out += [by_group(records, key, lambda m: m["scene"]["interferer"], "Interferer"), ""]
    out += ["## By SNR, background speech only (false / min · missed)", ""]
    out += [
        by_group(
            [r for r in records if speech(r["scene"])],
            key,
            lambda m: m["scene"]["snr_db"],
            "SNR dB",
        ),
        "",
    ]
    out += ["## By distance, talker and TV (false / min · missed)", ""]
    out += [
        by_group(
            [r for r in records if r["scene"]["scene"]["interferer"] in ("talker", "tv")],
            key,
            lambda m: m["scene"]["distance_m"],
            "Distance m",
        ),
        "",
    ]
    out += ["## By speech source (false / min · missed)", ""]
    out += [by_group(records, key, lambda m: m["scene"]["language"], "Source"), ""]
    out += ["## Filter CPU in the evaluation (parallel run, indicative)", ""]
    out += ["| Filter | CPU ms per s of 8 kHz audio | Worst single call ms |", "| --- | --- | --- |"]
    for name, c in sorted(filter_cost(records).items()):
        out.append(f"| {name} | {c['cpu_ms_per_s']:.0f} | {c['worst_call_ms']:.1f} |")
    if bench_path.exists():
        out += ["", "## Cost on one core (bench.py)", "", "```json", bench_path.read_text().strip(), "```"]
    out += ["", "## Licences", "", licence_table(), ""]
    (RESULTS / "results.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()

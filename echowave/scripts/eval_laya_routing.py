"""Evaluate Auto's routing: the rules against Laya, on the labelled set
(handoff 14; services/ops/laya_eval.py).

    python -m scripts.eval_laya_routing                       # LAYA_URL from the env
    python -m scripts.eval_laya_routing --samples my-set.jsonl --out report/
    docker compose exec -T api python -m scripts.eval_laya_routing --record

Writes ``laya-routing.md`` and ``laya-routing.json`` and prints the verdict.
With ``--record`` (inside the api container) the summary becomes an
``ops_evidence`` row the console's Laya page shows. Only numbers are
recorded; the samples themselves stay in the repository.

Exit status 0 when the verdict is "promote", 1 when Laya should stay in
shadow -- so it can gate switching LAYA_ROUTING to ``on``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _record(report: dict) -> None:
    from api.db import db_client
    from api.services.ops import evidence

    verdict = report["verdict"]
    async with db_client.async_session() as session:
        await evidence.record(
            session,
            kind="laya_evaluation",
            outcome="passed" if verdict["promote"] else "failed",
            summary=(
                f"{'Promote' if verdict['promote'] else 'Stay in shadow'}: rules "
                f"{report['rules']['accuracy']}, Laya+rules {report['combined']['accuracy']} "
                f"over {report['samples']} samples."
            ),
            metrics={
                "samples": report["samples"],
                "rules_accuracy": report["rules"]["accuracy"],
                "combined_accuracy": report["combined"]["accuracy"],
                "laya_answered_accuracy": report["laya"]["answered_accuracy"],
                "laya_abstention": report["laya"]["abstention"],
                "laya_p95_ms": report["laya"]["latency_ms"]["p95"],
                "calibration_error": report["laya"]["calibration"]["ece"],
            },
        )
        await session.commit()


def main(argv: list[str] | None = None) -> int:
    from api.services.ops import laya_eval

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--samples", type=Path, default=None)
    p.add_argument("--out", type=Path, default=Path("laya-evaluation"))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--record", action="store_true")
    args = p.parse_args(argv)

    samples = laya_eval.load_samples(args.samples)
    report = asyncio.run(laya_eval.evaluate(samples, concurrency=args.concurrency))
    args.out.mkdir(parents=True, exist_ok=True)
    page = laya_eval.render_markdown(report)
    (args.out / "laya-routing.md").write_text(page)
    (args.out / "laya-routing.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )
    print(page)
    if args.record:
        asyncio.run(_record(report))
    return 0 if report["verdict"]["promote"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

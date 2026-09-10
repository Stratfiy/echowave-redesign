"""Find where the HTTP control plane bends, before a real campaign finds it.

`rehearse_concurrency.py` proves the *spend ceiling* holds under concurrent
transactions. This asks the other volume question: how many concurrent HTTP
requests does this deployment serve before latency or errors climb — the knee
that decides how many agents, dashboards and embed widgets it carries at once.

It ramps concurrency through a series of levels, fires a batch at each, and
reports p50/p95/p99, throughput and error rate per level. It stops the moment
a level crosses a knee threshold (error rate or p95), so it finds the edge
without holding a degraded service down.

Two things it is careful about, because it can be pointed at production:

  * **It never dials out.** The default endpoint is the health check. The
    text-chat mode exercises the full LLM pipeline (STT-less, no carrier), but
    even that is opt-in and capped, and nothing here originates a phone call.
  * **It is capped.** ``--max-total`` bounds the whole run; the ramp also
    stops at the first knee. A run cannot become an unbounded flood.

Examples::

    # Read-only ramp against the health endpoint (safe anywhere).
    python -m scripts.load_ramp --base-url https://staging.decibyl.ai

    # Authenticated ramp against a read endpoint, with a key.
    python -m scripts.load_ramp --base-url https://staging.decibyl.ai \
        --path /api/v1/workflow/list --api-key dcb_xxx

    # Custom ramp and knee.
    python -m scripts.load_ramp --levels 1,10,25,50,100,200 \
        --rounds 4 --knee-p95-ms 8000 --knee-error-rate 0.10

Exit status is 0 when the ramp completed every level under the knee, 1 when a
knee stopped it early — so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:  # pragma: no cover - the script names its own dependency
    print("This script needs httpx:  pip install httpx", file=sys.stderr)
    raise


@dataclass
class LevelResult:
    concurrency: int
    latencies_ms: list[float] = field(default_factory=list)
    statuses: list[object] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return sum(1 for s in self.statuses if s == 200)

    @property
    def errors(self) -> int:
        return len(self.statuses) - self.ok

    @property
    def error_rate(self) -> float:
        return self.errors / len(self.statuses) if self.statuses else 1.0

    def pct(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = max(0, min(len(ordered) - 1, int(len(ordered) * p) - 1))
        return ordered[idx]

    @property
    def throughput_rps(self) -> float:
        # Little's law estimate: with `concurrency` requests in flight and a
        # mean service time of `mean_s` seconds, the system completes about
        # concurrency / mean_s requests per second. Reported, not the naive
        # count/time, because this run's batches are back-to-back rather than
        # a steady arrival rate.
        if not self.latencies_ms:
            return 0.0
        mean_s = statistics.mean(self.latencies_ms) / 1000
        return self.concurrency / mean_s if mean_s else 0.0


def parse_levels(raw: str) -> list[int]:
    levels = [int(x) for x in raw.split(",") if x.strip()]
    if not levels or any(n <= 0 for n in levels):
        raise ValueError("--levels must be positive integers, e.g. 1,10,25,50")
    return levels


async def _one(
    client: httpx.AsyncClient, url: str, headers: dict
) -> tuple[float, object]:
    t0 = time.perf_counter()
    try:
        r = await client.get(url, headers=headers)
        return (time.perf_counter() - t0) * 1000, r.status_code
    except Exception as exc:  # noqa: BLE001 - an error is a data point, not a crash
        return (time.perf_counter() - t0) * 1000, type(exc).__name__


async def run_level(
    url: str, headers: dict, *, concurrency: int, rounds: int, timeout: float
) -> LevelResult:
    result = LevelResult(concurrency=concurrency)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(rounds):
            batch = await asyncio.gather(
                *[_one(client, url, headers) for _ in range(concurrency)]
            )
            for ms, status in batch:
                result.latencies_ms.append(ms)
                result.statuses.append(status)
    return result


def format_line(r: LevelResult) -> str:
    from collections import Counter

    line = (
        f"conc={r.concurrency:4d}  n={len(r.statuses):5d}  "
        f"ok={r.ok:5d} err={r.errors:4d} ({r.error_rate:5.1%})  "
        f"p50={r.pct(0.50):7.0f}ms p95={r.pct(0.95):7.0f}ms "
        f"p99={r.pct(0.99):7.0f}ms  ~{r.throughput_rps:6.0f} rps"
    )
    non200 = Counter(s for s in r.statuses if s != 200)
    if non200:
        line += f"\n           non-200: {dict(non200)}"
    return line


async def ramp(args: argparse.Namespace) -> int:
    url = args.base_url.rstrip("/") + args.path
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    levels = parse_levels(args.levels)

    print(f"Ramp against {url}")
    print(
        f"levels={levels} rounds={args.rounds} "
        f"knee: error>{args.knee_error_rate:.0%} or p95>{args.knee_p95_ms:.0f}ms "
        f"cap={args.max_total} reqs\n"
    )

    sent = 0
    last_good = 0
    knee = None
    for conc in levels:
        if sent + conc * args.rounds > args.max_total:
            print(
                f"-> next level would exceed --max-total ({args.max_total}); stopping"
            )
            break
        result = await run_level(
            url, headers, concurrency=conc, rounds=args.rounds, timeout=args.timeout
        )
        sent += len(result.statuses)
        print(format_line(result))

        if result.error_rate > args.knee_error_rate:
            knee = (
                conc,
                f"error rate {result.error_rate:.0%} > {args.knee_error_rate:.0%}",
            )
            break
        if result.pct(0.95) > args.knee_p95_ms:
            knee = (conc, f"p95 {result.pct(0.95):.0f}ms > {args.knee_p95_ms:.0f}ms")
            break
        last_good = conc

    print()
    if knee:
        conc, why = knee
        print(f"KNEE at concurrency={conc}: {why}")
        print(f"Last level under the knee: concurrency={last_good}")
        print(f"Total requests: {sent}")
        return 1
    print(f"No knee reached through concurrency={last_good}. Total requests: {sent}")
    print(
        "The ceiling is above the top level tested (or the client/proxy is the bottleneck)."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8000", help="Target origin.")
    p.add_argument(
        "--path",
        default="/api/v1/health",
        help="Path to hit. Default is the read-only health endpoint.",
    )
    p.add_argument("--api-key", default=None, help="X-API-Key for authenticated paths.")
    p.add_argument("--levels", default="1,5,10,25,50,100", help="Concurrency steps.")
    p.add_argument("--rounds", type=int, default=3, help="Batches per level.")
    p.add_argument(
        "--timeout", type=float, default=20.0, help="Per-request timeout (s)."
    )
    p.add_argument(
        "--knee-error-rate", type=float, default=0.20, help="Stop above this."
    )
    p.add_argument(
        "--knee-p95-ms", type=float, default=12000.0, help="Stop above this p95."
    )
    p.add_argument(
        "--max-total", type=int, default=5000, help="Hard cap on total requests."
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(ramp(args))


if __name__ == "__main__":
    raise SystemExit(main())

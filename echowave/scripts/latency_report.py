"""What the pipeline actually costs, per stage, from real calls.

Two questions this answers, both of which are currently guesses:

**What is Sarvam's real time-to-final-segment from our region?**
`SARVAM_STT_TTFS_P99_DEFAULT` is 0.5s, and the comment beside it says plainly
that it is a starting point rather than a measurement — pipecat ships 1.17s,
measured against Sarvam from wherever their benchmark ran, and we sit in
ap-south-1 alongside Sarvam's own API. The turn strategy waits on that budget
on every single turn, so the difference between the assumed value and the real
one is dead air we are adding for no reason. This prints the real one.

**Are we losing turns to the turn-stop timeout?**
Pipecat issues #3643 and #3988: a turn can start without VAD but cannot stop
without it, so a short utterance the VAD misses hangs until
`user_turn_stop_timeout` fires — five seconds of silence. Hindi is dense with
exactly the utterances this hits: haan, ji, achha, theek hai, nahi. There is no
`turn_exit_reason` column, but a timeout exit has an unmistakable signature: a
latency that lands on the timeout value rather than anywhere near the stage
sum. That is detectable from the data we already store, with no pipeline change
and no risk.

Read-only. Run it against production.

    source venv/bin/activate && set -a && source api/.env && set +a \\
        && python -m scripts.latency_report --days 7
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import text

from api.db import db_client


@dataclass(frozen=True)
class Percentiles:
    n: int
    p50: float | None
    p90: float | None
    p99: float | None


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}ms"


async def _percentiles(session, expression: str, days: int) -> Percentiles:
    """Percentiles of one stage over recent turns.

    Percentiles are computed in SQL over raw rows rather than by averaging
    per-call percentiles, which gives a different and wrong answer.
    """
    row = (
        await session.execute(
            text(
                f"""
                SELECT
                  count(*) AS n,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY {expression}) AS p50,
                  percentile_cont(0.90) WITHIN GROUP (ORDER BY {expression}) AS p90,
                  percentile_cont(0.99) WITHIN GROUP (ORDER BY {expression}) AS p99
                FROM call_turn_metrics
                WHERE created_at > now() - make_interval(days => :days)
                  AND {expression} IS NOT NULL
                  AND {expression} >= 0
                """
            ),
            {"days": days},
        )
    ).one()
    return Percentiles(row.n, row.p50, row.p90, row.p99)


async def report(days: int, timeout_secs: float) -> None:
    async with db_client.async_session() as session:
        print(f"\nLatency over the last {days} days\n" + "=" * 52)

        stages = [
            ("Endpointing", "t_endpoint_fired_ms"),
            # Speech end is the zero point, so the final-transcript mark IS the
            # time to final segment — the number SARVAM_STT_TTFS_P99 stands in
            # for.
            ("STT final (TTFS)", "t_stt_final_ms"),
            ("LLM first token", "t_llm_first_token_ms - t_stt_final_ms"),
            ("TTS first byte", "t_tts_first_byte_ms - t_llm_first_token_ms"),
            ("Audio out", "t_audio_out_ms - t_tts_first_byte_ms"),
            ("PERCEIVED TOTAL", "latency_ms"),
        ]

        print(f"{'stage':<20} {'p50':>10} {'p90':>10} {'p99':>10} {'n':>8}")
        print("-" * 62)
        ttfs: Percentiles | None = None
        for label, expression in stages:
            p = await _percentiles(session, expression, days)
            if label.startswith("STT"):
                ttfs = p
            print(
                f"{label:<20} {_fmt(p.p50):>10} {_fmt(p.p90):>10} "
                f"{_fmt(p.p99):>10} {p.n:>8,}"
            )

        if ttfs and ttfs.p99 is not None and ttfs.n >= 100:
            measured = ttfs.p99 / 1000
            print(
                f"\nMeasured STT p99 is {measured:.2f}s over {ttfs.n:,} turns.\n"
                f"  Set SARVAM_STT_TTFS_P99={measured:.2f} if Sarvam is the "
                "transcriber on this traffic.\n"
                "  Every turn waits on this value, so an assumption that is "
                "high is dead air on all of them —\n"
                "  and one that is low is not a cut-off risk, because the turn "
                "still waits for the transcript."
            )
        elif ttfs:
            print(
                f"\nOnly {ttfs.n:,} turns with an STT mark. Under 100 this is "
                "noise, not a measurement —\n  leave the configured value alone."
            )

        # Suspected timeout exits. A turn that ends because the strategy fired
        # lands near the sum of its stages; one that ends because the timeout
        # expired lands on the timeout. Windowed at ±15% rather than matched
        # exactly, since the measured value carries scheduling jitter.
        timeout_ms = timeout_secs * 1000
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE latency_ms BETWEEN :low AND :high
                      ) AS suspected,
                      count(*) AS total
                    FROM call_turn_metrics
                    WHERE created_at > now() - make_interval(days => :days)
                      AND latency_ms IS NOT NULL
                    """
                ),
                {
                    "low": timeout_ms * 0.85,
                    "high": timeout_ms * 1.15,
                    "days": days,
                },
            )
        ).one()

        print(f"\nTurns near the {timeout_secs:g}s turn-stop timeout\n" + "-" * 52)
        if not row.total:
            print("No turns recorded in this window.")
            return

        share = row.suspected / row.total * 100
        print(f"{row.suspected:,} of {row.total:,} turns ({share:.1f}%)")
        if share >= 1.0:
            print(
                "\n  This looks like pipecat #3643/#3988: a turn cannot stop "
                "without VAD, so a short\n  utterance the VAD misses hangs "
                "until the timeout. In Hindi that is haan, ji, achha,\n  theek "
                "hai — the most common turns in the language. It will be "
                "reported as the agent\n  being slow, and it is not the model's "
                "fault. Check MinWordsUserTurnStartStrategy\n  before changing "
                "anything downstream."
            )
        else:
            print("  Below 1% — consistent with ordinary long turns, not the bug.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--timeout-secs",
        type=float,
        default=5.0,
        help="user_turn_stop_timeout in effect (pipecat default 5.0)",
    )
    args = parser.parse_args()
    asyncio.run(report(args.days, args.timeout_secs))


if __name__ == "__main__":
    main()

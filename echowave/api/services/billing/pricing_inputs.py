"""The physical facts a price has to be set against.

`kpis.py` answers *what did a minute earn*. This answers the question that
comes before it: what is actually true about the calls, so that a price can be
set rather than guessed.

It exists because three internal documents assumed 850, 900 and 2,300 TTS
characters per minute — a 2.7x spread nobody had ever measured — and every
margin figure in the pricing study rests on which of them is right. At $0.015
per 1,000 characters that spread is the difference between a healthy margin and
a negative one, and speech synthesis is the largest line on an Indic call.

Four things, and each settles a decision that is otherwise a guess:

**Characters per minute** sets the TTS rate, which sets the blended cost.
**Rate-card gaps** are the components billing zero and reporting healthy.
**Peak concurrency** is what a concurrency tier should be priced against —
the limiter's ceiling says what we permit, not what anyone has ever needed.
**Minutes per account per month** is what a bundle's included balance has to
be sized against.

The derivation here is deliberately thin. Percentiles come out of the database
because computing them in Python would mean pulling every call into memory to
answer a question the database can answer in one pass.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from api.db import billing_dashboard_client as dash
from api.db import billing_kpi_client as kpi_client

#: The band the internal documents disagreed over, carried so the screen can
#: say where the measured figure falls rather than leaving a reader to
#: remember. Not a target — an artefact of the disagreement being settled.
ASSUMED_CHARS_PER_MINUTE = (850, 900, 2300)


def _spread(rows: list[dict]) -> dict | None:
    """The headline: one number, and how far the assumptions were from it.

    Weighted by calls rather than averaged across models, because a model used
    on five calls should not move the figure the rate card is set from as much
    as one used on fifty thousand.
    """
    weighted = [(r["median_chars_per_minute"], r["calls"]) for r in rows if r["calls"]]
    total_calls = sum(calls for _, calls in weighted)
    if not total_calls:
        return None

    measured = round(sum(median * calls for median, calls in weighted) / total_calls)
    return {
        "measured_chars_per_minute": measured,
        "calls": total_calls,
        "assumptions": [
            {
                "assumed": assumed,
                # Positive means the assumption was high, so the cost model
                # built on it over-states what a minute costs us.
                "error_pct": round(100.0 * (assumed - measured) / measured, 1)
                if measured
                else None,
            }
            for assumed in ASSUMED_CHARS_PER_MINUTE
        ],
    }


async def pricing_inputs_report(
    session: AsyncSession, *, start: date, end: date
) -> dict:
    """Everything the pricing-inputs screen needs, in one round of queries."""
    by_model = await kpi_client.tts_chars_per_minute(session, start=start, end=end)
    by_language = await kpi_client.tts_chars_per_minute_by_language(
        session, start=start, end=end
    )
    gaps = await kpi_client.rate_card_gaps(session, start=start, end=end)
    concurrency = await dash.peak_concurrency_by_day(session, start=start, end=end)
    accounts = await kpi_client.monthly_minutes_by_account(
        session, start=start, end=end
    )

    return {
        "tts_chars_per_minute": {
            "headline": _spread(by_model),
            "by_model": by_model,
            "by_language": by_language,
        },
        "rate_card_gaps": gaps,
        "peak_concurrency": {
            "series": concurrency,
            "peak": max((row["peak"] for row in concurrency), default=0),
        },
        "monthly_minutes_by_account": accounts,
    }

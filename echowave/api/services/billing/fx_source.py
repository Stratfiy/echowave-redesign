"""Fetch USD→INR and record it, so the rate card is not quietly a year stale.

This is the only genuinely live price in the system, and it is worth having.
Provider prices are published as marketing HTML with no API behind them, so
scraping them would break silently on the first redesign and leave a plausible
wrong number in the price book — the precise failure this codebase keeps
guarding against. An exchange rate, by contrast, is a real feed.

Every fetch writes an **effective-dated** row rather than updating one, so a
call priced last Tuesday can still be re-derived at Tuesday's rate. The history
table already works this way; this module is what finally writes to it on a
schedule instead of at migration time.

**A failed fetch writes nothing.** Costing a call at a slightly stale rate is a
rounding error; costing it at a number invented because a request timed out is
a wrong invoice, and the two are not the same kind of mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import UsdInrRateHistoryModel

#: Open, keyless, no attribution requirement. Named in the stored row so an
#: auditor can see where a rate came from years later.
SOURCE_URL = "https://api.frankfurter.app/latest?from=USD&to=INR"
SOURCE_NAME = "frankfurter"

REQUEST_TIMEOUT_SECONDS = 10.0

#: Sanity bounds. A feed that returns 0, or 9600 because it quietly switched to
#: paise, must not become tomorrow's exchange rate. The band is deliberately
#: wide — this rejects nonsense, not movement.
MIN_PLAUSIBLE_PAISE_PER_USD = 5_000  # ₹50
MAX_PLAUSIBLE_PAISE_PER_USD = 20_000  # ₹200

#: Below this, a new row is not worth the history. Daily noise of a few paise
#: would otherwise fill the table with rows that change no invoice.
MIN_MOVE_PAISE = 25  # ₹0.25


class FxFetchError(RuntimeError):
    """The rate could not be established. Nothing was written."""


@dataclass(frozen=True)
class FetchedRate:
    paise_per_usd: int
    source: str
    fetched_at: datetime


async def fetch() -> FetchedRate:
    """Current USD→INR as an integer of paise per dollar.

    Rounded half-up to match every other money conversion in the codebase, so
    the rate cannot disagree with the arithmetic that consumes it.
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(SOURCE_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise FxFetchError(f"Could not reach {SOURCE_NAME}: {exc}") from exc

    rupees = (payload.get("rates") or {}).get("INR")
    if not isinstance(rupees, (int, float)) or rupees <= 0:
        raise FxFetchError(f"{SOURCE_NAME} returned no usable INR rate: {payload!r}")

    paise = int(
        (Decimal(str(rupees)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
    if not MIN_PLAUSIBLE_PAISE_PER_USD <= paise <= MAX_PLAUSIBLE_PAISE_PER_USD:
        raise FxFetchError(
            f"{SOURCE_NAME} returned ₹{paise / 100:.2f}/USD, outside the plausible "
            "band. Refusing it — a wrong exchange rate misprices every call."
        )

    return FetchedRate(
        paise_per_usd=paise, source=SOURCE_NAME, fetched_at=datetime.now(UTC)
    )


async def record(session: AsyncSession, *, rate: FetchedRate) -> bool:
    """Close the open row and open a new one. True if anything changed.

    Supersedes rather than updates, for the same reason provider rates do: an
    invoice raised last month has to stay re-derivable at the rate that was in
    force when it was raised.
    """
    current = await session.scalar(
        select(UsdInrRateHistoryModel)
        .where(UsdInrRateHistoryModel.effective_to.is_(None))
        .order_by(UsdInrRateHistoryModel.effective_from.desc())
        .limit(1)
    )

    if current is not None:
        if abs(current.paise_per_usd - rate.paise_per_usd) < MIN_MOVE_PAISE:
            logger.debug(
                "USD→INR unchanged within tolerance (₹{:.2f} vs ₹{:.2f}); no new row.",
                current.paise_per_usd / 100,
                rate.paise_per_usd / 100,
            )
            return False
        current.effective_to = rate.fetched_at

    session.add(
        UsdInrRateHistoryModel(
            paise_per_usd=rate.paise_per_usd,
            source=rate.source,
            effective_from=rate.fetched_at,
            effective_to=None,
        )
    )
    logger.info(
        "USD→INR now ₹{:.2f} from {} (was {})",
        rate.paise_per_usd / 100,
        rate.source,
        f"₹{current.paise_per_usd / 100:.2f}" if current else "unset",
    )
    return True


async def refresh(session: AsyncSession) -> bool:
    """Fetch and record in one step. Returns whether a new row was written.

    Raises :class:`FxFetchError` on a bad fetch and writes nothing — the caller
    decides whether that is worth alerting on. A stale rate is survivable; an
    invented one is not.
    """
    return await record(session, rate=await fetch())

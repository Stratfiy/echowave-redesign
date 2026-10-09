"""What an image cost its vendor, and what the customer was charged (nothing).

Image generation is free while it is tried. The founder's decision is to
price it later from real numbers, so every image records its vendor cost in
the ``image`` component's native unit -- one image -- at the vendor's rate,
beside ``charged_paise = 0``. Nothing touches the credit ledger.

The rate comes from the rate card (``provider_rates``, component ``image``)
when one is on file, and from the starter book in ``default_rates`` when it
is not -- the card is only filled when an operator seeds it, and an image
costed at "no rate on file" is a gap in exactly the numbers this exists to
collect. Which one answered is stored (``cost_source``), so a figure from
the book is never mistaken for a negotiated one. An image made on the
workspace's own key is still costed: the customer paid that vendor, and the
price we would put on it should know what it cost them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.enums import CostComponent, RateUnit
from api.services.billing.default_rates import IMAGE_RATES, usd_to_mpaise
from api.services.billing.money import DEFAULT_USD_INR_PAISE, cost_paise
from api.services.billing.rates import resolve_provider_rate

#: The customer's charge per image while image generation is free to try.
CUSTOMER_PAISE_PER_IMAGE = 0

RATE_CARD = "rate_card"
DEFAULT_BOOK = "default_book"
NONE = "none"


def _book_mpaise(provider: str, model: str) -> int | None:
    exact = None
    fallback = None
    for rate in IMAGE_RATES:
        if rate.provider != provider:
            continue
        if rate.model == model:
            exact = rate
        elif rate.model == "":
            fallback = rate
    chosen = exact or fallback
    if chosen is None:
        return None
    return usd_to_mpaise(chosen.usd_per_unit, usd_inr=DEFAULT_USD_INR_PAISE / 100)


async def vendor_paise_per_image(
    provider: str, model: str, *, at: datetime | None = None
) -> tuple[int, str]:
    """Paise one image costs its vendor, and where the figure came from."""
    at = at or datetime.now(UTC)
    try:
        async with db_client.async_session() as session:
            rate = await resolve_provider_rate(
                session,
                provider=provider,
                component=CostComponent.IMAGE,
                at=at,
                model=model,
            )
        if rate is not None:
            return (
                cost_paise(quantity=1, rate_mpaise=rate.rate_mpaise, unit=rate.unit),
                RATE_CARD,
            )
    except Exception as exc:
        logger.warning("Could not read the image rate for {}: {}", provider, exc)
    book = _book_mpaise(provider, model)
    if book is None:
        logger.warning(
            "No image rate for {}/{}: the image is recorded uncosted", provider, model
        )
        return 0, NONE
    return cost_paise(quantity=1, rate_mpaise=book, unit=RateUnit.IMAGE), DEFAULT_BOOK

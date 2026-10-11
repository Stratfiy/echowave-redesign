"""Watch the payment links agents sent, every two minutes."""

from __future__ import annotations

from loguru import logger

from api.services import features
from api.services.workflow import payment_links


async def watch_payment_links(_ctx) -> None:
    if not features.on_anywhere(payment_links.FLAG):
        return
    counts = await payment_links.watch()
    if any(v for k, v in counts.items() if k not in ("unknown", "created")):
        logger.info("Payment link watch: {}", counts)

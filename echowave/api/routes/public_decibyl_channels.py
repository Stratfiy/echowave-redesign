"""Webhooks for Decibyl in your apps (DCH-1, KAN-277): Telegram now; Slack
and Teams join here. Public and never authenticated: each platform proves
itself its own way, checked before the body is read. Always a 200 once the
proof holds, so a platform never retries a message we chose to drop.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger

from api.services.messaging import whatsapp_inbound
from api.services.messaging.channels import dispatch, telegram

router = APIRouter(prefix="/public", tags=["public-decibyl-channels"])


@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
) -> dict[str, Any]:
    if not telegram.verify(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Bad secret.")
    try:
        update = await request.json()
    except ValueError:
        return {"status": "unreadable"}
    inbound = telegram.parse(update)
    if inbound is None:
        return {"status": "ignored"}
    # Telegram retries until it gets a 200; twice is once.
    if await whatsapp_inbound.seen_before(f"telegram:{inbound.message_id}"):
        return {"status": "duplicate"}
    try:
        status = await dispatch.handle(inbound)
    except Exception as exc:  # noqa: BLE001 - never make Telegram retry
        logger.exception("Telegram update {} failed: {}", inbound.message_id, exc)
        status = "error"
    return {"status": status}

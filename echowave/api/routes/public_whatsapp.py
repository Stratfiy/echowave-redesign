"""Meta's webhook for the platform WhatsApp number (A3 groundwork).

Two routes, both public: the GET Meta calls once to register the webhook,
and the POST it calls for every message and status. Signature-gated, never
authenticated -- see services/messaging/whatsapp_inbound for what happens
to a message and why an unknown sender is dropped with a 200.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

from api.services.messaging import whatsapp_inbound

router = APIRouter(prefix="/public/whatsapp", tags=["public-whatsapp"])


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta's one-time registration: echo the challenge for our token only."""
    challenge = whatsapp_inbound.challenge_for(
        hub_mode, hub_verify_token, hub_challenge
    )
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed.")
    return challenge


@router.post("/webhook", status_code=200)
async def receive(
    request: Request,
    x_hub_signature_256: Annotated[
        str | None, Header(alias="X-Hub-Signature-256")
    ] = None,
) -> dict[str, Any]:
    """Every message to the platform number. Always 200 once the signature
    holds, so Meta never retries a message we chose to drop."""
    raw = await request.body()
    if not whatsapp_inbound.verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Bad signature.")
    try:
        payload = await request.json()
    except ValueError:
        return {"status": "unreadable"}
    statuses: list[str] = []
    for inbound in whatsapp_inbound.parse(payload):
        try:
            statuses.append(await whatsapp_inbound.handle(inbound))
        except Exception as exc:  # noqa: BLE001 - one bad message is not all
            logger.exception("WhatsApp inbound {} failed: {}", inbound.message_id, exc)
            statuses.append("error")
    return {"status": "ok", "messages": statuses}

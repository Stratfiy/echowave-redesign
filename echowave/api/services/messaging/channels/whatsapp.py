"""WhatsApp: text through the existing sender, cards as reply buttons.

Inbound stays in ``whatsapp_inbound`` (signature, dedupe, files), which
hands a linked sender's lines and every button tap to ``dispatch``.
Replies go out inside the 24-hour window the owner's own message opened,
so free text and interactive messages are allowed without a template.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from .base import WHATSAPP, Card, Inbound, button_id

TIMEOUT_SECONDS = 15
#: Meta's limits for reply buttons.
MAX_BODY = 1024
MAX_TITLE = 20


def interactive_payload(to: str, card: Card) -> dict[str, Any] | None:
    """The Cloud API body for one card, or None when it has no buttons."""
    buttons = card.buttons()
    body = card.headline()
    if card.effect and card.state == "proposed":
        body = f"{body}\n\n{card.effect}"
    if not buttons:
        return {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": body[:4096]},
        }
    return {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:MAX_BODY]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": button_id(card.event_id, verb),
                            "title": label[:MAX_TITLE],
                        },
                    }
                    for verb, label in buttons[:3]
                ]
            },
        },
    }


class WhatsAppAdapter:
    name = WHATSAPP

    def enabled(self, organization_id: int | None = None) -> bool:
        from api.services.messaging import platform_whatsapp

        return platform_whatsapp.is_configured()

    async def send_text(self, ref: dict[str, Any], text: str) -> bool:
        from api.services.messaging import whatsapp_inbound

        to = str(ref.get("to") or "")
        organization_id = ref.get("organization_id")
        if not to:
            return False
        if organization_id is None:
            # Before an account is known (a link reply): not charged to anyone.
            return await self._post(
                {
                    "messaging_product": "whatsapp",
                    "to": to.lstrip("+"),
                    "type": "text",
                    "text": {"body": text[:4096]},
                }
            )
        await whatsapp_inbound.reply(
            organization_id=int(organization_id), to=to, body=text
        )
        return True

    async def send_card(self, ref: dict[str, Any], card: Card) -> bool:
        to = str(ref.get("to") or "")
        payload = interactive_payload(to, card) if to else None
        return await self._post(payload) if payload else False

    async def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        # WhatsApp has no "button answered" call; the card that follows is
        # the acknowledgement.
        return None

    async def _post(self, payload: dict[str, Any]) -> bool:
        from api.services.messaging import platform_whatsapp

        if not platform_whatsapp.is_configured():
            return False
        creds = platform_whatsapp.credentials()
        version = creds.get("graph_version") or "v21.0"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"https://graph.facebook.com/{version}/{creds['phone_number_id']}/messages",
                    headers={"Authorization": f"Bearer {creds['access_token']}"},
                    json=payload,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("WhatsApp card send failed: {}", exc)
            return False
        if response.status_code >= 400:
            logger.warning(
                "WhatsApp refused a card: {} {}", response.status_code, response.text[:300]
            )
            return False
        return True


ADAPTER = WhatsAppAdapter()

"""Telegram: a Decibyl bot (@BotFather), text and inline-keyboard cards.

Setup is LAUNCH-6 (KAN-278): ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_WEBHOOK_SECRET`` in the environment. Telegram sends the secret in
``X-Telegram-Bot-Api-Secret-Token`` on every update, which is the only proof
an update came from Telegram. ``register_webhook`` points the bot at us.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import httpx
from loguru import logger

from .base import TELEGRAM, Card, Inbound, button_id, parse_button_id

TIMEOUT_SECONDS = 15
API = "https://api.telegram.org"


def token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def webhook_secret() -> str:
    return os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()


def bot_username() -> str:
    return os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")


def verify(header: str | None) -> bool:
    secret = webhook_secret()
    if not secret or not header:
        return False
    return hmac.compare_digest(secret, header.strip())


def parse(update: Any) -> Inbound | None:
    """One Telegram update, or None for anything that is not a private
    message or a button press (group chats are not Decibyl's)."""
    if not isinstance(update, dict):
        return None
    update_id = str(update.get("update_id") or "")
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return None
        tap = parse_button_id(callback.get("data"))
        return Inbound(
            channel=TELEGRAM,
            external_id=str(chat.get("id")),
            message_id=f"cb:{callback.get('id') or update_id}",
            tap=tap,
            display_name=_name(callback.get("from") or {}),
            ref={"chat_id": chat.get("id")},
            extra={"callback_query_id": callback.get("id")},
        )
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return None
    return Inbound(
        channel=TELEGRAM,
        external_id=str(chat.get("id")),
        message_id=f"m:{chat.get('id')}:{message.get('message_id') or update_id}",
        text=str(message.get("text") or message.get("caption") or ""),
        display_name=_name(message.get("from") or {}),
        ref={"chat_id": chat.get("id")},
    )


def _name(user: dict[str, Any]) -> str:
    return " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    ) or str(user.get("username") or "")


def keyboard(card: Card) -> dict[str, Any] | None:
    buttons = card.buttons()
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": button_id(card.event_id, verb)}
                for verb, label in buttons
            ]
        ]
    }


class TelegramAdapter:
    name = TELEGRAM

    def enabled(self, organization_id: int | None = None) -> bool:
        return bool(token())

    async def send_text(self, ref: dict[str, Any], text: str) -> bool:
        chat_id = ref.get("chat_id") or ref.get("to")
        if not chat_id or not text.strip():
            return False
        return await self._call(
            "sendMessage", {"chat_id": chat_id, "text": text[:4096]}
        )

    async def send_card(self, ref: dict[str, Any], card: Card) -> bool:
        chat_id = ref.get("chat_id") or ref.get("to")
        if not chat_id:
            return False
        text = card.headline()
        if card.effect and card.state == "proposed":
            text = f"{text}\n\n{card.effect}"
        body: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        markup = keyboard(card)
        if markup:
            body["reply_markup"] = markup
        return await self._call("sendMessage", body)

    async def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        callback_id = inbound.extra.get("callback_query_id")
        if callback_id:
            await self._call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": note[:200]},
            )

    async def _call(self, method: str, body: dict[str, Any]) -> bool:
        if not token():
            return False
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(f"{API}/bot{token()}/{method}", json=body)
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram {} failed: {}", method, exc)
            return False
        if response.status_code >= 400:
            logger.warning(
                "Telegram refused {}: {} {}",
                method,
                response.status_code,
                response.text[:300],
            )
            return False
        return True


async def register_webhook(base_url: str) -> bool:
    """Point the bot at ``{base_url}/api/v1/public/telegram/webhook``."""
    if not token() or not webhook_secret():
        return False
    return await ADAPTER._call(
        "setWebhook",
        {
            "url": f"{base_url.rstrip('/')}/api/v1/public/telegram/webhook",
            "secret_token": webhook_secret(),
            "allowed_updates": ["message", "callback_query"],
        },
    )


ADAPTER = TelegramAdapter()

"""Answer one Instagram message, off the webhook's request."""

from __future__ import annotations

from api.services.messaging import instagram


async def answer_instagram_message(
    _ctx, account_ig_id: str, sender_id: str, message_id: str, text: str
) -> str:
    return await instagram.handle(
        instagram.Incoming(
            account_ig_id=account_ig_id,
            sender_id=sender_id,
            message_id=message_id,
            text=text,
        )
    )

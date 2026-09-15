"""Sunday, 09:00 IST: the memory review for the people who asked for it."""

from __future__ import annotations

from api.services.knowledge_graph import sunday_review


async def send_sunday_reviews(_ctx=None) -> dict[str, int]:
    return await sunday_review.send_reviews()

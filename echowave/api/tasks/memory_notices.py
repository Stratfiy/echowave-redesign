"""Daily, 09:30 IST: what memory may say unasked, under its switches and
the one-message-a-day cap -- a connection it noticed (B4), a fact
resurfaced before a related event (B6)."""

from __future__ import annotations

from api.services.knowledge_graph import connections, spaced_recall


async def notice_connections(_ctx=None) -> dict[str, int]:
    return await connections.notice()


async def resurface_asked(_ctx=None) -> dict[str, int]:
    return await spaced_recall.resurface()

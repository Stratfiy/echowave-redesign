"""The rail's Recents: the conversations somebody was last in.

Two kinds, one list, newest first -- the way ChatGPT's sidebar works:

* **Decibyl chats**, one row per thread, titled by its first line.
* **Agent chats**, one row per agent, under the agent's name with its last
  message beneath.

This replaced a list of every agent (Colleagues), which repeated the Agents
page one click below it. A recents list answers the question the rail is
actually asked: "where was I?"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from api.db import db_client
from api.db.models import WorkflowModel
from api.enums import WorkflowStatus
from api.schemas.agent_avatar import read_avatar

#: Untitled Decibyl chats still need a name in a list.
UNTITLED = "New chat"


def _avatar(raw: Any) -> Optional[dict]:
    face = read_avatar(raw)
    return face.model_dump(mode="json") if face is not None else None


def _title(text: str, limit: int = 60) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else f"{line[: limit - 1].rstrip()}…"


async def recents(
    *,
    organization_id: int,
    viewer_id: Optional[int],
    viewer_is_admin: bool,
    limit: int = 12,
) -> list[dict[str, Any]]:
    threads = await db_client.assistant_threads(
        organization_id=organization_id,
        limit=limit,
        viewer_id=viewer_id,
        viewer_is_admin=viewer_is_admin,
    )
    agents = await db_client.recent_agent_conversations(
        organization_id=organization_id, limit=limit
    )

    names: dict[int, tuple[str, Any]] = {}
    ids = [a["workflow_id"] for a in agents]
    if ids:
        async with db_client.async_session() as session:
            rows = await session.execute(
                select(
                    WorkflowModel.id, WorkflowModel.name, WorkflowModel.avatar
                ).where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowModel.id.in_(ids),
                    WorkflowModel.status == WorkflowStatus.ACTIVE.value,
                )
            )
            names = {row.id: (row.name, row.avatar) for row in rows}

    items: list[tuple[datetime, dict[str, Any]]] = []
    for thread in threads:
        thread_id = thread.get("thread_id")
        items.append(
            (
                thread["last_at"],
                {
                    "kind": "decibyl",
                    "key": f"t-{thread_id or 'main'}",
                    "title": _title(thread.get("title") or "") or UNTITLED,
                    "subtitle": None,
                    "href": f"/overview?thread={thread_id}"
                    if thread_id
                    else "/overview",
                    "workflow_id": None,
                    "avatar": None,
                    "last_at": thread["last_at"],
                },
            )
        )
    for agent in agents:
        workflow_id = agent["workflow_id"]
        if workflow_id not in names:
            # Archived, or not this workspace's: not somewhere to go back to.
            continue
        name, avatar = names[workflow_id]
        items.append(
            (
                agent["at"],
                {
                    "kind": "agent",
                    "key": f"a-{workflow_id}",
                    "title": name,
                    "subtitle": _title(agent["summary"], 48) or None,
                    "href": f"/workflow/{workflow_id}/thread",
                    "workflow_id": workflow_id,
                    "avatar": _avatar(avatar),
                    "last_at": agent["at"],
                },
            )
        )
    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in items[:limit]]

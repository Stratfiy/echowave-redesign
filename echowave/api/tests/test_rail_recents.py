"""The rail's Recents: Decibyl chats and agent chats, newest first."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, WorkflowModel
from api.services.workflow import recents


@pytest.mark.asyncio
async def test_both_kinds_interleave_newest_first(db_session, async_session):
    org = OrganizationModel(provider_id=f"recents-{datetime.now(UTC).timestamp()}")
    async_session.add(org)
    await async_session.flush()
    riya = WorkflowModel(
        name="Riya",
        organization_id=org.id,
        workflow_definition={"nodes": [], "edges": []},
    )
    old = WorkflowModel(
        name="Gone",
        organization_id=org.id,
        status="archived",
        workflow_definition={"nodes": [], "edges": []},
    )
    async_session.add_all([riya, old])
    await async_session.commit()

    now = datetime.now(UTC)
    threads = [
        {
            "thread_id": "abc",
            "title": "Plan my week",
            "last_at": now - timedelta(minutes=5),
            "messages": 3,
        },
        {
            "thread_id": None,
            "title": "",
            "last_at": now - timedelta(days=2),
            "messages": 1,
        },
    ]
    agents = [
        {
            "workflow_id": riya.id,
            "summary": "Booked Mr Rao for 4pm",
            "at": now - timedelta(minutes=1),
        },
        {"workflow_id": old.id, "summary": "old", "at": now},
    ]
    with (
        patch.object(
            recents.db_client, "assistant_threads", AsyncMock(return_value=threads)
        ),
        patch.object(
            recents.db_client,
            "recent_agent_conversations",
            AsyncMock(return_value=agents),
        ),
    ):
        items = await recents.recents(
            organization_id=org.id, viewer_id=None, viewer_is_admin=False
        )

    assert [i["title"] for i in items] == ["Riya", "Plan my week", "New chat"]
    assert items[0]["href"] == f"/workflow/{riya.id}/thread"
    assert items[0]["subtitle"] == "Booked Mr Rao for 4pm"
    assert items[1]["href"] == "/overview?thread=abc"
    assert items[2]["href"] == "/overview"

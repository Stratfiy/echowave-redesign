"""The last line on a bot's timeline reaches the team status.

`latest_event_per_workflow` was written "for the sidebar" and nothing called
it; the rail showed a dot and no line. This is the arrival test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.team import _members


@pytest.mark.asyncio
async def test_the_last_line_reaches_the_member():
    at = datetime(2026, 9, 14, 6, 30, tzinfo=UTC)
    workflows = [
        SimpleNamespace(id=3, workflow_uuid="u3", name="Front desk", is_live=True),
        SimpleNamespace(id=4, workflow_uuid="u4", name="Quiet one", is_live=False),
    ]
    with (
        patch(
            "api.routes.team.db_client.get_all_workflows_for_listing",
            new=AsyncMock(return_value=workflows),
        ),
        patch(
            "api.routes.team.db_client.agent_activity", new=AsyncMock(return_value={})
        ),
        patch(
            "api.routes.team.db_client.latest_event_per_workflow",
            new=AsyncMock(
                return_value={
                    3: {
                        "kind": "message",
                        "actor": "agent",
                        "summary": "Booked Meera for 4pm.",
                        "at": at,
                    }
                }
            ),
        ) as latest,
    ):
        members = {
            m.workflow_id: m for m in await _members(organization_id=7, hours=24)
        }

    assert latest.await_args.kwargs["workflow_ids"] == [3, 4]
    assert members[3].last_line == "Booked Meera for 4pm."
    assert members[3].last_at == at
    assert members[3].last_actor == "agent"
    # A bot with no history says nothing rather than something made up.
    assert members[4].last_line is None and members[4].last_at is None

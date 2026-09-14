"""A bad publish has an undo: any version can be copied into the draft.

Arrival tests: the chosen version's snapshot becomes the draft and nothing
goes live; the draft itself cannot be restored; a version from another
bot's history is not found.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _version(id, status="published", number=3):
    return SimpleNamespace(
        id=id,
        version_number=number,
        status=status,
        created_at=datetime.now(UTC),
        published_at=None,
        workflow_json={"nodes": [{"id": "a"}], "edges": []},
        workflow_configurations={"k": "v"},
        template_context_variables={"name": "x"},
    )


async def _post(path):
    from api.app import app
    from api.services.auth.depends import get_user

    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=42, selected_organization_id=7
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(path)
    finally:
        app.dependency_overrides.pop(get_user, None)


@pytest.mark.asyncio
class TestRestoring:
    async def test_the_snapshot_becomes_the_draft_and_nothing_goes_live(self):
        draft = _version(10, status="draft", number=5)
        with (
            patch(
                "api.routes.workflow.db_client.get_workflow",
                new=AsyncMock(return_value=SimpleNamespace(id=3)),
            ),
            patch(
                "api.routes.workflow.db_client.get_workflow_version",
                new=AsyncMock(return_value=_version(8)),
            ) as get,
            patch(
                "api.routes.workflow.db_client.save_workflow_draft",
                new=AsyncMock(return_value=draft),
            ) as save,
            patch(
                "api.routes.workflow.db_client.publish_workflow_draft",
                new=AsyncMock(),
            ) as publish,
        ):
            response = await _post("/api/v1/workflow/3/versions/8/restore")
        assert response.status_code == 200
        assert get.await_args.args == (3, 8)
        assert save.await_args.args == (3,)
        assert save.await_args.kwargs["workflow_definition"] == {
            "nodes": [{"id": "a"}],
            "edges": [],
        }
        assert save.await_args.kwargs["workflow_configurations"] == {"k": "v"}
        publish.assert_not_awaited()
        body = response.json()
        assert body["status"] == "draft" and body["version_number"] == 5

    async def test_the_draft_itself_cannot_be_restored(self):
        with (
            patch(
                "api.routes.workflow.db_client.get_workflow",
                new=AsyncMock(return_value=SimpleNamespace(id=3)),
            ),
            patch(
                "api.routes.workflow.db_client.get_workflow_version",
                new=AsyncMock(return_value=_version(10, status="draft")),
            ),
        ):
            response = await _post("/api/v1/workflow/3/versions/10/restore")
        assert response.status_code == 409

    async def test_another_bots_version_is_not_here(self):
        with (
            patch(
                "api.routes.workflow.db_client.get_workflow",
                new=AsyncMock(return_value=SimpleNamespace(id=3)),
            ),
            patch(
                "api.routes.workflow.db_client.get_workflow_version",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = await _post("/api/v1/workflow/3/versions/99/restore")
        assert response.status_code == 404

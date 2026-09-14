"""Knowledge has a scope: the company's, a channel's, a bot's, or the library.

Before this every document sat on a shelf, and no bot read any of it unless
a node named the document by uuid. These are the arrival tests: a bot with
no documents of its own still gets the retrieval tool when the organisation
has knowledge for it; a scope is checked against the caller's own tenancy
before it is stored; and a file dropped into a chat is a message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
    knowledge_for_run,
)


def _node(document_uuids=None):
    return SimpleNamespace(
        document_uuids=document_uuids,
        tool_uuids=None,
        mcp_tool_filters=None,
        out_edges=[],
        node_type="agent",
    )


class TestTheUnion:
    def test_the_nodes_documents_come_first_and_nothing_repeats(self):
        assert knowledge_for_run(["a", "b"], ["b", "c", "a"]) == ["a", "b", "c"]

    def test_nothing_named_and_nothing_scoped_is_nothing(self):
        assert knowledge_for_run(None, None) == []
        assert knowledge_for_run([], [""]) == []


@pytest.mark.asyncio
class TestTheToolIsOffered:
    async def test_a_node_with_no_documents_still_reads_company_knowledge(self):
        functions = await compose_functions_for_node(
            node=_node(None),
            custom_tool_manager=None,
            scoped_document_uuids=["org-doc"],
        )
        names = [getattr(f, "name", None) for f in functions]
        assert any(n and "knowledge" in n for n in names), names

    async def test_no_knowledge_anywhere_means_no_tool(self):
        functions = await compose_functions_for_node(
            node=_node(None), custom_tool_manager=None, scoped_document_uuids=[]
        )
        names = [getattr(f, "name", None) for f in functions]
        assert not any(n and "knowledge" in n for n in names), names


def _user():
    return SimpleNamespace(id=42, selected_organization_id=7, provider_id="p-42")


@pytest.mark.asyncio
class TestTheScopeIsChecked:
    """Through the endpoint: a folder or workflow id from another tenant would
    otherwise make a document readable by that tenant's bots."""

    async def _post(self, body, *, folder=None, workflow=None):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = _user
        created = AsyncMock(
            return_value=SimpleNamespace(
                id=1,
                document_uuid=body["document_uuid"],
                filename="x.pdf",
                file_size_bytes=0,
                file_hash="",
                mime_type="application/octet-stream",
                processing_status="pending",
                processing_error=None,
                needs_reingest=False,
                total_chunks=0,
                retrieval_mode="chunked",
                created_at=datetime(2026, 9, 14, tzinfo=UTC),
                updated_at=datetime(2026, 9, 14, tzinfo=UTC),
                custom_metadata={},
                docling_metadata={},
                source_url=None,
                created_by=42,
                scope=body.get("scope", "library"),
                folder_id=body.get("folder_id"),
                workflow_id=body.get("workflow_id"),
                is_active=True,
                organization_id=7,
            )
        )
        try:
            with (
                patch(
                    "api.routes.knowledge_base.upload_keys.key_belongs_to",
                    return_value=True,
                ),
                patch(
                    "api.routes.knowledge_base._assert_room_to_ingest", new=AsyncMock()
                ),
                patch(
                    "api.routes.knowledge_base.db_client.get_folder",
                    new=AsyncMock(return_value=folder),
                ),
                patch(
                    "api.routes.knowledge_base.db_client.get_workflow",
                    new=AsyncMock(return_value=workflow),
                ),
                patch(
                    "api.routes.knowledge_base.db_client.create_document", new=created
                ),
                patch("api.routes.knowledge_base.enqueue_job", new=AsyncMock()),
                patch("api.routes.knowledge_base.capture_event"),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/knowledge-base/process-document", json=body
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        return response, created

    async def test_company_knowledge_is_stored_as_the_organisations(self):
        response, created = await self._post(
            {
                "document_uuid": "d1",
                "s3_key": "knowledge_base/7/d1/policy.pdf",
                "scope": "org",
            }
        )
        assert response.status_code == 200, response.text
        assert created.await_args.kwargs["scope"] == "org"
        assert created.await_args.kwargs["folder_id"] is None

    async def test_a_channel_document_needs_a_channel_of_this_organisation(self):
        missing, _ = await self._post(
            {"document_uuid": "d1", "s3_key": "k", "scope": "channel"}
        )
        assert missing.status_code == 422
        foreign, created = await self._post(
            {"document_uuid": "d1", "s3_key": "k", "scope": "channel", "folder_id": 99},
            folder=None,
        )
        assert foreign.status_code == 404
        assert not created.await_count

    async def test_a_bot_document_is_filed_under_the_bot(self):
        response, created = await self._post(
            {"document_uuid": "d1", "s3_key": "k", "scope": "bot", "workflow_id": 3},
            workflow=SimpleNamespace(id=3),
        )
        assert response.status_code == 200, response.text
        assert created.await_args.kwargs["workflow_id"] == 3
        assert created.await_args.kwargs["scope"] == "bot"

    async def test_an_unknown_scope_is_refused(self):
        response, _ = await self._post(
            {"document_uuid": "d1", "s3_key": "k", "scope": "everyone"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestAFileIsAMessage:
    async def _post(self, body, *, document):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = _user
        workflow = SimpleNamespace(id=3, name="Front desk", folder_id=5)
        try:
            with (
                patch(
                    "api.routes.agent_timeline.db_client.get_workflow",
                    new=AsyncMock(return_value=workflow),
                ),
                patch(
                    "api.routes.agent_timeline.db_client.get_document_by_uuid",
                    new=AsyncMock(return_value=document),
                ),
                patch(
                    "api.routes.agent_timeline.agent_timeline.record", new=AsyncMock()
                ) as record,
                patch(
                    "api.routes.agent_timeline.enqueue_job", new=AsyncMock()
                ) as enqueue,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post("/api/v1/timeline/message", json=body)
        finally:
            app.dependency_overrides.pop(get_user, None)
        return response, record, enqueue

    async def test_an_attachment_with_no_words_is_still_said(self):
        response, record, enqueue = await self._post(
            {
                "workflow_id": 3,
                "attachments": [
                    {"document_uuid": "d1", "filename": "rates.pdf", "size_bytes": 12}
                ],
            },
            document=SimpleNamespace(filename="rates.pdf", file_size_bytes=12),
        )
        assert response.status_code == 200, response.text
        kwargs = record.await_args.kwargs
        assert kwargs["summary"] == "Shared rates.pdf"
        assert kwargs["payload"]["attachments"][0]["document_uuid"] == "d1"
        # The bot is told what was shared, so its turn is not an empty line.
        assert enqueue.await_args.args[-1] == "Shared rates.pdf"

    async def test_nothing_at_all_is_refused(self):
        response, record, _ = await self._post(
            {"workflow_id": 3, "text": "  "}, document=None
        )
        assert response.status_code == 422
        assert not record.await_count

    async def test_someone_elses_document_is_not_a_file_here(self):
        response, record, _ = await self._post(
            {
                "workflow_id": 3,
                "text": "see this",
                "attachments": [{"document_uuid": "x", "filename": "x"}],
            },
            document=None,
        )
        assert response.status_code == 404
        assert not record.await_count

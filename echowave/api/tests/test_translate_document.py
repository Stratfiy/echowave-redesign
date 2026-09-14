"""A document in another language, made from one in the library."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services.knowledge_base import translate_document as td

MOD = "api.services.knowledge_base.translate_document"


class TestTheWords:
    def test_full_text_wins_and_chunks_are_the_fallback_in_order(self):
        assert td.text_of(SimpleNamespace(full_text=" whole "), []) == "whole"
        chunks = [
            SimpleNamespace(chunk_index=1, chunk_text="two"),
            SimpleNamespace(chunk_index=0, chunk_text="one"),
        ]
        assert td.text_of(SimpleNamespace(full_text=None), chunks) == "one\n\ntwo"

    def test_the_copy_is_named_for_its_language(self):
        assert td.translated_filename("rates.pdf", "en-IN") == "rates (English).txt"
        assert td.translated_filename("notes", "ta-IN") == "notes (Tamil).txt"


@pytest.mark.asyncio
class TestStarting:
    async def test_makes_a_pending_row_in_the_same_scope_and_queues_the_job(self):
        source = SimpleNamespace(
            id=5,
            document_uuid="src",
            filename="rates.pdf",
            processing_status="completed",
            retrieval_mode="full_document",
            scope="channel",
            folder_id=9,
            workflow_id=None,
        )
        created = AsyncMock(return_value=SimpleNamespace(id=6))
        with (
            patch(
                f"{MOD}.db_client.get_document_by_uuid",
                new=AsyncMock(return_value=source),
            ),
            patch(f"{MOD}.db_client.create_document", new=created),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            await td.start(
                source_uuid="src",
                organization_id=7,
                user_id=42,
                provider_id="p",
                target="en-IN",
            )
        kwargs = created.await_args.kwargs
        assert kwargs["filename"] == "rates (English).txt"
        assert kwargs["scope"] == "channel" and kwargs["folder_id"] == 9
        assert kwargs["custom_metadata"]["translated_from"] == "src"
        assert kwargs["custom_metadata"]["s3_key"].startswith("knowledge_base/7/")
        assert enqueue.await_args.args[1:] == (6, 5, 7, "p", "en-IN")

    async def test_an_unread_document_is_refused(self):
        source = SimpleNamespace(processing_status="processing", filename="x")
        with patch(
            f"{MOD}.db_client.get_document_by_uuid", new=AsyncMock(return_value=source)
        ):
            with pytest.raises(td.NotTranslatable, match="read"):
                await td.start(
                    source_uuid="src",
                    organization_id=7,
                    user_id=1,
                    provider_id="p",
                    target="en-IN",
                )

    async def test_an_unknown_language_is_refused(self):
        with pytest.raises(td.NotTranslatable):
            await td.start(
                source_uuid="src",
                organization_id=7,
                user_id=1,
                provider_id="p",
                target="fr-FR",
            )


@pytest.mark.asyncio
class TestTheJob:
    def _docs(self):
        target = SimpleNamespace(
            id=6,
            document_uuid="new",
            filename="rates (English).txt",
            retrieval_mode="chunked",
            custom_metadata={"s3_key": "knowledge_base/7/new/rates (English).txt"},
        )
        source = SimpleNamespace(id=5, full_text=None)
        return target, source

    async def test_translates_writes_the_file_and_queues_ingestion(self):
        target, source = self._docs()
        chunks = [SimpleNamespace(chunk_index=0, chunk_text="விலை பட்டியல்")]
        written = AsyncMock(return_value=True)
        with (
            patch(
                f"{MOD}.db_client.get_document_by_id",
                new=AsyncMock(side_effect=[target, source]),
            ),
            patch(
                f"{MOD}.db_client.get_chunks_for_document",
                new=AsyncMock(return_value=chunks),
            ),
            patch(
                f"{MOD}.translation.translate",
                new=AsyncMock(return_value=("Price list", "ta-IN")),
            ),
            patch(f"{MOD}.storage_fs.acreate_file_from_bytes", new=written),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
            patch(f"{MOD}.db_client.update_document_status", new=AsyncMock()) as status,
        ):
            await td.run(
                document_id=6,
                source_id=5,
                organization_id=7,
                provider_id="p",
                target="en-IN",
            )
        assert written.await_args.args == (
            "knowledge_base/7/new/rates (English).txt",
            b"Price list",
        )
        assert enqueue.await_args.args[1:4] == (
            6,
            "knowledge_base/7/new/rates (English).txt",
            7,
        )
        assert not status.await_count

    async def test_a_failure_is_written_on_the_row(self):
        target, source = self._docs()
        with (
            patch(
                f"{MOD}.db_client.get_document_by_id",
                new=AsyncMock(side_effect=[target, source]),
            ),
            patch(
                f"{MOD}.db_client.get_chunks_for_document",
                new=AsyncMock(return_value=[]),
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
            patch(f"{MOD}.db_client.update_document_status", new=AsyncMock()) as status,
        ):
            await td.run(
                document_id=6,
                source_id=5,
                organization_id=7,
                provider_id="p",
                target="en-IN",
            )
        assert status.await_args.args[:2] == (6, "failed")
        assert "no text" in status.await_args.kwargs["error_message"]
        assert not enqueue.await_count


@pytest.mark.asyncio
class TestTheRoute:
    async def test_returns_the_pending_copy(self):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=42, selected_organization_id=7, provider_id="p"
        )
        now = datetime(2026, 9, 14, tzinfo=UTC)
        doc = SimpleNamespace(
            id=6,
            document_uuid="new",
            filename="rates (English).txt",
            retrieval_mode="chunked",
            custom_metadata={"translated_from": "src"},
            scope="org",
            folder_id=None,
            workflow_id=None,
            created_at=now,
            updated_at=now,
        )
        try:
            with (
                patch(
                    "api.routes.knowledge_base._assert_room_to_ingest", new=AsyncMock()
                ),
                patch(
                    "api.routes.knowledge_base.translate_document.start",
                    new=AsyncMock(return_value=doc),
                ) as start,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/knowledge-base/documents/src/translate",
                        json={"target_language_code": "hi-IN"},
                    )
        finally:
            app.dependency_overrides.pop(get_user, None)
        assert response.status_code == 202, response.text
        assert response.json()["processing_status"] == "pending"
        assert response.json()["custom_metadata"]["translated_from"] == "src"
        assert start.await_args.kwargs["target"] == "hi-IN"

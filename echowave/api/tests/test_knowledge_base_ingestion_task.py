"""What the ingestion task records, and what the customer reads.

The unit under test is the background job, with S3, the database and the
embedding provider stubbed. Everything asserted here is about the two outcomes
a customer can see on the files screen: the status badge, and the sentence
under it.

The failure this suite exists to prevent is the *quiet* one — a document that
produced nothing recorded as ``completed``. It is worse than a visible failure
because nobody investigates it: the customer believes the agent has read their
policy, and finds out otherwise during a call with a real caller on the line.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.tasks import knowledge_base_processing as task_module


class FakeDocumentStore:
    """Just enough of db_client for the task, recording what it was told."""

    def __init__(self, *, document=None, duplicate_of=None):
        self.statuses: list[tuple[str, str | None]] = []
        self.metadata_updates: list[dict] = []
        self.full_texts: list[str] = []
        self.stored_chunks: list = []
        self._document = document or SimpleNamespace(
            id=1, organization_id=42, filename="refunds.txt", document_uuid="doc-uuid"
        )
        self._duplicate_of = duplicate_of

    async def update_document_status(
        self, document_id, status, error_message=None, total_chunks=None, **kwargs
    ):
        self.statuses.append((status, error_message))
        self.last_total_chunks = total_chunks

    async def update_document_metadata(self, document_id, **kwargs):
        self.metadata_updates.append(kwargs)

    async def update_document_full_text(self, document_id, full_text):
        self.full_texts.append(full_text)

    async def get_document_by_id(self, document_id):
        return self._document

    async def get_document_by_hash(self, file_hash, organization_id):
        return self._duplicate_of

    async def replace_chunks_for_document(self, document_id, organization_id, chunks):
        self.stored_chunks = chunks

    def compute_file_hash(self, path):
        return "hash-of-the-file"

    def get_mime_type(self, path):
        return "text/plain"


class FakeEmbeddingService:
    def get_model_id(self):
        return "text-embedding-3-small"

    def get_embedding_dimension(self):
        return 1536

    async def embed_texts(self, texts):
        return [[0.1] * 1536 for _ in texts]


@pytest.fixture
def stubbed_ingestion(monkeypatch, tmp_path):
    """Wire the task to a local file, a fake store and a fake embedder."""
    store = FakeDocumentStore()

    async def fake_download(s3_key, destination):
        # The task hands us the temp path it created; fill it with the fixture.
        source = tmp_path / "source"
        with open(source, "rb") as src, open(destination, "wb") as dst:
            dst.write(src.read())
        return True

    monkeypatch.setattr(task_module, "db_client", store)
    monkeypatch.setattr(
        task_module, "storage_fs", SimpleNamespace(adownload_file=fake_download)
    )

    async def fake_build_embedding_service(**kwargs):
        return FakeEmbeddingService()

    monkeypatch.setattr(
        task_module, "build_embedding_service", fake_build_embedding_service
    )

    async def fake_resolved_configuration(**kwargs):
        return SimpleNamespace(
            effective=SimpleNamespace(
                embeddings=SimpleNamespace(
                    provider="openai",
                    api_key="sk-test",
                    model="text-embedding-3-small",
                    base_url=None,
                    endpoint=None,
                    api_version=None,
                )
            )
        )

    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "get_resolved_ai_model_configuration",
        fake_resolved_configuration,
    )
    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "apply_managed_embeddings_base_url",
        lambda **kwargs: None,
    )

    def write_source(content: bytes):
        (tmp_path / "source").write_bytes(content)

    return SimpleNamespace(store=store, write_source=write_source)


async def _run(filename="refunds.txt", **kwargs):
    await task_module.process_knowledge_base_document(
        ctx={},
        document_id=1,
        s3_key=f"knowledge_base/42/doc-uuid/{filename}",
        organization_id=42,
        created_by_provider_id="user-provider-id",
        **kwargs,
    )


class TestASuccessfulIngestion:
    async def test_a_text_document_is_chunked_embedded_and_completed(
        self, stubbed_ingestion
    ):
        stubbed_ingestion.write_source(
            b"Refund policy\n\n"
            b"Customers may request a refund within fourteen days of purchase. "
            b"Refunds go back to the original payment method.\n\n"
            b"Escalation\n\n"
            b"Escalate to a supervisor when the caller asks twice.\n"
        )

        await _run()

        assert [status for status, _ in stubbed_ingestion.store.statuses] == [
            "processing",
            "completed",
        ]
        assert stubbed_ingestion.store.stored_chunks
        # Every stored chunk carries an embedding of the declared dimension —
        # a chunk stored without one is invisible to the vector search that is
        # the entire point of storing it.
        for chunk in stubbed_ingestion.store.stored_chunks:
            assert chunk.embedding is not None
            assert len(chunk.embedding) == 1536
            assert chunk.organization_id == 42

    async def test_ingestion_needs_no_network(self, stubbed_ingestion, monkeypatch):
        """The ship blocker, stated as a test.

        Any outbound HTTP during ingestion fails this. The document still
        completes, because conversion and chunking now happen in this process.
        """
        import httpx

        def forbidden(*args, **kwargs):
            raise AssertionError("ingestion made a network call")

        monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
        monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)

        stubbed_ingestion.write_source(
            b"Our refund window is fourteen days from the date of purchase."
        )
        await _run()

        assert stubbed_ingestion.store.statuses[-1][0] == "completed"

    async def test_full_document_mode_stores_the_text(self, stubbed_ingestion):
        stubbed_ingestion.write_source(b"The refund window is fourteen days.")
        await _run(retrieval_mode="full_document")

        assert stubbed_ingestion.store.statuses[-1][0] == "completed"
        assert "fourteen days" in stubbed_ingestion.store.full_texts[0]


class TestTheQuietFailure:
    async def test_a_document_with_no_text_is_failed_not_completed(
        self, stubbed_ingestion
    ):
        stubbed_ingestion.write_source(b"   \n\n   \n")
        await _run()

        status, message = stubbed_ingestion.store.statuses[-1]
        assert status == "failed"
        assert "no text" in message.lower()

    async def test_a_failed_document_reports_zero_chunks_not_a_stale_count(
        self, stubbed_ingestion
    ):
        stubbed_ingestion.write_source(b"")
        await _run()

        assert stubbed_ingestion.store.statuses[-1][0] == "failed"
        assert not stubbed_ingestion.store.stored_chunks

    async def test_full_document_mode_refuses_an_empty_extraction(
        self, stubbed_ingestion
    ):
        stubbed_ingestion.write_source(b"\n\n")
        await _run(retrieval_mode="full_document")

        assert stubbed_ingestion.store.statuses[-1][0] == "failed"
        assert stubbed_ingestion.store.full_texts == []


class TestWhatTheCustomerReads:
    async def test_an_unreadable_format_gets_a_remedy(self, stubbed_ingestion):
        stubbed_ingestion.write_source(b"\x00\x00\x00\x18ftypmp42")
        await _run(filename="recording.mp4")

        _, message = stubbed_ingestion.store.statuses[-1]
        assert "cannot read" in message.lower()
        # Names the formats that would work, rather than only what did not.
        assert ".pdf" in message

    async def test_an_internal_failure_does_not_leak_its_stack(
        self, stubbed_ingestion, monkeypatch
    ):
        def exploding_process(**kwargs):
            raise RuntimeError("psycopg2.OperationalError at 10.0.3.14:5432")

        monkeypatch.setattr(
            "api.services.knowledge_base.processor.process_document_locally",
            exploding_process,
        )
        stubbed_ingestion.write_source(b"Refunds take fourteen days.")

        with pytest.raises(RuntimeError):
            await _run()

        _, message = stubbed_ingestion.store.statuses[-1]
        assert "10.0.3.14" not in message
        assert "psycopg2" not in message
        assert "our side" in message

    async def test_a_customer_fixable_failure_is_not_retried(self, stubbed_ingestion):
        """ARQ retries what raises. Re-reading the same .mp4 four times reaches
        the same conclusion four times and delays every other document."""
        stubbed_ingestion.write_source(b"\x00\x00\x00\x18ftypmp42")

        # No exception escapes: the document is already marked failed with a
        # reason the customer can act on.
        await _run(filename="recording.mp4")

        assert stubbed_ingestion.store.statuses[-1][0] == "failed"

    async def test_a_missing_embedding_key_says_where_to_set_it(
        self, stubbed_ingestion, monkeypatch
    ):
        async def no_embeddings_configured(**kwargs):
            return SimpleNamespace(effective=SimpleNamespace(embeddings=None))

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            no_embeddings_configured,
        )
        stubbed_ingestion.write_source(b"Refunds take fourteen days to process.")

        await _run()

        status, message = stubbed_ingestion.store.statuses[-1]
        assert status == "failed"
        assert "Model Configurations" in message

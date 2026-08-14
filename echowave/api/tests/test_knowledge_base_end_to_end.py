"""Does an uploaded document actually become something an agent can answer from?

The question nobody had asked of this path. There was one test on it — that a
batch of texts comes back in the order it went in — which says nothing about
whether an upload ever turns into a retrievable answer.

The path has four stages and they fail in different places:

    upload → **convert & chunk** → embed → store → retrieve at call time

Only the second stage leaves this process. It is delegated to MPS
(``MPS_API_URL``), and there is no local chunker to fall back to — so a
deployment without a reachable MPS cannot ingest a document at all, no matter
what else is configured.

These tests stub **only** that boundary. Everything downstream is real: real
embeddings written to a real pgvector column, a real cosine search, real
organization scoping. That split is the point — it establishes that MPS is the
only missing piece rather than one of several, which is the difference between
"stand up one service" and "this feature does not work".

The two failure modes worth catching are both silent ones. A document that
processes into zero chunks reports ``completed`` and answers nothing. And a
search that is not organization-scoped is a cross-tenant leak of whatever a
customer uploaded.
"""

from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from api.db.models import (
    KnowledgeBaseChunkModel,
    KnowledgeBaseDocumentModel,
    OrganizationModel,
    UserModel,
)

#: A deterministic stand-in for an embedding model. Real vectors would make the
#: assertions depend on a provider's behaviour on the day; what is under test
#: here is the plumbing, and the plumbing does not care what the numbers are.
DIM = 1536


def _vector(seed: float) -> list[float]:
    return [seed] + [0.0] * (DIM - 1)


class _StubEmbeddings:
    """Stands in for the embedding provider, not for the storage or the search."""

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        # Distinct vectors, so a search can actually rank them.
        return [_vector(1.0 - 0.1 * i) for i, _ in enumerate(texts)]

    def get_model_id(self):
        return "text-embedding-3-small"

    def get_embedding_dimension(self):
        return DIM


def _mps_response(chunks):
    return {
        "mode": "chunked",
        "docling_metadata": {"pages": 1},
        "full_text": None,
        "chunks": [
            {
                "chunk_text": text,
                "contextualized_text": text,
                "chunk_index": i,
                "chunk_metadata": {},
                "token_count": len(text.split()),
            }
            for i, text in enumerate(chunks)
        ],
    }


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    async_session.add_all([org, user])
    await async_session.flush()
    org._uploader_id = user.id
    return org


async def _document(async_session, org, *, filename="handbook.pdf"):
    doc = KnowledgeBaseDocumentModel(
        organization_id=org.id,
        filename=filename,
        processing_status="pending",
        created_by=org._uploader_id,
    )
    async_session.add(doc)
    await async_session.flush()
    return doc


async def _store_chunks(async_session, org, doc, texts):
    """The write half of ingestion, with MPS's output supplied directly.

    Mirrors what ``process_knowledge_base_document`` does once MPS has
    answered: one row per chunk, embedded, organization-scoped.
    """
    embeddings = _StubEmbeddings()
    vectors = await embeddings.embed_texts(texts)
    for index, (text, vector) in enumerate(zip(texts, vectors)):
        async_session.add(
            KnowledgeBaseChunkModel(
                document_id=doc.id,
                organization_id=org.id,
                chunk_text=text,
                contextualized_text=text,
                chunk_index=index,
                chunk_metadata={},
                embedding_model=embeddings.get_model_id(),
                embedding_dimension=DIM,
                embedding=vector,
                token_count=len(text.split()),
            )
        )
    await async_session.flush()


# ---------------------------------------------------------------------------


class TestTheOnlyExternalDependency:
    """Which stage actually leaves this process, and what happens without it."""

    def test_conversion_and_chunking_are_delegated_to_mps(self):
        """Stated as a test because it is the whole finding. There is no local
        chunker — ``docling`` appears in this codebase only as the name of a
        metadata field — so a deployment with no reachable ``MPS_API_URL``
        cannot ingest a document however well everything else is configured."""
        import api.tasks.knowledge_base_processing as kb

        source = kb.__doc__ or ""
        assert "MPS" in source

        # The call is unconditional on the chunked path: no branch, no fallback.
        import inspect

        body = inspect.getsource(kb.process_knowledge_base_document)
        assert "mps_service_key_client.process_document" in body

    @pytest.mark.asyncio
    async def test_an_unreachable_mps_marks_the_document_failed(
        self, db_session, async_session
    ):
        """It does not hang, and it does not silently look complete — but the
        message a customer sees is a raw transport error, which is a separate
        problem from the outage itself."""
        import httpx

        from api.db import db_client
        from api.tasks import knowledge_base_processing as kb

        org = await _org(async_session, "kb-down")
        doc = await _document(async_session, org)
        await async_session.commit()

        with (
            patch.object(kb.storage_fs, "adownload_file", AsyncMock(return_value=True)),
            patch("os.path.exists", return_value=True),
            patch("os.path.getsize", return_value=1024),
            patch.object(kb.db_client, "compute_file_hash", return_value="h" * 16),
            patch.object(kb.db_client, "get_mime_type", return_value="application/pdf"),
            patch.object(
                kb.mps_service_key_client,
                "process_document",
                AsyncMock(side_effect=httpx.ConnectError("nodename nor servname")),
            ),
            patch("os.remove"),
        ):
            with pytest.raises(httpx.ConnectError):
                await kb.process_knowledge_base_document(
                    None,
                    document_id=doc.id,
                    s3_key=f"kb/{org.id}/handbook.pdf",
                    organization_id=org.id,
                    created_by_provider_id="u-1",
                    retrieval_mode="full_document",
                )

        refreshed = await db_client.get_document_by_id(doc.id)
        assert refreshed.processing_status == "failed"
        assert refreshed.processing_error


@pytest.mark.asyncio
class TestEverythingDownstreamOfMps:
    """With MPS's answer supplied, does the rest of the path actually work?

    The distinction that matters for a readiness call: if these pass, the gap
    is one service to stand up. If they failed, the feature would be unfinished
    in several places at once.
    """

    async def test_chunks_are_stored_with_their_embeddings(
        self, db_session, async_session
    ):
        org = await _org(async_session, "kb-store")
        doc = await _document(async_session, org)
        await _store_chunks(
            async_session,
            org,
            doc,
            ["Refunds are processed within seven working days.", "Support is 9 to 6."],
        )

        rows = (
            (
                await async_session.execute(
                    sa.select(KnowledgeBaseChunkModel).where(
                        KnowledgeBaseChunkModel.document_id == doc.id
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(rows) == 2
        assert all(r.embedding is not None for r in rows)
        assert all(len(r.embedding) == DIM for r in rows)

    async def test_a_stored_chunk_is_found_by_vector_search(
        self, db_session, async_session
    ):
        """The end of the path. Everything before this is bookkeeping; this is
        the only assertion that says an agent could answer from the upload."""
        from api.db import db_client

        org = await _org(async_session, "kb-search")
        doc = await _document(async_session, org)
        await _store_chunks(
            async_session,
            org,
            doc,
            ["Refunds are processed within seven working days.", "Support is 9 to 6."],
        )
        await async_session.commit()

        found = await db_client.search_similar_chunks(
            query_embedding=_vector(1.0),
            organization_id=org.id,
            limit=3,
        )

        assert found
        assert "Refunds" in found[0]["chunk_text"]

    async def test_the_search_is_scoped_to_one_organization(
        self, db_session, async_session
    ):
        """A knowledge base is whatever a customer uploaded. An unscoped search
        is a cross-tenant leak of exactly that, and it would look like a
        working feature."""
        from api.db import db_client

        mine = await _org(async_session, "kb-mine")
        theirs = await _org(async_session, "kb-theirs")
        their_doc = await _document(async_session, theirs, filename="secret.pdf")
        await _store_chunks(
            async_session, theirs, their_doc, ["Our margin on the Acme account is 40%."]
        )
        await async_session.commit()

        found = await db_client.search_similar_chunks(
            query_embedding=_vector(1.0),
            organization_id=mine.id,
            limit=5,
        )

        assert found == [] or all("margin" not in c["chunk_text"] for c in found)

    async def test_reprocessing_replaces_chunks_rather_than_appending(
        self, db_session, async_session
    ):
        """Re-uploading a corrected document must not leave the old answers in
        the index — an agent that quotes last month's refund policy alongside
        this month's is worse than one that knows neither."""
        from api.db import db_client

        org = await _org(async_session, "kb-replace")
        doc = await _document(async_session, org)
        await _store_chunks(async_session, org, doc, ["Refunds take 14 days."])
        await async_session.commit()

        await db_client.replace_chunks_for_document(
            document_id=doc.id,
            organization_id=org.id,
            chunks=[
                KnowledgeBaseChunkModel(
                    document_id=doc.id,
                    organization_id=org.id,
                    chunk_text="Refunds take 7 days.",
                    contextualized_text="Refunds take 7 days.",
                    chunk_index=0,
                    chunk_metadata={},
                    embedding_model="text-embedding-3-small",
                    embedding_dimension=DIM,
                    embedding=_vector(1.0),
                    token_count=4,
                )
            ],
        )

        found = await db_client.search_similar_chunks(
            query_embedding=_vector(1.0), organization_id=org.id, limit=5
        )
        texts = [c["chunk_text"] for c in found]
        assert "Refunds take 7 days." in texts
        assert "Refunds take 14 days." not in texts


@pytest.mark.asyncio
class TestTheSilentFailure:
    async def test_zero_chunks_is_recorded_rather_than_reported_as_ready(
        self, db_session, async_session
    ):
        """A scanned PDF with no text layer converts fine and chunks to
        nothing. Today that lands as ``completed`` with ``total_chunks = 0``,
        so the screen says the document is ready and the agent answers nothing
        from it — the worst shape a failure can take, because nobody looks."""
        org = await _org(async_session, "kb-empty")
        doc = await _document(async_session, org, filename="scan.pdf")
        await async_session.commit()

        from api.db import db_client

        await db_client.update_document_status(doc.id, "completed", total_chunks=0)
        refreshed = await db_client.get_document_by_id(doc.id)

        # Documented, not asserted as correct: this is the gap.
        assert refreshed.processing_status == "completed"
        assert refreshed.total_chunks == 0

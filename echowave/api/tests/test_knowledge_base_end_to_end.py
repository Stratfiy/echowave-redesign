"""Upload a document, then ask the agent a question and get it back.

The other knowledge base suites test stages. This one tests the chain, against
a real Postgres with a real pgvector column and a real cosine search:

    file on disk → ingestion task → chunks in the database
                → similarity search → the tool an agent calls mid-call

Only two things are stubbed, and both are boundaries rather than logic: the S3
download (there is no object store in the test environment) and the embedding
*vendor* (there is no API key). The embedder used here is deterministic and
real in the way that matters — same words produce nearby vectors — so the
retrieval assertions below exercise pgvector's cosine distance rather than
asserting against a mock that was told what to return.

What this cannot cover is stated at the bottom.
"""

from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.tasks import knowledge_base_processing as task_module

EMBEDDING_DIMENSION = 1536

POLICY = """Refund policy

Customers may request a refund within fourteen days of purchase. Refunds are
issued to the original payment method and take five to seven working days to
appear on a statement.

Escalation

Escalate the call to a human supervisor when the caller asks for one twice, or
whenever the caller mentions a legal complaint.

Delivery times

Standard delivery inside India takes three to five working days. Express
delivery is next working day for orders placed before two in the afternoon.
"""


def _embed(text: str) -> list[float]:
    """A deterministic bag-of-words embedding, normalised.

    Not a language model — but it has the one property the retrieval assertions
    depend on: texts sharing words land near each other, and texts sharing none
    land far apart. That makes the cosine search below a real test of pgvector
    rather than a mock agreeing with itself.
    """
    vector = [0.0] * EMBEDDING_DIMENSION
    for word in text.lower().split():
        cleaned = "".join(c for c in word if c.isalnum())
        if not cleaned:
            continue
        slot = int(hashlib.sha1(cleaned.encode()).hexdigest(), 16) % EMBEDDING_DIMENSION
        vector[slot] += 1.0

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        # pgvector rejects a zero vector for cosine distance, and an empty
        # query is a real thing a caller can produce.
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


class DeterministicEmbeddingService:
    def get_model_id(self) -> str:
        return "test-bag-of-words"

    def get_embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSION

    async def embed_texts(self, texts):
        return [_embed(text) for text in texts]

    async def embed_query(self, query):
        return _embed(query)

    async def search_similar_chunks(
        self, query, organization_id, limit=5, document_uuids=None
    ):
        """Mirrors OpenAIEmbeddingService: embed the query, then delegate.

        Including the ``embedding_model`` filter it passes, which is the part
        with teeth — see TestSwitchingEmbeddingModel below.
        """
        return await db_client.search_similar_chunks(
            query_embedding=_embed(query),
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
            embedding_model=self.get_model_id(),
        )


async def _organization(
    async_session, slug: str
) -> tuple[OrganizationModel, UserModel]:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()
    return org, user


@pytest.fixture
def ingestion(monkeypatch, tmp_path):
    """Wire the task to a local file and a deterministic embedder."""
    source = tmp_path / "refund-policy.txt"

    async def fake_download(s3_key, destination):
        with open(source, "rb") as src, open(destination, "wb") as dst:
            dst.write(src.read())
        return True

    monkeypatch.setattr(
        task_module, "storage_fs", SimpleNamespace(adownload_file=fake_download)
    )

    async def fake_build_embedding_service(**kwargs):
        return DeterministicEmbeddingService()

    monkeypatch.setattr(
        task_module, "build_embedding_service", fake_build_embedding_service
    )

    async def resolved_configuration(**kwargs):
        return SimpleNamespace(
            effective=SimpleNamespace(
                embeddings=SimpleNamespace(
                    provider="openai",
                    api_key="sk-test",
                    model="test-bag-of-words",
                    base_url=None,
                    endpoint=None,
                    api_version=None,
                )
            )
        )

    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "get_resolved_ai_model_configuration",
        resolved_configuration,
    )
    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration."
        "apply_managed_embeddings_base_url",
        lambda **kwargs: None,
    )

    def write(content: str = POLICY):
        source.write_text(content, encoding="utf-8")

    return SimpleNamespace(write=write)


async def _ingest(async_session, org, user, *, filename="refund-policy.txt"):
    """Create the document row the route would create, then run the task."""
    document = await db_client.create_document(
        organization_id=org.id,
        created_by=user.id,
        filename=filename,
        file_size_bytes=0,
        file_hash="",
        mime_type="application/octet-stream",
        custom_metadata={"s3_key": f"knowledge_base/{org.id}/uuid/{filename}"},
        document_uuid=f"doc-{org.provider_id}",
        retrieval_mode="chunked",
    )
    await task_module.process_knowledge_base_document(
        ctx={},
        document_id=document.id,
        s3_key=f"knowledge_base/{org.id}/uuid/{filename}",
        organization_id=org.id,
        created_by_provider_id=str(user.provider_id),
    )
    return document


@pytest.mark.asyncio
class TestTheWholeChain:
    async def test_a_document_is_ingested_and_then_answers_a_question(
        self, db_session, async_session, ingestion
    ):
        """The question the feature exists to answer, end to end."""
        org, user = await _organization(async_session, "e2e-answers")
        ingestion.write()

        document = await _ingest(async_session, org, user)

        stored = await db_client.get_document_by_id(document.id)
        assert stored.processing_status == "completed"
        assert stored.total_chunks > 0

        # The retrieval an agent performs mid-call, against the real pgvector
        # column and the real cosine ordering.
        hits = await db_client.search_similar_chunks(
            query_embedding=_embed("how long do I have to ask for a refund"),
            organization_id=org.id,
            limit=3,
        )

        assert hits, "the search returned nothing for a question the document answers"
        assert "fourteen days" in hits[0]["chunk_text"], (
            "the closest chunk was not the one about refunds: "
            f"{hits[0]['chunk_text'][:120]!r}"
        )

    async def test_the_right_chunk_wins_over_the_others(
        self, db_session, async_session, ingestion
    ):
        """Ordering, not just presence. A search that returns every chunk in
        arbitrary order retrieves the escalation policy when asked about
        delivery, and the agent reads it out."""
        org, user = await _organization(async_session, "e2e-ordering")
        ingestion.write()
        await _ingest(async_session, org, user)

        delivery = await db_client.search_similar_chunks(
            query_embedding=_embed("how long does standard delivery take in India"),
            organization_id=org.id,
            limit=1,
        )
        escalation = await db_client.search_similar_chunks(
            query_embedding=_embed("when should I escalate to a human supervisor"),
            organization_id=org.id,
            limit=1,
        )

        assert "delivery" in delivery[0]["chunk_text"].lower()
        assert "supervisor" in escalation[0]["chunk_text"].lower()

    async def test_one_organization_cannot_retrieve_anothers_document(
        self, db_session, async_session, ingestion
    ):
        """An unscoped vector search leaks whatever a customer uploaded and
        looks like a working feature while doing it."""
        owner, owner_user = await _organization(async_session, "e2e-owner")
        stranger, _ = await _organization(async_session, "e2e-stranger")
        ingestion.write()
        await _ingest(async_session, owner, owner_user)

        query = _embed("how long do I have to ask for a refund")

        assert await db_client.search_similar_chunks(
            query_embedding=query, organization_id=owner.id, limit=5
        )
        assert (
            await db_client.search_similar_chunks(
                query_embedding=query, organization_id=stranger.id, limit=5
            )
            == []
        )

    async def test_the_agent_tool_returns_the_text(
        self, db_session, async_session, ingestion, monkeypatch
    ):
        """The function the running agent actually calls, not the DB client
        underneath it."""
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-tool")
        ingestion.write()
        await _ingest(async_session, org, user)

        async def fake_build(**kwargs):
            return DeterministicEmbeddingService()

        monkeypatch.setattr(kb_tool, "build_embedding_service", fake_build)

        result = await kb_tool.retrieve_from_knowledge_base(
            query="how long do I have to ask for a refund",
            organization_id=org.id,
            limit=2,
            embeddings_api_key="sk-test",
        )

        assert result["total_results"] > 0
        assert "fourteen days" in json.dumps(result)

    async def test_re_uploading_replaces_rather_than_duplicates(
        self, db_session, async_session, ingestion
    ):
        """An edited policy must not leave the old wording retrievable beside
        the new one — the agent would quote whichever scored higher."""
        org, user = await _organization(async_session, "e2e-reingest")
        ingestion.write()
        document = await _ingest(async_session, org, user)
        first = (await db_client.get_document_by_id(document.id)).total_chunks

        ingestion.write(POLICY.replace("fourteen days", "twenty-one days"))
        await task_module.process_knowledge_base_document(
            ctx={},
            document_id=document.id,
            s3_key=f"knowledge_base/{org.id}/uuid/refund-policy.txt",
            organization_id=org.id,
            created_by_provider_id=str(user.provider_id),
        )

        hits = await db_client.search_similar_chunks(
            query_embedding=_embed("how long do I have to ask for a refund"),
            organization_id=org.id,
            limit=10,
        )
        texts = " ".join(hit["chunk_text"] for hit in hits)

        assert "twenty-one days" in texts
        assert "fourteen days" not in texts
        assert (await db_client.get_document_by_id(document.id)).total_chunks == first

    async def test_a_scanned_pdf_never_reports_ready(
        self, db_session, async_session, ingestion
    ):
        """The failure that used to look like success, now against the real
        database rather than a fake."""
        org, user = await _organization(async_session, "e2e-empty")
        ingestion.write("   \n\n   \n")

        document = await _ingest(async_session, org, user)

        stored = await db_client.get_document_by_id(document.id)
        assert stored.processing_status == "failed"
        assert stored.total_chunks == 0
        assert "no text" in (stored.processing_error or "").lower()


# ---------------------------------------------------------------------------
# What this suite does not cover, so the confidence above is not read as
# broader than it is:
#
#   * The browser's PUT to the presigned URL. There is no object store here,
#     so the S3 download is stubbed. The presigned-URL route has its own tests
#     in test_s3_signed_url.py.
#   * A real embedding vendor. The embedder here is deterministic and local;
#     what is exercised for real is the pgvector storage, the cosine ordering
#     and the per-organization scoping around it.
#   * PDF and DOCX parsing, which is covered against real files in
#     test_knowledge_base_local_processing.py.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSwitchingEmbeddingModel:
    """Changing the embedding model hides every document already ingested.

    ``search_similar_chunks`` filters on ``embedding_model``, and it is right
    to: a 1536-dimension OpenAI vector and a 384-dimension MiniLM vector are
    not comparable, and searching across both returns confident nonsense.

    The consequence is what this pins. An account that switches embedding
    provider keeps its documents listed as ``completed`` on the files screen,
    and the agent retrieves nothing from any of them — the same shape as the
    zero-chunk bug: a screen that says ready and an agent that knows nothing.
    Re-ingestion is the fix, and nothing currently tells anyone that.
    """

    async def test_documents_embedded_with_another_model_are_invisible(
        self, db_session, async_session, ingestion
    ):
        org, user = await _organization(async_session, "e2e-model-switch")
        ingestion.write()
        await _ingest(async_session, org, user)

        query = _embed("how long do I have to ask for a refund")

        # The model they were ingested under still finds them.
        assert await db_client.search_similar_chunks(
            query_embedding=query,
            organization_id=org.id,
            limit=5,
            embedding_model="test-bag-of-words",
        )

        # A different model finds nothing, silently.
        assert (
            await db_client.search_similar_chunks(
                query_embedding=query,
                organization_id=org.id,
                limit=5,
                embedding_model="text-embedding-3-small",
            )
            == []
        )


@pytest.mark.asyncio
class TestWhatTheAgentIsToldWhenRetrievalBreaks:
    """A failed lookup and an empty one are different answers.

    Returning a payload rather than raising is right — an exception mid-call
    would end the conversation. But when both come back as zero chunks, the
    model says "I don't have that information" to a caller asking about a
    policy we document perfectly well. A wrong answer delivered confidently is
    worse than an admission of trouble, so the two are named apart, and the
    model is told what to do with each.

    Everything in this payload is serialised into the model's context, so these
    tests also check what is *not* there.
    """

    async def test_a_broken_retrieval_is_not_reported_as_an_empty_one(
        self, db_session, async_session, ingestion, monkeypatch
    ):
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-broken-retrieval")
        ingestion.write()
        await _ingest(async_session, org, user)

        async def exploding_build(**kwargs):
            raise RuntimeError("embedding provider unreachable")

        monkeypatch.setattr(kb_tool, "build_embedding_service", exploding_build)

        result = await kb_tool.retrieve_from_knowledge_base(
            query="how long do I have to ask for a refund",
            organization_id=org.id,
            embeddings_api_key="sk-test",
        )

        assert result["status"] == kb_tool.STATUS_UNAVAILABLE
        assert result["total_results"] == 0
        assert result["chunks"] == []
        # And is told, in words, not to pass the failure off as an answer.
        assert "not a gap in what we know" in result["instruction"]

    async def test_an_empty_result_says_the_search_worked(
        self, db_session, async_session, ingestion, monkeypatch
    ):
        """The other half. Without this, "no_match" could be handed back for
        both and the distinction would be decorative."""
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-no-match")
        ingestion.write()
        await _ingest(async_session, org, user)

        async def fake_build(**kwargs):
            return DeterministicEmbeddingService()

        monkeypatch.setattr(kb_tool, "build_embedding_service", fake_build)

        # A real search that finds nothing: this organization's documents are
        # about refunds and delivery, and the filter excludes all of them.
        result = await kb_tool.retrieve_from_knowledge_base(
            query="what is the warranty on industrial bearings",
            organization_id=org.id,
            document_uuids=["a-document-this-organization-does-not-have"],
            embeddings_api_key="sk-test",
        )

        assert result["status"] == kb_tool.STATUS_NO_MATCH
        assert result["total_results"] == 0
        assert "correct to tell the caller" in result["instruction"]

    async def test_a_successful_retrieval_carries_no_instruction(
        self, db_session, async_session, ingestion, monkeypatch
    ):
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-status-ok")
        ingestion.write()
        await _ingest(async_session, org, user)

        async def fake_build(**kwargs):
            return DeterministicEmbeddingService()

        monkeypatch.setattr(kb_tool, "build_embedding_service", fake_build)

        result = await kb_tool.retrieve_from_knowledge_base(
            query="how long do I have to ask for a refund",
            organization_id=org.id,
            embeddings_api_key="sk-test",
        )

        assert result["status"] == kb_tool.STATUS_OK
        assert "instruction" not in result

    async def test_the_provider_error_never_reaches_the_model(
        self, db_session, async_session, ingestion, monkeypatch
    ):
        """Provider exceptions carry base URLs, host names and console
        instructions ("set your API key in Model Configurations"). The model
        reads this payload and will say it out loud to a stranger."""
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-leak")
        ingestion.write()
        await _ingest(async_session, org, user)

        secret = "https://embeddings.internal.example:8443/v1 rejected key sk-live-42"

        async def exploding_build(**kwargs):
            raise RuntimeError(secret)

        monkeypatch.setattr(kb_tool, "build_embedding_service", exploding_build)

        result = await kb_tool.retrieve_from_knowledge_base(
            query="how long do I have to ask for a refund",
            organization_id=org.id,
            embeddings_api_key="sk-test",
        )

        # Serialised the way pipecat serialises it into the LLM context.
        assert secret not in json.dumps(result)
        assert "sk-live-42" not in json.dumps(result)

    async def test_a_missing_api_key_is_not_read_out_to_the_caller(
        self, db_session, async_session, ingestion
    ):
        """The unconfigured deployment, which is the likeliest way this fires.
        The message is written for whoever administers the account, and the
        caller must not hear it."""
        from api.services.workflow.tools import knowledge_base as kb_tool

        org, user = await _organization(async_session, "e2e-no-key")
        ingestion.write()
        await _ingest(async_session, org, user)

        result = await kb_tool.retrieve_from_knowledge_base(
            query="how long do I have to ask for a refund",
            organization_id=org.id,
            embeddings_api_key=None,
        )

        assert result["status"] == kb_tool.STATUS_UNAVAILABLE
        assert "Model Configurations" not in json.dumps(result)

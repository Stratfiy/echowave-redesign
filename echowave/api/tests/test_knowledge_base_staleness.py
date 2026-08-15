"""Documents an embedding-model switch has quietly hidden.

``test_knowledge_base_end_to_end.py::TestSwitchingEmbeddingModel`` pins the
failure: swap embedding model and every already-ingested document keeps saying
``completed`` while the agent retrieves nothing from it. This suite covers the
detection that makes it visible, and the two ways detection can be worse than
nothing —

* a **false alarm**, telling an account to re-ingest a library that is fine,
  which costs them real money at their embedding provider; and
* a **miss**, staying quiet about documents that genuinely cannot be retrieved.

The comparison only holds if this module resolves the current model exactly the
way ``build_embedding_service`` does, including its default. The last test in
:class:`TestItAgreesWithTheIngestionPath` is the one that keeps that true.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.db.models import (
    KnowledgeBaseChunkModel,
    KnowledgeBaseDocumentModel,
    OrganizationModel,
    UserModel,
)
from api.services.gen_ai.embedding.factory import DEFAULT_EMBEDDING_MODEL
from api.services.knowledge_base import staleness

EMBEDDING_DIMENSION = 1536


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


async def _document(
    async_session,
    org: OrganizationModel,
    user: UserModel,
    *,
    filename: str,
    embedded_with: str | None,
    chunks: int = 2,
) -> KnowledgeBaseDocumentModel:
    """A completed document, with chunks carrying ``embedded_with``.

    ``embedded_with=None`` writes no chunks at all — the shape of a document
    that is still pending, or one that failed.
    """
    document = KnowledgeBaseDocumentModel(
        organization_id=org.id,
        document_uuid=f"{org.provider_id}-{filename}",
        filename=filename,
        file_size_bytes=1,
        file_hash=f"hash-{org.provider_id}-{filename}",
        mime_type="text/plain",
        processing_status="completed",
        total_chunks=chunks if embedded_with else 0,
        custom_metadata={},
        created_by=user.id,
    )
    async_session.add(document)
    await async_session.flush()

    if embedded_with:
        for index in range(chunks):
            async_session.add(
                KnowledgeBaseChunkModel(
                    document_id=document.id,
                    organization_id=org.id,
                    chunk_text=f"chunk {index}",
                    contextualized_text=f"chunk {index}",
                    chunk_index=index,
                    chunk_metadata={},
                    embedding_model=embedded_with,
                    embedding_dimension=EMBEDDING_DIMENSION,
                    token_count=3,
                )
            )
        await async_session.flush()

    return document


@pytest.fixture
def configured_model(monkeypatch):
    """Set what the organization's *next* ingestion would embed with."""

    def use(model: str | None):
        async def resolved(**kwargs):
            return SimpleNamespace(
                effective=SimpleNamespace(
                    embeddings=(
                        SimpleNamespace(model=model) if model is not None else None
                    )
                )
            )

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            resolved,
        )

    return use


class TestIsStranded:
    """The comparison itself. Cheap to get subtly wrong, so stated outright."""

    def test_a_different_model_strands_the_document(self):
        assert staleness.is_stranded("text-embedding-3-small", "voyage-3")

    def test_the_same_model_does_not(self):
        assert not staleness.is_stranded("voyage-3", "voyage-3")

    def test_a_document_with_no_chunks_is_not_reported(self):
        """It is pending or failed, and its status already says so. Adding a
        second explanation for a document nobody can retrieve from *yet* is
        noise on top of the message that matters."""
        assert not staleness.is_stranded(None, "voyage-3")

    def test_an_unknowable_current_model_reports_nothing(self):
        """When the configuration could not be resolved, the honest answer is
        silence — flagging every document in the account on the strength of a
        failed lookup is the false alarm this guards against."""
        assert not staleness.is_stranded("voyage-3", None)


@pytest.mark.asyncio
class TestFindingTheStrandedDocuments:
    async def test_documents_on_the_old_model_are_named(
        self, db_session, async_session, configured_model
    ):
        org, user = await _organization(async_session, "stale-named")
        old = await _document(
            async_session, org, user, filename="old.txt", embedded_with="voyage-3"
        )
        current = await _document(
            async_session,
            org,
            user,
            filename="current.txt",
            embedded_with="text-3-large",
        )
        configured_model("text-3-large")

        stranded = await staleness.stranded_document_ids(async_session, org.id)

        assert old.id in stranded
        assert current.id not in stranded

    async def test_an_account_that_never_switched_is_left_alone(
        self, db_session, async_session, configured_model
    ):
        """The common case, and the one a false positive would ruin."""
        org, user = await _organization(async_session, "stale-untouched")
        await _document(
            async_session, org, user, filename="a.txt", embedded_with="text-3-large"
        )
        await _document(
            async_session, org, user, filename="b.txt", embedded_with="text-3-large"
        )
        configured_model("text-3-large")

        assert await staleness.stranded_document_ids(async_session, org.id) == set()

    async def test_a_pending_document_is_not_called_stranded(
        self, db_session, async_session, configured_model
    ):
        org, user = await _organization(async_session, "stale-pending")
        pending = await _document(
            async_session, org, user, filename="pending.txt", embedded_with=None
        )
        configured_model("text-3-large")

        stranded = await staleness.stranded_document_ids(async_session, org.id)

        assert pending.id not in stranded

    async def test_another_organizations_documents_are_never_looked_at(
        self, db_session, async_session, configured_model
    ):
        """Tenant isolation, in a query that has every reason to forget it —
        it groups by document and the organization is not in the output."""
        mine, mine_user = await _organization(async_session, "stale-mine")
        theirs, theirs_user = await _organization(async_session, "stale-theirs")
        await _document(
            async_session,
            mine,
            mine_user,
            filename="mine.txt",
            embedded_with="text-3-large",
        )
        stranger = await _document(
            async_session,
            theirs,
            theirs_user,
            filename="theirs.txt",
            embedded_with="voyage-3",
        )
        configured_model("text-3-large")

        stranded = await staleness.stranded_document_ids(async_session, mine.id)

        assert stranger.id not in stranded
        assert stranded == set()

    async def test_a_failed_configuration_lookup_reports_nothing(
        self, db_session, async_session, monkeypatch
    ):
        org, user = await _organization(async_session, "stale-unresolvable")
        await _document(
            async_session, org, user, filename="a.txt", embedded_with="voyage-3"
        )

        async def exploding(**kwargs):
            raise RuntimeError("configuration store unreachable")

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            exploding,
        )

        assert await staleness.stranded_document_ids(async_session, org.id) == set()

    async def test_one_query_covers_the_whole_page(
        self, db_session, async_session, configured_model
    ):
        """A per-document lookup is how a screen that was fine with ten
        documents becomes unusable at four hundred. Counted, not assumed."""
        org, user = await _organization(async_session, "stale-one-query")
        for index in range(12):
            await _document(
                async_session,
                org,
                user,
                filename=f"doc-{index}.txt",
                embedded_with="voyage-3",
            )
        configured_model("text-3-large")

        statements: list[str] = []

        class CountingSession:
            """Delegates to the real session, and keeps the tally."""

            async def execute(self, statement, *args, **kwargs):
                statements.append(str(statement))
                return await async_session.execute(statement, *args, **kwargs)

        stranded = await staleness.stranded_document_ids(CountingSession(), org.id)

        assert len(stranded) == 12
        assert len(statements) == 1, (
            "expected one grouped query for the page, got "
            f"{len(statements)}: {statements}"
        )


@pytest.mark.asyncio
class TestItAgreesWithTheIngestionPath:
    """The detection is only worth anything if it compares like with like."""

    async def test_the_configured_model_is_what_gets_compared(
        self, db_session, configured_model
    ):
        configured_model("voyage-3")
        assert await staleness.current_embedding_model(1) == "voyage-3"

    async def test_an_unconfigured_account_resolves_to_the_same_default_as_ingestion(
        self, db_session, async_session, configured_model
    ):
        """``build_embedding_service`` turns an unset model into
        ``DEFAULT_EMBEDDING_MODEL``, and stores that on every chunk. Reading
        the configured value alone would report ``None`` here and stay silent
        about a library that really is stranded."""
        org, user = await _organization(async_session, "stale-default")
        old = await _document(
            async_session, org, user, filename="old.txt", embedded_with="voyage-3"
        )
        on_default = await _document(
            async_session,
            org,
            user,
            filename="default.txt",
            embedded_with=DEFAULT_EMBEDDING_MODEL,
        )
        configured_model(None)

        assert (
            await staleness.current_embedding_model(org.id) == DEFAULT_EMBEDDING_MODEL
        )
        stranded = await staleness.stranded_document_ids(async_session, org.id)
        assert old.id in stranded
        assert on_default.id not in stranded


@pytest.mark.asyncio
class TestTheFilesScreenIsTold:
    """The whole point is a flag reaching the screen. Tested through the
    route, because a correct service function nobody wired up changes
    nothing about what a customer sees."""

    async def test_the_listing_marks_the_stranded_document(
        self, db_session, async_session, configured_model, test_client_factory
    ):
        org, user = await _organization(async_session, "stale-route")
        old = await _document(
            async_session, org, user, filename="old.txt", embedded_with="voyage-3"
        )
        fine = await _document(
            async_session, org, user, filename="fine.txt", embedded_with="text-3-large"
        )
        configured_model("text-3-large")

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/knowledge-base/documents")

        assert response.status_code == 200
        flags = {
            document["filename"]: document["needs_reingest"]
            for document in response.json()["documents"]
        }
        assert flags == {old.filename: True, fine.filename: False}

    async def test_a_healthy_library_is_listed_without_alarm(
        self, db_session, async_session, configured_model, test_client_factory
    ):
        org, user = await _organization(async_session, "stale-route-clean")
        await _document(
            async_session, org, user, filename="a.txt", embedded_with="text-3-large"
        )
        configured_model("text-3-large")

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/knowledge-base/documents")

        assert response.status_code == 200
        assert all(
            document["needs_reingest"] is False
            for document in response.json()["documents"]
        )

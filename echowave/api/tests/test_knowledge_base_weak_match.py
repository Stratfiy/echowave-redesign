"""A near miss is not an answer.

Vector search returns the nearest chunks however far away they are, so while
any document exists there is no such thing as "no results". Without a floor the
model receives the nearest paragraph to every question and reads it as context
worth answering from — and the module's own docstring already names that
failure: "a wrong answer delivered confidently, which is worse than an
admission of trouble". It guarded the empty case and not the distant one.

Measured on the live knowledge base before this was written (DHL rate guide,
117 chunks, text-embedding-3-small):

    on topic  ("what is the fuel surcharge?")        0.478 - 0.719
    off topic ("what is the capital of Mongolia?")   0.143 - 0.322

"What is the capital of Mongolia?" came back as status ok carrying the rate
guide at 0.316.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.tools import knowledge_base as kb


def _result(similarity: float, text: str = "Fuel surcharge is 28%."):
    return {
        "chunk_text": text,
        "contextualized_text": text,
        "filename": "DHL_Time_Definite_Export_2026.pdf",
        "similarity": similarity,
        "chunk_index": 3,
    }


async def _retrieve(results):
    """Run the tool's retrieval with the vector search stubbed to `results`."""
    service = AsyncMock()
    service.search_similar_chunks = AsyncMock(return_value=results)
    service.get_model_id = lambda: "text-embedding-3-small"

    with (
        patch.object(kb, "build_embedding_service", AsyncMock(return_value=service)),
        patch.object(
            kb.db_client, "get_full_text_documents", AsyncMock(return_value=[])
        ),
    ):
        return await kb._perform_retrieval(
            query="what is the capital of Mongolia?",
            organization_id=1,
            document_uuids=None,
            limit=3,
            embeddings_api_key="sk-test",
            embeddings_model="text-embedding-3-small",
            embeddings_provider="openai",
        )


class TestTheFloor:
    @pytest.mark.asyncio
    async def test_a_distant_match_is_not_served_as_an_answer(self):
        """The live failure: DHL text returned for a question about Mongolia."""
        payload = await _retrieve([_result(0.316), _result(0.234), _result(0.189)])

        assert payload["status"] == kb.STATUS_WEAK_MATCH
        # The chunks must not reach the model at all. An instruction not to use
        # them, with them attached, is an instruction the model may ignore.
        assert payload["chunks"] == []
        assert payload["total_results"] == 0

    @pytest.mark.asyncio
    async def test_it_tells_the_model_not_to_fill_the_gap(self):
        payload = await _retrieve([_result(0.316)])
        assert "do not have that information" in payload["instruction"].lower()
        assert "not answer from memory" in payload["instruction"].lower()

    @pytest.mark.asyncio
    async def test_it_records_how_close_the_near_miss_was(self):
        """For the operator, not the model: it is the number that says whether
        the floor is set right."""
        payload = await _retrieve([_result(0.316), _result(0.234)])
        assert payload["best_similarity"] == 0.316

    @pytest.mark.asyncio
    async def test_an_on_topic_match_is_served_normally(self):
        payload = await _retrieve([_result(0.4781), _result(0.4526)])
        assert payload["status"] == kb.STATUS_OK
        assert len(payload["chunks"]) == 2

    @pytest.mark.asyncio
    async def test_the_near_chunks_survive_and_the_far_ones_do_not(self):
        """A real answer alongside a distant one keeps the answer and drops the
        noise, rather than discarding the lot."""
        payload = await _retrieve([_result(0.7193), _result(0.2110)])
        assert payload["status"] == kb.STATUS_OK
        assert [c["similarity"] for c in payload["chunks"]] == [0.7193]

    @pytest.mark.asyncio
    async def test_a_genuinely_empty_search_is_still_no_match(self):
        """Distinct from a weak match: nothing was found at all, which is a
        different thing to tell an operator."""
        payload = await _retrieve([])
        assert payload["status"] == kb.STATUS_NO_MATCH

    @pytest.mark.asyncio
    async def test_a_full_document_match_is_never_filtered(self):
        """Full-document retrieval carries similarity 1.0 by construction; the
        floor is about vector search and must not touch it."""
        service = AsyncMock()
        service.search_similar_chunks = AsyncMock(return_value=[])
        service.get_model_id = lambda: "text-embedding-3-small"

        class _Doc:
            full_text = "The whole policy document."
            filename = "policy.pdf"
            document_uuid = "uuid-1"

        with (
            patch.object(
                kb, "build_embedding_service", AsyncMock(return_value=service)
            ),
            patch.object(
                kb.db_client,
                "get_full_text_documents",
                AsyncMock(return_value=[_Doc()]),
            ),
        ):
            payload = await kb._perform_retrieval(
                query="what does the policy say?",
                organization_id=1,
                document_uuids=["uuid-1"],
                limit=3,
                embeddings_api_key="sk-test",
            )

        assert payload["status"] == kb.STATUS_OK
        assert payload["chunks"][0]["similarity"] == 1.0


class TestTheFloorItself:
    def test_it_sits_between_the_measured_populations(self):
        """The number is evidence, not taste. On-topic bottomed out at 0.478
        and off-topic topped out at 0.322; the floor belongs in that gap with
        room either side."""
        assert 0.322 < kb.WEAK_MATCH_BELOW < 0.478

    def test_it_is_tunable_without_a_deploy(self, monkeypatch):
        """One corpus and one embedding model is evidence, not a law."""
        monkeypatch.setenv("KNOWLEDGE_BASE_MIN_SIMILARITY", "0.55")
        import importlib

        reloaded = importlib.reload(kb)
        try:
            assert reloaded.WEAK_MATCH_BELOW == 0.55
        finally:
            monkeypatch.delenv("KNOWLEDGE_BASE_MIN_SIMILARITY")
            importlib.reload(kb)

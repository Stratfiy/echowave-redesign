"""Knowledge-base search on an account that brought no key of its own.

A managed account's embeddings section reads ``provider=decibyl, api_key=""``
as stored. Only ``get_effective_ai_model_configuration_for_workflow`` runs the
resolution that turns it into a real vendor on the platform key;
``get_resolved_ai_model_configuration`` returns the stored shape untouched.

Search and ingestion both read the stored shape, so every managed organization
searched with an empty key and was told "Failed to search chunks" — the whole
knowledge base dark, for every account that had not brought its own key, with
an error naming nothing an operator could act on.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import knowledge_base as kb_routes

user = SimpleNamespace(id=3, selected_organization_id=7)


def request(query="root canal"):
    return SimpleNamespace(
        query=query, limit=3, document_uuids=None, min_similarity=None
    )


@pytest.fixture
def resolution(monkeypatch):
    """Record which resolver search reaches for, and what it gets back."""
    seen = {}

    async def for_workflow(*, organization_id, workflow_configurations):
        seen["organization_id"] = organization_id
        # What resolution produces: a real vendor, on our key.
        return SimpleNamespace(
            embeddings=SimpleNamespace(
                provider="openai",
                api_key="sk-platform",
                model="text-embedding-3-small",
                base_url=None,
                endpoint=None,
                api_version=None,
            )
        )

    monkeypatch.setattr(
        "api.services.configuration.ai_model_configuration"
        ".get_effective_ai_model_configuration_for_workflow",
        for_workflow,
    )
    return seen


@pytest.mark.asyncio
class TestSearchResolvesBeforeItEmbeds:
    async def test_it_embeds_with_the_resolved_vendor_and_key(
        self, resolution, monkeypatch
    ):
        built = {}

        async def build(**kwargs):
            built.update(kwargs)
            return SimpleNamespace(search_similar_chunks=AsyncMock(return_value=[]))

        monkeypatch.setattr("api.services.gen_ai.build_embedding_service", build)

        await kb_routes.search_chunks(request(), user=user)

        assert built["provider"] == "openai", "searched with the unresolved provider"
        assert built["api_key"] == "sk-platform", "searched with an empty key"

    async def test_it_asks_about_the_callers_own_organization(
        self, resolution, monkeypatch
    ):
        monkeypatch.setattr(
            "api.services.gen_ai.build_embedding_service",
            AsyncMock(
                return_value=SimpleNamespace(
                    search_similar_chunks=AsyncMock(return_value=[])
                )
            ),
        )

        await kb_routes.search_chunks(request(), user=user)

        assert resolution["organization_id"] == 7


@pytest.mark.asyncio
class TestWhatAFailureTellsTheOperator:
    """Both ways this fails are an administrator's to fix, and a 500 saying
    "Failed to search chunks" names neither."""

    async def _search_raising(self, monkeypatch, error: str):
        async def build(**_):
            raise ValueError(error)

        monkeypatch.setattr("api.services.gen_ai.build_embedding_service", build)
        with pytest.raises(HTTPException) as exc:
            await kb_routes.search_chunks(request(), user=user)
        return exc.value

    async def test_a_missing_key_is_a_503_naming_the_screen(
        self, resolution, monkeypatch
    ):
        err = await self._search_raising(
            monkeypatch, "OpenAI API key not configured. Please set your API key"
        )
        assert err.status_code == 503
        assert "provider keys" in err.detail

    async def test_a_rejected_key_is_a_502_not_a_missing_one(
        self, resolution, monkeypatch
    ):
        """The vendor's 401 body says "Incorrect API key provided", which also
        matches the missing-key test. Checked in the wrong order, every
        rejected key is reported as absent and the administrator goes looking
        for a setting that is already filled in."""
        err = await self._search_raising(
            monkeypatch,
            "Error code: 401 - {'error': {'message': 'Incorrect API key "
            "provided: sk-live-***0099', 'code': 'invalid_api_key'}}",
        )
        assert err.status_code == 502
        assert "rejected" in err.detail

    async def test_anything_else_stays_a_500(self, resolution, monkeypatch):
        """Only the two an operator can act on get their own answer. Mapping
        everything to a key problem would send them to the wrong screen."""
        err = await self._search_raising(monkeypatch, "connection reset by peer")
        assert err.status_code == 500

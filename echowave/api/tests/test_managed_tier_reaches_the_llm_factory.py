"""A managed LLM tier must survive the trip into the pipeline's LLM factory.

``managed_resolution`` rewrites a managed section's ``provider``, ``model`` and
``api_key`` in place and deliberately leaves the object as the tier class it
was — one configuration object rather than two representations of the same
thing. The consequence is that ``provider`` stops being a reliable guide to
what class you are holding: a ``DecibylLLMService`` will answer
``provider == "openai"``, while carrying none of OpenAI's optional fields.

``create_llm_service`` used to read those fields off the section directly, so
every managed LLM tier raised ``AttributeError`` before the pipeline produced a
frame. The caller's phone rang, they answered, and the line went dead — Plivo
recorded "End Of XML Instructions" against a two-second call, which names the
symptom and not one part of the cause.

Both branches a tier can reach are covered, because they failed on different
attributes and fixing only the one that happened to be the default tier would
have left the others broken:

* ``default`` and ``accurate`` resolve to openai, which reads ``base_url``
* ``lite``, ``fast`` and ``zen`` resolve to sarvam, which reads ``temperature``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.configuration import managed_resolution
from api.services.configuration.registry import DecibylLLMService
from api.services.pipecat import service_factory


def _effective(section):
    return SimpleNamespace(
        llm=section, stt=None, tts=None, realtime=None, embeddings=None
    )


@pytest.fixture
def captured(monkeypatch):
    """The kwargs the factory would hand the provider, instead of building it.

    Constructing a real service would need vendor SDKs and a live key; the
    thing under test is which keyword arguments survive resolution.
    """
    seen = {}

    def fake_create(provider, model, api_key, correlation_id=None, **kwargs):
        seen.update(
            provider=provider, model=model, api_key=api_key, kwargs=dict(kwargs)
        )
        return object()

    monkeypatch.setattr(
        service_factory, "create_llm_service_from_provider", fake_create
    )
    return seen


@pytest.mark.asyncio
class TestAManagedTierReachesTheFactory:
    async def _resolve(self, monkeypatch, tier: str):
        effective = _effective(DecibylLLMService(model=tier))

        async def fake_key(session, *, component, provider):
            return "platform-key-xyz"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )
        await managed_resolution.apply(effective)
        return effective

    async def test_the_openai_tier_builds(self, monkeypatch, captured):
        effective = await self._resolve(monkeypatch, "default")
        assert effective.llm.provider == "openai"

        # The assertion is that this does not raise AttributeError.
        service_factory.create_llm_service(effective)

        assert captured["provider"] == "openai"
        assert captured["api_key"] == "platform-key-xyz"
        # Absent rather than None: the vendor default is what a managed slot
        # should run on, and passing base_url=None would override it.
        assert "base_url" not in captured["kwargs"]

    async def test_the_sarvam_tier_builds(self, monkeypatch, captured):
        effective = await self._resolve(monkeypatch, "lite")
        assert effective.llm.provider == "sarvam"

        service_factory.create_llm_service(effective)

        assert captured["provider"] == "sarvam"
        assert "temperature" not in captured["kwargs"]

    async def test_a_byok_section_still_carries_its_own_endpoint(self, captured):
        """The guard must not stop a real OpenAI config passing its base_url —
        that field is why the branch exists."""
        from api.services.configuration.registry import OpenAILLMService

        section = OpenAILLMService(model="gpt-4o", api_key="sk-real")
        section.base_url = "https://proxy.example.com/v1"

        service_factory.create_llm_service(_effective(section))

        assert captured["kwargs"]["base_url"] == "https://proxy.example.com/v1"

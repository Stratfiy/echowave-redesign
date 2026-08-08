"""Stamping which key a section resolved to.

``byok_resolution.apply`` is the only point where a section's own
``provider`` value still says whether it belongs to the account or to
Decibyl -- ``managed_resolution`` runs straight after and overwrites
``provider`` to a real vendor name on every managed section, at which point a
managed section and a BYOK one are indistinguishable by inspection. Billing
reads the stamp this module leaves behind (``section.key_source``) back off
``usage_info`` at the end of the call to decide whether a component's usage
was ours to charge for -- see ``api/services/billing/usage.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.configuration import byok_resolution, managed_resolution
from api.services.configuration.registry import DecibylLLMService, OpenAILLMService


@pytest.mark.asyncio
class TestKeySourceStamping:
    async def test_a_byok_section_with_its_own_key_is_stamped_byok(self):
        """Already carries a usable key, so byok_resolution's vault lookup
        never touches it -- the stamp must not depend on that lookup running."""
        effective = SimpleNamespace(
            llm=OpenAILLMService(api_key="sk-already-here", model="gpt-4o"),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        await byok_resolution.apply(effective, organization_id=None)

        assert effective.llm.key_source == "byok"

    async def test_a_managed_section_is_stamped_managed_before_resolution(self):
        effective = SimpleNamespace(
            llm=DecibylLLMService(model="default"),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        await byok_resolution.apply(effective, organization_id=None)

        assert effective.llm.key_source == "managed"

    async def test_the_stamp_survives_managed_resolution_rewriting_the_section(
        self, monkeypatch
    ):
        """The real end-to-end order: byok-then-managed. managed_resolution
        rewrites provider/model/api_key in place -- key_source must not be
        one of the fields it clobbers."""
        effective = SimpleNamespace(
            llm=DecibylLLMService(model="default"),
            stt=None,
            tts=None,
            realtime=None,
            embeddings=None,
        )

        async def _fake_resolve_api_key(*_a, **_kw):
            return "platform-key"

        monkeypatch.setattr(
            "api.services.configuration.platform_credentials.resolve_api_key",
            _fake_resolve_api_key,
        )

        await byok_resolution.apply(effective, organization_id=None)
        await managed_resolution.apply(effective)

        assert effective.llm.key_source == "managed"
        # Sanity: managed_resolution actually ran and moved it off "decibyl".
        assert effective.llm.provider != "decibyl"

    async def test_an_absent_section_is_skipped_without_error(self):
        effective = SimpleNamespace(
            llm=None, stt=None, tts=None, realtime=None, embeddings=None
        )
        await byok_resolution.apply(effective, organization_id=None)  # must not raise

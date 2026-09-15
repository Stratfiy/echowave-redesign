"""The shipped skills parse, and the shelf can describe them."""

from __future__ import annotations

import pytest

from api.services.skills import catalogue
from api.services.skills.document import prompt_block


class TestWhatShips:
    def test_every_file_parses_and_carries_its_credit(self):
        skills = catalogue.all_skills()
        assert len(skills) >= 50, "the catalogue should not be nearly empty"
        for slug, skill in skills.items():
            assert slug == slug.lower()
            assert skill.title and skill.description
            assert skill.division
            # Redistributed from somebody else's repository: the source and
            # the licence are the terms, not decoration.
            assert skill.source, slug
            assert skill.license, slug
            assert skill.skill.line_count <= 500, slug

    def test_divisions_lead_with_the_big_shelves(self):
        divisions = catalogue.divisions()
        assert divisions, "no divisions"
        counts = [
            sum(1 for s in catalogue.all_skills().values() if s.division == d)
            for d in divisions
        ]
        assert counts == sorted(counts, reverse=True)

    def test_the_credit_line_names_both_repositories(self):
        sources = {row["source"] for row in catalogue.attributions()}
        assert "msitarzewski/agency-agents" in sources
        assert all(row["license"] for row in catalogue.attributions())

    def test_a_card_is_a_card_and_not_the_whole_body(self):
        skill = next(iter(catalogue.all_skills().values()))
        card = skill.as_card()
        assert set(card) == {
            "slug",
            "title",
            "description",
            "division",
            "emoji",
            "source",
            "license",
            "lines",
        }
        assert "body" not in card

    def test_a_skill_becomes_a_delimited_prompt_block(self):
        skill = catalogue.get("sales-coach")
        assert skill is not None
        block = prompt_block(skill.skill)
        assert block.startswith('<skill name="sales-coach">')
        assert block.rstrip().endswith("</skill>")

    def test_an_unknown_slug_is_none_rather_than_a_raise(self):
        assert catalogue.get("no-such-skill") is None
        assert catalogue.get("") is None


class TestDictationDoesNotAskForAKeyWeNeverAskedFor:
    """The microphone on the composer told a managed account to "add a
    speech-to-text key under Model Configurations". A managed account holds
    no vendor key by design: the tier resolves to a vendor and the platform's
    key pays for it, exactly as a call does."""

    @staticmethod
    def _resolved(provider: str, api_key: str | None):
        from types import SimpleNamespace

        return SimpleNamespace(
            effective=SimpleNamespace(
                stt=SimpleNamespace(
                    provider=provider, api_key=api_key, model=None, base_url=None
                )
            )
        )

    @pytest.mark.asyncio
    async def test_the_platform_key_is_used_when_the_account_has_none(
        self, monkeypatch
    ):
        from unittest.mock import AsyncMock, patch

        from api.services.gen_ai.transcription import factory

        with (
            patch(
                "api.services.configuration.ai_model_configuration.get_resolved_ai_model_configuration",
                new=AsyncMock(return_value=self._resolved("sarvam", None)),
            ),
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                new=AsyncMock(return_value="platform-key"),
            ),
            patch("api.services.gen_ai.transcription.factory.db_client.async_session"),
        ):
            service = await factory.build_transcription_service(organization_id=7)
        assert isinstance(service, factory.SarvamTranscriptionService)

    @pytest.mark.asyncio
    async def test_no_key_anywhere_falls_to_the_managed_service_not_an_error(
        self, monkeypatch
    ):
        from unittest.mock import AsyncMock, patch

        from api.services.gen_ai.transcription import factory

        with (
            patch(
                "api.services.configuration.ai_model_configuration.get_resolved_ai_model_configuration",
                new=AsyncMock(return_value=self._resolved("sarvam", None)),
            ),
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                new=AsyncMock(return_value=None),
            ),
            patch("api.services.gen_ai.transcription.factory.db_client.async_session"),
        ):
            service = await factory.build_transcription_service(organization_id=7)
        assert isinstance(service, factory.MPSTranscriptionService)

    @pytest.mark.asyncio
    async def test_an_account_with_its_own_key_still_uses_it(self, monkeypatch):
        from unittest.mock import AsyncMock, patch

        from api.services.gen_ai.transcription import factory

        platform = AsyncMock(return_value="platform-key")
        with (
            patch(
                "api.services.configuration.ai_model_configuration.get_resolved_ai_model_configuration",
                new=AsyncMock(return_value=self._resolved("deepgram", "their-own")),
            ),
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                new=platform,
            ),
        ):
            service = await factory.build_transcription_service(organization_id=7)
        assert isinstance(service, factory.DeepgramTranscriptionService)
        assert not platform.await_count, "should not reach for ours when they have one"

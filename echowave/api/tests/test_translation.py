"""Translate where it is needed: an Indian-script message, a draft typed in
an Indian script. Arrival tests for the service and the route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.services import translation


class TestIndicDetection:
    def test_indian_scripts_are_seen_and_latin_is_not(self):
        assert translation.is_indic("வணக்கம், appointment please")
        assert translation.is_indic("कल सुबह दस बजे")
        assert translation.is_indic("ನಮಸ್ಕಾರ")
        assert not translation.is_indic("Book Meera at 4")
        assert not translation.is_indic("")


class TestChunking:
    def test_short_text_is_one_piece(self):
        assert translation.chunks("hello") == ["hello"]
        assert translation.chunks("   ") == []

    def test_long_text_is_cut_at_sentence_ends_under_the_limit(self):
        text = ("This is a sentence. " * 80).strip()
        pieces = translation.chunks(text)
        assert len(pieces) > 1
        assert all(len(p) <= translation.CHUNK_CHARS for p in pieces)
        assert all(p.endswith(".") for p in pieces)
        assert " ".join(pieces) == text


@pytest.mark.asyncio
class TestTheService:
    async def test_translate_sends_each_piece_and_joins_the_answers(self):
        calls = []

        async def fake_post(url, key, body, client):
            calls.append((url, key, body))
            return {
                "translated_text": f"[{body['input'][:5]}]",
                "source_language_code": "ta-IN",
            }

        long = ("இது ஒரு வாக்கியம். " * 90).strip()
        with (
            patch("api.services.translation._key", new=AsyncMock(return_value="k")),
            patch("api.services.translation._post", new=fake_post),
        ):
            text, source = await translation.translate(long, target="en-IN")
        assert source == "ta-IN"
        assert len(calls) > 1
        assert all(
            c[0] == translation.SARVAM_TRANSLATE_URL and c[1] == "k" for c in calls
        )
        assert calls[0][2]["target_language_code"] == "en-IN"
        assert calls[0][2]["source_language_code"] == "auto"
        assert text.count("[") == len(calls)

    async def test_no_platform_key_is_said_not_swallowed(self):
        with patch(
            "api.services.translation.platform_credentials.resolve_api_key",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(translation.TranslationUnavailable):
                await translation.translate("नमस्ते")

    async def test_an_unknown_language_is_refused_before_any_request(self):
        with pytest.raises(ValueError):
            await translation.translate("hi", target="fr-FR")


@pytest.mark.asyncio
class TestTheRoute:
    async def _post(self, body):
        from api.app import app
        from api.services.auth.depends import get_user

        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=1, selected_organization_id=7
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post("/api/v1/translate", json=body)
        finally:
            app.dependency_overrides.pop(get_user, None)

    async def test_translates_and_says_what_it_was(self):
        with patch(
            "api.routes.translate.translation.translate",
            new=AsyncMock(return_value=("Hello, appointment please", "ta-IN")),
        ):
            response = await self._post({"text": "வணக்கம், appointment please"})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "text": "Hello, appointment please",
            "source_language_code": "ta-IN",
            "target_language_code": "en-IN",
            "mode": "translate",
        }

    async def test_transliterates_on_request(self):
        with patch(
            "api.routes.translate.translation.transliterate",
            new=AsyncMock(return_value=("namaste", "hi-IN")),
        ) as tl:
            response = await self._post({"text": "नमस्ते", "mode": "transliterate"})
        assert response.status_code == 200
        assert response.json()["text"] == "namaste"
        assert tl.await_args.kwargs["target"] == "en-IN"

    async def test_no_key_is_a_503_the_screen_can_read(self):
        with patch(
            "api.routes.translate.translation.translate",
            new=AsyncMock(
                side_effect=translation.TranslationUnavailable(
                    "No Sarvam platform key is stored."
                )
            ),
        ):
            response = await self._post({"text": "नमस्ते"})
        assert response.status_code == 503
        assert "Sarvam" in response.json()["detail"]

    async def test_a_made_up_mode_is_refused(self):
        response = await self._post({"text": "hi", "mode": "summarise"})
        assert response.status_code == 422

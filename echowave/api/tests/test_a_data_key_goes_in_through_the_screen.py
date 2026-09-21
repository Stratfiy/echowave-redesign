"""A search vendor's key is entered on the provider-keys screen like any
other vendor's, checked against the vendor on save, and stored under the
``data`` component the search reads (OP-1's key, done properly)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from api.services.configuration import key_validation
from api.services.configuration.registry import components_for_provider, known_providers


class TestTheRegistry:
    def test_serper_is_a_known_vendor_serving_data(self):
        assert known_providers()["serper"] == ("data",)
        assert components_for_provider("serper") == ("data",)
        assert components_for_provider("SERPER ") == ("data",)

    def test_the_speech_and_model_vendors_are_unchanged(self):
        assert "data" not in known_providers()["openai"]
        assert components_for_provider("nobody") == ()


class TestTheCheck:
    def _client(self, status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == "k-123456789"
            return httpx.Response(status, json={"organic": []})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _validate(self, status: int):
        client = self._client(status)
        with patch("httpx.AsyncClient", lambda **kw: client):
            return await key_validation.validate_key("serper", "k-123456789")

    async def test_an_accepted_key_is_valid(self):
        result = await self._validate(200)
        assert result.outcome == "valid" and result.may_store

    async def test_a_rejected_key_is_refused(self):
        result = await self._validate(403)
        assert result.outcome == "invalid" and not result.may_store
        assert "Serper rejected" in result.message

    async def test_a_vendor_error_stores_and_says_so(self):
        result = await self._validate(500)
        assert result.outcome == "unverified" and result.may_store
        assert "500" in result.message

    async def test_a_vendor_that_cannot_be_reached_stores_and_says_so(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("dns")):
            result = await key_validation.validate_key("serper", "k-123456789")
        assert result.outcome == "unverified"

    def test_it_is_a_vendor_we_can_check(self):
        assert key_validation.can_validate("serper")

    async def test_other_vendors_take_the_old_path(self):
        probe = AsyncMock()
        with (
            patch.dict(key_validation._DATA_CHECKS, {}, clear=True),
            patch.object(key_validation, "_check_serper", probe),
            patch.object(
                key_validation, "_validator", side_effect=RuntimeError("no sdk")
            ),
        ):
            result = await key_validation.validate_key("serper", "k-123456789")
        probe.assert_not_awaited()
        assert result.outcome == "unverified"


class TestTheSearchReadsIt:
    async def test_the_stored_key_is_the_one_the_search_uses(self):
        import contextlib
        from types import SimpleNamespace

        from api.services.workflow import web_tools

        @contextlib.asynccontextmanager
        async def _session():
            yield object()

        with (
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                AsyncMock(return_value="stored-key"),
            ),
            patch("api.db.db_client", SimpleNamespace(async_session=_session)),
        ):
            assert await web_tools._search_key() == "stored-key"

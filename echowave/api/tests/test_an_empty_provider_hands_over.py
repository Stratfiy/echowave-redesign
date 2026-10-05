"""When the AI vendor's account runs out of credit, the turn is answered on
another vendor the platform holds a key for, and the empty one is skipped
for a while.

Seen in production on 3 October 2026: Anthropic answered every Decibyl and
Studio turn with 400 "Your credit balance is too low", the screen said "The
assistant hit an error", and nothing tried the Google key sitting beside it.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api.db import db_client as dbc
from api.services.agent_builder import client, settings

NO_CREDIT = httpx.Response(
    400,
    content=json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API.",
            },
        }
    ).encode(),
)
GEMINI_OK = httpx.Response(
    200,
    content=json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]}
    ).encode(),
)


@pytest.fixture(autouse=True)
def _fresh():
    client._exhausted_until.clear()
    yield
    client._exhausted_until.clear()


def _turn(**kw):
    return client.complete(
        provider=kw.get("provider", client.ANTHROPIC),
        model="claude-x",
        api_key=kw.get("api_key", "platform-anthropic"),
        system="s",
        conversation=client.Conversation(),
        tools=[],
    )


class TestDetection:
    def test_credit_messages_are_out_of_credit(self):
        assert client._out_of_credit(400, NO_CREDIT.text)
        assert client._out_of_credit(429, '{"error":{"code":"insufficient_quota"}}')

    def test_an_ordinary_error_or_rate_limit_is_not(self):
        assert not client._out_of_credit(400, '{"error":"bad tool schema"}')
        assert not client._out_of_credit(429, '{"error":"rate limit exceeded"}')
        assert not client._out_of_credit(500, "credit balance is too low")


@pytest.mark.asyncio
class TestHandOver:
    async def test_the_turn_is_answered_on_the_other_vendor(self):
        post = AsyncMock(side_effect=[NO_CREDIT, GEMINI_OK])
        with (
            patch.object(httpx.AsyncClient, "post", post),
            patch.object(
                client,
                "_fallback_model",
                AsyncMock(return_value=(client.GOOGLE, "gemini-x", "platform-google")),
            ),
        ):
            reply = await _turn()
        assert reply.text == "Hello from Gemini"
        assert "generativelanguage" in post.call_args_list[1].args[0]
        assert client.is_exhausted(client.ANTHROPIC)

    async def test_nowhere_to_go_says_out_of_credit(self):
        with (
            patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=NO_CREDIT)),
            patch.object(client, "_fallback_model", AsyncMock(return_value=None)),
        ):
            with pytest.raises(client.ProviderOutOfCredit) as caught:
                await _turn()
        assert "out of credit" in str(caught.value)
        # Still a BuilderClientError, so every route shows it as before.
        assert isinstance(caught.value, client.BuilderClientError)


def _keys(table):
    async def resolve(session, *, component, provider):
        return table.get(provider)

    @asynccontextmanager
    async def session():
        yield object()

    return resolve, session


@pytest.mark.asyncio
class TestWhereItFallsTo:
    async def test_the_next_platform_key_in_preference_order(self):
        resolve, session = _keys(
            {"anthropic": "platform-anthropic", "google": "platform-google"}
        )
        with (
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                resolve,
            ),
            patch.object(dbc, "async_session", session),
        ):
            other = await client._fallback_model("anthropic", "platform-anthropic")
        assert (
            other is not None and other[0] == "google" and other[2] == "platform-google"
        )

    async def test_a_workspaces_own_key_never_moves_onto_ours(self):
        resolve, session = _keys(
            {"anthropic": "platform-anthropic", "google": "platform-google"}
        )
        with (
            patch(
                "api.services.configuration.platform_credentials.resolve_api_key",
                resolve,
            ),
            patch.object(dbc, "async_session", session),
        ):
            assert await client._fallback_model("anthropic", "their-own-key") is None


@pytest.mark.asyncio
async def test_the_next_turn_starts_on_the_vendor_that_has_credit(monkeypatch):
    client.mark_exhausted("anthropic")
    monkeypatch.setattr(settings.constants, "AGENT_BUILDER_ENABLED", True)
    monkeypatch.setattr(settings.constants, "AGENT_BUILDER_PROVIDER", "")
    monkeypatch.setattr(
        settings.constants, "AGENT_BUILDER_PROVIDER_PREFERENCE", ("anthropic", "google")
    )

    async def resolve(session, *, component, provider):
        return {"anthropic": "a", "google": "g"}.get(provider)

    monkeypatch.setattr(settings.platform_credentials, "resolve_api_key", resolve)
    chosen = await settings.resolve_model(object())
    assert chosen.provider == "google"

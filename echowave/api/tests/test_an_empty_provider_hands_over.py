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
    # A successful turn meters itself; keep those rows out of the shared test
    # DB, where test_every_model_call_is_metered counts them.
    client._exhausted_until.clear()
    with patch.object(client.model_usage, "record", AsyncMock()):
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


RATE_LIMITED = httpx.Response(
    429,
    headers={"retry-after": "1"},
    content=b'{"type":"error","error":{"type":"rate_limit_error","message":"Number of request tokens has exceeded your per-minute rate limit"}}',
)
ANTHROPIC_OK = httpx.Response(
    200,
    content=json.dumps(
        {"content": [{"type": "text", "text": "Hello again"}], "usage": {}}
    ).encode(),
)


@pytest.mark.asyncio
class TestARateLimitIsRidden:
    """Seen in production on 6 October 2026: a 695 KB Apollo CSV and a deck
    on the thread, three messages in a minute, and every reply after the
    first was "I could not think that through" -- a 429 from the vendor."""

    async def test_a_short_wait_then_the_same_vendor(self, monkeypatch):
        slept = []

        async def sleep(seconds):
            slept.append(seconds)

        monkeypatch.setattr(client.asyncio, "sleep", sleep)
        post = AsyncMock(side_effect=[RATE_LIMITED, ANTHROPIC_OK])
        with patch.object(httpx.AsyncClient, "post", post):
            reply = await _turn()
        assert reply.text == "Hello again"
        assert slept == [1.0]
        assert not client.is_exhausted(client.ANTHROPIC)

    async def test_still_limited_goes_to_the_other_vendor(self, monkeypatch):
        async def sleep(seconds):
            return None

        monkeypatch.setattr(client.asyncio, "sleep", sleep)
        post = AsyncMock(side_effect=[RATE_LIMITED, RATE_LIMITED, GEMINI_OK])
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
        # Tried last for a couple of minutes, not the half hour of no credit.
        assert client.is_exhausted(client.ANTHROPIC)
        assert (
            client._exhausted_until[client.ANTHROPIC] - client.time.monotonic()
            <= client.RATE_LIMITED_FOR_SECONDS + 1
        )

    async def test_nowhere_to_go_says_rate_limited(self, monkeypatch):
        async def sleep(seconds):
            return None

        monkeypatch.setattr(client.asyncio, "sleep", sleep)
        with (
            patch.object(
                httpx.AsyncClient, "post", AsyncMock(return_value=RATE_LIMITED)
            ),
            patch.object(client, "_fallback_model", AsyncMock(return_value=None)),
        ):
            with pytest.raises(client.ProviderRateLimited) as caught:
                await _turn()
        assert "rate limited" in str(caught.value)

    def test_a_rate_limit_never_shortens_an_out_of_credit_mark(self):
        client.mark_exhausted("anthropic")
        long_mark = client._exhausted_until["anthropic"]
        client.mark_exhausted("anthropic", client.RATE_LIMITED_FOR_SECONDS)
        assert client._exhausted_until["anthropic"] == long_mark

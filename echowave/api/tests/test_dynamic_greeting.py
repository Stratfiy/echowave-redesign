"""The dynamic greeting, which is mostly a study in failing safely.

Somebody is on the line while this runs. Every test below is really the same
assertion — that whatever goes wrong, the agent still says something — because
there is no error state better than the greeting the customer already wrote.
"""

import httpx
import pytest

from api.services.pipecat import dynamic_greeting as dg

FALLBACK = "Hello, thanks for calling Sunrise Dental."
CONFIG = {"enabled": True, "url": "https://example.com/greeting"}


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def _client(monkeypatch, behaviour):
    """Stand in for httpx.AsyncClient with a scripted post()."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            FakeClient.last_kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            FakeClient.last_url = url
            FakeClient.last_body = json
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour

    monkeypatch.setattr(dg.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        dg, "validate_user_configured_service_url", lambda *a, **k: None
    )
    return FakeClient


class TestTheHappyPath:
    async def test_uses_the_returned_greeting(self, monkeypatch):
        _client(
            monkeypatch, _Response({"greeting": "Hello Priya, your order shipped."})
        )
        assert await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == (
            "Hello Priya, your order shipped."
        )

    @pytest.mark.parametrize("key", ["greeting", "message", "text"])
    async def test_accepts_three_obvious_key_names(self, monkeypatch, key):
        """A customer should not have to read our docs to guess which one."""
        _client(monkeypatch, _Response({key: "Hi there"}))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == "Hi there"
        )

    async def test_accepts_a_bare_string_body(self, monkeypatch):
        _client(monkeypatch, _Response("Hi there"))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == "Hi there"
        )

    async def test_posts_the_call_context(self, monkeypatch):
        client = _client(monkeypatch, _Response({"greeting": "Hi"}))
        await dg.fetch_greeting(
            CONFIG, context={"phone_number": "+919876543210"}, fallback=FALLBACK
        )
        assert client.last_body == {"phone_number": "+919876543210"}

    async def test_a_long_greeting_is_truncated_not_rejected(self, monkeypatch):
        """An endpoint returning an essay should still open the call.

        The first sentence of too much is better than the fallback.
        """
        _client(monkeypatch, _Response({"greeting": "x" * 900}))
        result = await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK)
        assert len(result) == dg.MAX_GREETING_CHARS


class TestEveryFailureEndsInAGreeting:
    async def test_disabled(self, monkeypatch):
        _client(monkeypatch, _Response({"greeting": "should not be used"}))
        assert (
            await dg.fetch_greeting(
                {"enabled": False, "url": "https://example.com"},
                context={},
                fallback=FALLBACK,
            )
            == FALLBACK
        )

    @pytest.mark.parametrize(
        "config",
        [None, {}, "not-a-dict", {"enabled": True}, {"enabled": True, "url": "   "}],
    )
    async def test_unusable_configuration(self, config):
        assert (
            await dg.fetch_greeting(config, context={}, fallback=FALLBACK) == FALLBACK
        )

    async def test_a_refused_url_does_not_raise(self, monkeypatch):
        """The SSRF guard rejecting the URL is a config error, not a call error."""
        _client(monkeypatch, _Response({"greeting": "nope"}))
        monkeypatch.setattr(
            dg,
            "validate_user_configured_service_url",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("private address")),
        )
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == FALLBACK
        )

    async def test_timeout(self, monkeypatch):
        _client(monkeypatch, httpx.ReadTimeout("too slow"))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == FALLBACK
        )

    async def test_server_error(self, monkeypatch):
        _client(monkeypatch, _Response({"greeting": "x"}, status=500))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == FALLBACK
        )

    async def test_body_is_not_json(self, monkeypatch):
        _client(monkeypatch, _Response(ValueError("not json")))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == FALLBACK
        )

    @pytest.mark.parametrize("payload", [{}, {"greeting": ""}, {"greeting": 7}, [], 42])
    async def test_a_body_with_no_usable_greeting(self, monkeypatch, payload):
        _client(monkeypatch, _Response(payload))
        assert (
            await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK) == FALLBACK
        )


class TestTimeoutBounds:
    def test_default(self):
        assert dg._timeout_seconds({}) == dg.DEFAULT_TIMEOUT_MS / 1000

    def test_capped(self):
        """A customer cannot make the caller wait indefinitely.

        Dead air on answer is the worst thing this product can do, so the
        ceiling is ours rather than theirs.
        """
        assert dg._timeout_seconds({"timeout_ms": 60_000}) == dg.MAX_TIMEOUT_MS / 1000

    def test_floored(self):
        assert dg._timeout_seconds({"timeout_ms": 1}) == 0.2

    @pytest.mark.parametrize("raw", ["soon", None, [], {}])
    def test_an_unreadable_value_falls_back_to_the_default(self, raw):
        assert dg._timeout_seconds({"timeout_ms": raw}) == dg.DEFAULT_TIMEOUT_MS / 1000


class TestRedirects:
    async def test_redirects_are_not_followed(self, monkeypatch):
        """A redirect is a second URL the SSRF guard never saw.

        Following one would be the way around every check above it.
        """
        client = _client(monkeypatch, _Response({"greeting": "Hi"}))
        await dg.fetch_greeting(CONFIG, context={}, fallback=FALLBACK)
        assert client.last_kwargs["follow_redirects"] is False

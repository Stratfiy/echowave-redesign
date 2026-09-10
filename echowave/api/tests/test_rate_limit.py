"""The coarse HTTP request gate.

The properties worth defending: it counts per client per route class, it
refuses over the limit with a Retry-After, and — the one that matters most —
it fails open, so a Redis outage never turns into a 429 storm across the
whole API.
"""

import pytest
from starlette.datastructures import Headers

import api.middleware_rate_limit as mw
from api.middleware_rate_limit import RateLimitMiddleware, _classify, _identity
from api.services.rate_limit import RateLimiter


class TestClassify:
    def test_auth_paths_get_the_tight_bucket(self):
        for path in (
            "/api/v1/auth/login",
            "/api/v1/otp/send",
            "/api/v1/password/reset",
        ):
            bucket, _ = _classify(path)
            assert bucket == "auth", path

    def test_public_embed_paths_get_the_embed_bucket(self):
        for path in ("/api/v1/public/embed/init", "/talk/emb_abc", "/api/v1/embed/x"):
            bucket, _ = _classify(path)
            assert bucket == "embed", path

    def test_everything_else_is_default(self):
        assert _classify("/api/v1/workflow/17")[0] == "default"

    def test_health_and_docs_are_never_counted(self):
        for path in (
            "/api/v1/health",
            "/api/v1/health/active-calls",
            "/api/v1/openapi.json",
        ):
            assert _classify(path) is None, path


class TestIdentity:
    def test_api_key_wins_and_only_the_prefix_is_used(self):
        headers = Headers({"x-api-key": "dcb_supersecretvalue_1234567890"})
        ident = _identity(headers, {})
        assert ident == "key:dcb_supersec"
        assert "supersecretvalue" not in ident

    def test_forwarded_ip_when_no_key(self):
        headers = Headers({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
        assert _identity(headers, {}) == "ip:203.0.113.9"

    def test_socket_ip_is_the_last_resort(self):
        assert (
            _identity(Headers({}), {"client": ("198.51.100.2", 5555)})
            == "ip:198.51.100.2"
        )


class _FakeRedis:
    def __init__(self, *, raises=False):
        self.counts: dict = {}
        self.raises = raises

    async def eval(self, script, numkeys, key, window):
        if self.raises:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def ttl(self, key):
        return 60


@pytest.mark.asyncio
async def test_counts_up_to_the_limit_then_refuses():
    limiter = RateLimiter()
    limiter._redis = _FakeRedis()
    ok = []
    for _ in range(4):
        allowed, retry = await limiter.check(
            bucket="auth", identity="ip:1.2.3.4", limit=3, window_secs=60
        )
        ok.append(allowed)
    assert ok == [True, True, True, False]
    _, retry = await limiter.check(
        bucket="auth", identity="ip:1.2.3.4", limit=3, window_secs=60
    )
    assert retry == 60  # Retry-After from the window TTL


@pytest.mark.asyncio
async def test_separate_identities_do_not_share_a_bucket():
    limiter = RateLimiter()
    limiter._redis = _FakeRedis()
    a, _ = await limiter.check(
        bucket="default", identity="key:a", limit=1, window_secs=60
    )
    b, _ = await limiter.check(
        bucket="default", identity="key:b", limit=1, window_secs=60
    )
    assert a is True and b is True  # first hit each; neither spent the other's


@pytest.mark.asyncio
async def test_it_fails_open_when_redis_is_down():
    limiter = RateLimiter()
    limiter._redis = _FakeRedis(raises=True)
    for _ in range(100):
        allowed, retry = await limiter.check(
            bucket="auth", identity="ip:1.2.3.4", limit=1, window_secs=60
        )
        assert allowed is True
        assert retry == 0


class _Recorder:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


@pytest.mark.asyncio
async def test_middleware_returns_429_when_the_limiter_refuses(monkeypatch):
    async def refuse(**kwargs):
        return False, 30

    monkeypatch.setattr(mw.rate_limiter, "check", refuse)
    monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)
    inner = _Recorder()
    middleware = RateLimitMiddleware(inner)

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
        "client": ("1.2.3.4", 5000),
    }
    await middleware(scope, None, send)

    assert inner.called is False  # request never reached the app
    assert sent[0]["status"] == 429
    assert (b"retry-after", b"30") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_middleware_lets_health_through_uncounted(monkeypatch):
    called = {"check": False}

    async def check(**kwargs):
        called["check"] = True
        return True, 0

    monkeypatch.setattr(mw.rate_limiter, "check", check)
    monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", True)
    inner = _Recorder()
    scope = {"type": "http", "method": "GET", "path": "/api/v1/health", "headers": []}
    await RateLimitMiddleware(inner)(scope, None, lambda m: None)
    assert inner.called is True
    assert called["check"] is False  # exempt paths are never counted


@pytest.mark.asyncio
async def test_disabled_middleware_is_a_passthrough(monkeypatch):
    monkeypatch.setattr(mw, "RATE_LIMIT_ENABLED", False)
    inner = _Recorder()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": [],
    }
    await RateLimitMiddleware(inner)(scope, None, lambda m: None)
    assert inner.called is True

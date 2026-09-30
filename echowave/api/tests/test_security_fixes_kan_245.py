"""KAN-245: the four security fixes hold.

1. A ``rediss://`` queue URL is verified (CA, hostname); the escape hatch is
   explicit.
2. A failure inside Stack Auth user or organisation creation returns a 500
   whose body carries no exception text.
3. The websocket dependency takes the credential from
   ``Sec-WebSocket-Protocol`` and still accepts the query form for one release.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.services.auth import depends as auth_depends
from api.tasks.redis_settings import build_redis_settings

# ---------------------------------------------------------------------------
# 1. Redis TLS
# ---------------------------------------------------------------------------


def test_rediss_url_is_verified_by_default():
    settings = build_redis_settings("rediss://:pw@cache.example.com:6380")

    assert settings.ssl is True
    assert settings.host == "cache.example.com"
    assert settings.port == 6380
    assert settings.password == "pw"
    assert settings.ssl_cert_reqs == "required"
    assert settings.ssl_check_hostname is True
    assert settings.ssl_ca_certs is None  # system trust store


def test_private_ca_bundle_is_passed_through():
    settings = build_redis_settings(
        "rediss://cache.internal", ca_certs="/etc/decibyl/redis-ca.pem"
    )
    assert settings.ssl_ca_certs == "/etc/decibyl/redis-ca.pem"
    assert settings.ssl_cert_reqs == "required"


def test_verification_can_only_be_turned_off_on_purpose():
    settings = build_redis_settings("rediss://cache.internal", verify=False)
    assert settings.ssl_cert_reqs == "none"
    assert settings.ssl_check_hostname is False


def test_plain_redis_url_has_no_tls_fields():
    settings = build_redis_settings("redis://:pw@redis:6379")
    assert settings.ssl is False
    assert settings.ssl_cert_reqs is None
    assert settings.ssl_check_hostname is None
    assert settings.ssl_ca_certs is None


# ---------------------------------------------------------------------------
# 2. Stack Auth 500s carry no exception text
# ---------------------------------------------------------------------------

SECRET_DETAIL = "postgresql://decibyl:hunter2@db/prod duplicate key"


def _stack_user():
    return {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "primary_email_verified": False,
    }


async def test_user_creation_failure_hides_the_exception(monkeypatch):
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    monkeypatch.setattr(
        auth_depends.stackauth, "get_user", AsyncMock(return_value=_stack_user())
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_user_by_provider_id",
        AsyncMock(side_effect=RuntimeError(SECRET_DETAIL)),
    )

    with pytest.raises(HTTPException) as excinfo:
        await auth_depends.get_user(authorization="Bearer t")

    assert excinfo.value.status_code == 500
    assert "hunter2" not in excinfo.value.detail
    assert "duplicate key" not in excinfo.value.detail
    assert excinfo.value.detail == auth_depends.AUTH_INTERNAL_ERROR_DETAIL


async def test_organization_mapping_failure_hides_the_exception(monkeypatch):
    user = SimpleNamespace(
        id=7, email=None, provider_id="stack-user-1", selected_organization_id=None
    )
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    monkeypatch.setattr(
        auth_depends.stackauth, "get_user", AsyncMock(return_value=_stack_user())
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_user_by_provider_id",
        AsyncMock(return_value=(user, False)),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_organization_by_provider_id",
        AsyncMock(side_effect=RuntimeError(SECRET_DETAIL)),
    )

    with pytest.raises(HTTPException) as excinfo:
        await auth_depends.get_user(authorization="Bearer t")

    assert excinfo.value.status_code == 500
    assert SECRET_DETAIL not in excinfo.value.detail
    assert excinfo.value.detail == auth_depends.AUTH_INTERNAL_ERROR_DETAIL


# ---------------------------------------------------------------------------
# 3. WebSocket credential in the subprotocol header
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self, protocols: str | None = None):
        self.headers = {"sec-websocket-protocol": protocols} if protocols else {}
        self.url = SimpleNamespace(path="/api/v1/ws/signaling/1/2")
        self.closed_with = None

    async def close(self, code=None, reason=None):
        self.closed_with = (code, reason)


async def _capture_get_user(monkeypatch):
    calls = []

    async def fake_get_user(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(auth_depends, "get_user", fake_get_user)
    return calls


async def test_bearer_token_in_subprotocol_is_used(monkeypatch):
    calls = await _capture_get_user(monkeypatch)
    ws = _FakeWebSocket("decibyl.auth, bearer.eyJhbGciOi.payload.sig")

    user = await auth_depends.get_user_ws(ws, token=None, api_key=None)

    assert user.id == 1
    assert calls == [{"authorization": "Bearer eyJhbGciOi.payload.sig"}]
    assert auth_depends.ws_accept_subprotocol(ws) == "decibyl.auth"


async def test_api_key_in_subprotocol_is_used(monkeypatch):
    calls = await _capture_get_user(monkeypatch)
    ws = _FakeWebSocket("decibyl.auth,apikey.dcb_live_abc")

    await auth_depends.get_user_ws(ws, token=None, api_key=None)

    assert calls == [{"x_api_key": "dcb_live_abc"}]


async def test_header_credential_wins_over_query_string(monkeypatch):
    calls = await _capture_get_user(monkeypatch)
    ws = _FakeWebSocket("decibyl.auth, bearer.from-header")

    await auth_depends.get_user_ws(ws, token="from-query", api_key=None)

    assert calls == [{"authorization": "Bearer from-header"}]


async def test_query_string_form_still_works_for_one_release(monkeypatch):
    calls = await _capture_get_user(monkeypatch)
    ws = _FakeWebSocket()

    await auth_depends.get_user_ws(ws, token="legacy-token", api_key=None)

    assert calls == [{"authorization": "Bearer legacy-token"}]
    assert auth_depends.ws_accept_subprotocol(ws) is None


async def test_no_credential_closes_the_socket(monkeypatch):
    await _capture_get_user(monkeypatch)
    ws = _FakeWebSocket()

    with pytest.raises(HTTPException) as excinfo:
        await auth_depends.get_user_ws(ws, token=None, api_key=None)

    assert excinfo.value.status_code == 401
    assert ws.closed_with[0] == 1008

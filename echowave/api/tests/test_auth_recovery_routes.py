from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import auth as routes
from api.services.auth import depends, google_oauth, password_reset


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(depends, "AUTH_PROVIDER", "local")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    return TestClient(app, base_url="https://api.example.com")


def test_reset_request_does_not_disclose_account_existence(client, monkeypatch):
    monkeypatch.setattr(password_reset, "email_is_configured", lambda: True)
    issue = AsyncMock()
    monkeypatch.setattr(password_reset, "issue_link", issue)
    response = client.post(
        "/api/v1/auth/password-reset/request", json={"email": "u@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"message": password_reset.GENERIC_MESSAGE}
    issue.assert_awaited_once_with("u@example.com")


def test_reset_mail_unavailable_is_actionable(client, monkeypatch):
    monkeypatch.setattr(password_reset, "email_is_configured", lambda: False)
    assert (
        client.post(
            "/api/v1/auth/password-reset/request", json={"email": "u@example.com"}
        ).status_code
        == 503
    )


def test_reset_confirm_handles_invalid_link_and_validates_password(client, monkeypatch):
    reset = AsyncMock(return_value=False)
    monkeypatch.setattr(password_reset, "reset_password", reset)
    body = {"token": "a" * 43, "password": "new-password"}
    assert (
        client.post("/api/v1/auth/password-reset/confirm", json=body).status_code == 400
    )
    body["password"] = "short"
    assert (
        client.post("/api/v1/auth/password-reset/confirm", json=body).status_code == 422
    )
    assert reset.await_count == 1


def test_local_recovery_routes_are_hidden_in_stack_mode(client, monkeypatch):
    monkeypatch.setattr(depends, "AUTH_PROVIDER", "stack")
    assert (
        client.post(
            "/api/v1/auth/password-reset/request", json={"email": "u@example.com"}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/auth/google/status").status_code == 404


def test_google_readiness_does_not_issue_state_or_cookies(client, monkeypatch):
    monkeypatch.setattr(google_oauth, "is_enabled", lambda: True)
    response = client.get("/api/v1/auth/google/status")
    assert response.json() == {"enabled": True}
    assert "set-cookie" not in response.headers


def test_google_start_sets_browser_cookie_and_redirects_to_minimal_scopes(
    client, monkeypatch
):
    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_ID", "test-id")
    monkeypatch.setattr(google_oauth, "GOOGLE_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(routes, "BACKEND_API_ENDPOINT", "https://api.example.com")
    response = client.get(
        "/api/v1/auth/google/start?redirect=true", follow_redirects=False
    )
    assert response.status_code == 303
    params = parse_qs(urlparse(response.headers["location"]).query)
    assert set(params["scope"][0].split()) == {"openid", "email", "profile"}
    assert params["redirect_uri"] == [
        "https://api.example.com/api/v1/auth/google/callback"
    ]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert google_oauth.browser_state_digest(params["state"][0]) in cookie
    assert response.headers["cache-control"] == "no-store"


def test_google_callback_rejects_another_browser_before_token_exchange(
    client, monkeypatch
):
    complete = AsyncMock()
    monkeypatch.setattr(google_oauth, "complete_sign_in", complete)
    state = google_oauth._issue_state(nonce="other-browser", next_path=None)
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "unused", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/auth/login?error=" in response.headers["location"]
    complete.assert_not_awaited()

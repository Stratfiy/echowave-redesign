from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from api.schemas.auth import PasswordResetConfirm
from api.services.auth import google_oauth, password_reset
from api.utils.auth import (
    create_jwt_token,
    decode_jwt_token,
    session_version_matches,
    verify_password,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("password_hash", ["hash", None])
async def test_reset_email_contains_only_the_raw_fragment_token_and_storage_gets_a_digest(
    monkeypatch,
    password_hash,
):
    monkeypatch.setattr(password_reset, "email_is_configured", lambda: True)
    user = SimpleNamespace(id=7, email="user@example.com", password_hash=password_hash)
    db = SimpleNamespace(
        get_user_by_email=AsyncMock(return_value=user),
        issue_password_reset=AsyncMock(return_value=True),
    )
    sender = AsyncMock(return_value=SimpleNamespace(ok=True))
    now = datetime.now(UTC)
    await password_reset.issue_link(" USER@example.com ", db=db, sender=sender, now=now)
    db.get_user_by_email.assert_awaited_once_with("user@example.com")
    body = sender.call_args.kwargs["body_text"]
    link = next(line for line in body.splitlines() if "/auth/reset-password#" in line)
    token = parse_qs(urlparse(link).fragment)["token"][0]
    assert not urlparse(link).query
    assert len(token) >= 40
    saved = db.issue_password_reset.call_args.kwargs
    assert saved["token_hash"] == password_reset.token_digest(token)
    assert saved["token_hash"] != token
    assert saved["expires_at"] == now + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_unknown_accounts_do_not_get_reset_mail(monkeypatch):
    user = None
    monkeypatch.setattr(password_reset, "email_is_configured", lambda: True)
    db = SimpleNamespace(
        get_user_by_email=AsyncMock(return_value=user), issue_password_reset=AsyncMock()
    )
    sender = AsyncMock()
    await password_reset.issue_link("user@example.com", db=db, sender=sender)
    sender.assert_not_awaited()
    db.issue_password_reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limited_issue_does_not_send_mail(monkeypatch):
    monkeypatch.setattr(password_reset, "email_is_configured", lambda: True)
    db = SimpleNamespace(
        get_user_by_email=AsyncMock(
            return_value=SimpleNamespace(
                id=1, email="u@example.com", password_hash="hash"
            )
        ),
        issue_password_reset=AsyncMock(return_value=False),
    )
    sender = AsyncMock()
    await password_reset.issue_link("u@example.com", db=db, sender=sender)
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_or_expired_token_never_hashes_a_password(monkeypatch):
    db = SimpleNamespace(
        has_password_reset=AsyncMock(return_value=False),
        consume_password_reset=AsyncMock(),
    )
    monkeypatch.setattr(
        password_reset,
        "hash_password",
        lambda _: pytest.fail("invalid token must not trigger bcrypt"),
    )
    assert not await password_reset.reset_password("a" * 43, "new-password", db=db)
    db.consume_password_reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_hashes_password_and_leaves_atomic_consumption_to_database():
    db = SimpleNamespace(
        has_password_reset=AsyncMock(return_value=True),
        consume_password_reset=AsyncMock(return_value=True),
    )
    assert await password_reset.reset_password("a" * 43, "new-password", db=db)
    args = db.consume_password_reset.call_args.kwargs
    assert args["token_hash"] == password_reset.token_digest("a" * 43)
    assert args["password_hash"] != "new-password"
    assert verify_password("new-password", args["password_hash"])


def test_password_reset_validation_matches_signup_and_bcrypt_byte_limits():
    for password in ["short", "a" * 73, "ह" * 25]:
        with pytest.raises(ValidationError):
            PasswordResetConfirm(token="a" * 43, password=password)
    assert (
        PasswordResetConfirm(token="a" * 43, password="long-enough").password
        == "long-enough"
    )


def test_reset_revokes_legacy_and_current_browser_sessions():
    legacy = {"sub": "7"}
    current = decode_jwt_token(create_jwt_token(7, "u@example.com", 2))
    assert session_version_matches(legacy, 0)
    assert not session_version_matches(legacy, 1)
    assert session_version_matches(current, 2)
    assert not session_version_matches(current, 3)


def test_google_state_requires_the_initiating_browser_cookie():
    state = google_oauth._issue_state(nonce="browser-one", next_path=None)
    google_oauth.verify_browser_state(state, google_oauth.browser_state_digest(state))
    for cookie in [None, "", "another-browser"]:
        with pytest.raises(google_oauth.GoogleAuthError):
            google_oauth.verify_browser_state(state, cookie)

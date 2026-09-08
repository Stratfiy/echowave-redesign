from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import PasswordResetChallengeModel, UserModel


@pytest.mark.asyncio
async def test_recovery_consumes_once_preserves_mfa_and_revokes_sessions(
    db_session, async_session
):
    user = UserModel(
        provider_id="reset-test",
        email="reset@example.com",
        password_hash="old",
        mfa_enabled=True,
        mfa_secret_encrypted="encrypted",
        auth_version=0,
    )
    async_session.add(user)
    await async_session.commit()
    now = datetime.now(UTC)
    assert await db_session.issue_password_reset(
        user.id, token_hash="a" * 64, expires_at=now + timedelta(minutes=30), now=now
    )
    assert not await db_session.issue_password_reset(
        user.id, token_hash="b" * 64, expires_at=now + timedelta(minutes=30), now=now
    )
    assert await db_session.consume_password_reset(
        token_hash="a" * 64, password_hash="new", now=now
    )
    assert not await db_session.consume_password_reset(
        token_hash="a" * 64, password_hash="replayed", now=now
    )
    await async_session.refresh(user)
    assert user.password_hash == "new"
    assert user.auth_version == 1
    assert user.mfa_enabled and user.mfa_secret_encrypted == "encrypted"
    assert await async_session.get(PasswordResetChallengeModel, user.id) is None


@pytest.mark.asyncio
async def test_recovery_expiry_and_hourly_send_window(db_session, async_session):
    user = UserModel(
        provider_id="reset-expiry",
        email="expiry@example.com",
        password_hash="old",
        auth_version=0,
    )
    async_session.add(user)
    await async_session.commit()
    now = datetime.now(UTC)
    for attempt in range(5):
        moment = now + timedelta(minutes=attempt)
        assert await db_session.issue_password_reset(
            user.id,
            token_hash=str(attempt) * 64,
            expires_at=moment + timedelta(minutes=30),
            now=moment,
        )
    assert not await db_session.issue_password_reset(
        user.id,
        token_hash="f" * 64,
        expires_at=now + timedelta(hours=1),
        now=now + timedelta(minutes=6),
    )
    assert not await db_session.consume_password_reset(
        token_hash="4" * 64, password_hash="late", now=now + timedelta(minutes=35)
    )
    assert await db_session.issue_password_reset(
        user.id,
        token_hash="f" * 64,
        expires_at=now + timedelta(hours=2),
        now=now + timedelta(hours=1),
    )

"""A forgotten password has a way back that says nothing to strangers.

The mechanics are the email-verification ones and are tested there; what is
guarded here is the two things that make a reset flow safe rather than a
second way in: every outcome looks the same from outside, and the code dies
with the password it set.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db import db_client
from api.db.models import UserModel
from api.services.auth import password_reset as pr
from api.utils.auth import hash_password, verify_password

EMAIL = "forgetful@example.com"


async def _user(
    session, slug: str, *, email: str = EMAIL, password: str | None = "old-password-1"
):
    user = UserModel(
        provider_id=f"user-{slug}",
        email=email,
        password_hash=hash_password(password) if password else None,
    )
    session.add(user)
    await session.flush()
    return user.id


class TestTheMail:
    def test_the_code_is_in_the_body_and_the_subject_says_reset(self):
        assert "123456" in pr.body("123456")
        assert "reset" in pr.subject().lower()

    def test_a_google_only_account_is_told_how_to_get_in(self):
        assert "Google" in pr.google_only_body()
        assert "code" not in pr.google_only_body().lower().split("sign in")[0]


@pytest.mark.asyncio
class TestStarting:
    async def test_an_unknown_address_gets_nothing_and_no_error(self, db_session):
        assert await pr.start_reset("nobody@example.com") is None

    async def test_a_known_address_gets_a_code(self, db_session, async_session):
        user_id = await _user(async_session, "known")
        started = await pr.start_reset(EMAIL)
        assert isinstance(started, pr.StartedReset)
        assert started.user_id == user_id
        assert len(started.code) == 6
        challenge = await db_client.get_password_reset_challenge(user_id)
        assert challenge is not None
        assert challenge.code_hash != started.code

    async def test_a_google_only_account_is_not_given_a_code(
        self, db_session, async_session
    ):
        await _user(async_session, "google", email="g@example.com", password=None)
        assert isinstance(await pr.start_reset("g@example.com"), pr.NoPasswordToReset)


@pytest.mark.asyncio
class TestConfirming:
    async def test_the_right_code_sets_the_password(self, db_session, async_session):
        user_id = await _user(async_session, "confirm")
        started = await pr.start_reset(EMAIL)

        await pr.confirm_reset(EMAIL, started.code, "brand-new-password")

        user = await db_client.get_user_by_id(user_id)
        assert verify_password("brand-new-password", user.password_hash)
        assert not verify_password("old-password-1", user.password_hash)

    async def test_the_code_dies_with_the_password_it_set(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "dies")
        started = await pr.start_reset(EMAIL)
        await pr.confirm_reset(EMAIL, started.code, "brand-new-password")

        assert await db_client.get_password_reset_challenge(user_id) is None
        with pytest.raises(pr.CodeIncorrect):
            await pr.confirm_reset(EMAIL, started.code, "another-password-2")

    async def test_a_wrong_code_changes_nothing(self, db_session, async_session):
        user_id = await _user(async_session, "wrong")
        started = await pr.start_reset(EMAIL)
        wrong = "000000" if started.code != "000000" else "111111"

        with pytest.raises(pr.CodeIncorrect):
            await pr.confirm_reset(EMAIL, wrong, "brand-new-password")
        user = await db_client.get_user_by_id(user_id)
        assert verify_password("old-password-1", user.password_hash)

    async def test_an_unknown_address_looks_like_a_wrong_code(self, db_session):
        with pytest.raises(pr.CodeIncorrect):
            await pr.confirm_reset("nobody@example.com", "123456", "brand-new-password")

    async def test_an_expired_code_is_refused(self, db_session, async_session):
        await _user(async_session, "expired")
        started = await pr.start_reset(EMAIL)
        later = datetime.now(UTC) + timedelta(minutes=11)
        with pytest.raises(pr.CodeExpired):
            await pr.confirm_reset(EMAIL, started.code, "brand-new-password", now=later)

    async def test_a_short_password_is_refused_before_the_code_is_spent(
        self, db_session, async_session
    ):
        await _user(async_session, "short")
        started = await pr.start_reset(EMAIL)
        with pytest.raises(pr.PasswordTooShort):
            await pr.confirm_reset(EMAIL, started.code, "short")
        # The code is still good.
        await pr.confirm_reset(EMAIL, started.code, "long-enough-now")

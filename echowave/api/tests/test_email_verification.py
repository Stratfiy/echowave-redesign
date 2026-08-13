"""Proving an email address at signup.

Delivered over the existing SMTP path, which is already configured and already
sends invoices in production — which is why this is finishable where phone
verification is not. Email carries no equivalent of India's DLT registration.

The limits are tested hardest, for the same reason as the phone flow: each
closes a different abuse and none substitutes for another.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.constants import VERIFICATION_MAX_ATTEMPTS, VERIFICATION_MAX_SENDS
from api.db import db_client
from api.db.models import UserModel
from api.services.auth import email_verification as ev
from api.services.auth import otp

EMAIL = "someone@example.com"


async def _user(session, slug: str, *, email: str = EMAIL) -> int:
    user = UserModel(provider_id=f"user-{slug}", email=email)
    session.add(user)
    await session.flush()
    return user.id


def _later(minutes: int = 0) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


class TestTheSharedPrimitives:
    def test_a_code_is_six_digits(self):
        for _ in range(50):
            code = otp.generate_code()
            assert len(code) == 6 and code.isdigit()

    def test_salting_is_what_defeats_a_rainbow_table(self):
        """Six digits is a million possibilities. An unsalted digest of one is
        a lookup, which is why this differs from hash_recovery_code."""
        assert otp.hash_code("123456", "aaaa") != otp.hash_code("123456", "bbbb")

    def test_phone_and_email_share_one_implementation(self):
        """Two copies of these decisions drift, and the drift is always in the
        direction of the weaker one."""
        from api.services.telephony import verified_numbers as vn

        assert vn.hash_code is otp.hash_code
        assert vn.generate_code is otp.generate_code


class TestStarting:
    async def test_it_stores_only_the_hash(self, db_session, async_session):
        user_id = await _user(async_session, "issue")
        started = await ev.start_verification(user_id, EMAIL)

        challenge = await db_client.get_email_challenge(user_id)
        assert started.code not in challenge.code_hash
        assert challenge.code_hash == otp.hash_code(started.code, challenge.code_salt)

    async def test_the_address_is_lowercased(self, db_session, async_session):
        user_id = await _user(async_session, "case")
        started = await ev.start_verification(user_id, "Someone@Example.COM")
        assert started.email == EMAIL

    async def test_a_resend_inside_the_cooldown_is_refused(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "cooldown")
        await ev.start_verification(user_id, EMAIL)
        with pytest.raises(ev.ResendTooSoon):
            await ev.start_verification(user_id, EMAIL)

    async def test_the_send_ceiling_protects_our_sending_reputation(
        self, db_session, async_session
    ):
        """An uncapped endpoint is a way to have our domain mail a stranger
        repeatedly, which costs deliverability rather than money."""
        user_id = await _user(async_session, "ceiling")
        for i in range(VERIFICATION_MAX_SENDS):
            await ev.start_verification(user_id, EMAIL, now=_later(minutes=10 * i))

        with pytest.raises(ev.TooManySends):
            await ev.start_verification(user_id, EMAIL, now=_later(minutes=999))

    async def test_an_already_verified_address_is_not_re_challenged(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "done")
        started = await ev.start_verification(user_id, EMAIL)
        await ev.confirm_verification(user_id, started.code)

        with pytest.raises(ev.AlreadyVerified):
            await ev.start_verification(user_id, EMAIL)

    async def test_a_new_code_clears_previous_failed_attempts(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "reset")
        await ev.start_verification(user_id, EMAIL)
        for _ in range(3):
            with pytest.raises(ev.CodeIncorrect):
                await ev.confirm_verification(user_id, "000000")

        await ev.start_verification(user_id, EMAIL, now=_later(minutes=5))
        assert (await db_client.get_email_challenge(user_id)).attempts == 0


class TestConfirming:
    async def test_the_right_code_verifies_the_address(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "confirm")
        started = await ev.start_verification(user_id, EMAIL)

        await ev.confirm_verification(user_id, started.code)
        user = await db_client.get_user_by_id(user_id)
        assert user.email_verified_at is not None

    async def test_verifying_destroys_the_challenge(
        self, db_session, async_session
    ):
        """A used code sitting in the database is a credential nobody needs."""
        user_id = await _user(async_session, "destroy")
        started = await ev.start_verification(user_id, EMAIL)
        await ev.confirm_verification(user_id, started.code)

        assert await db_client.get_email_challenge(user_id) is None

    async def test_a_wrong_code_does_not_verify(self, db_session, async_session):
        user_id = await _user(async_session, "wrong")
        started = await ev.start_verification(user_id, EMAIL)
        wrong = "000000" if started.code != "000000" else "111111"

        with pytest.raises(ev.CodeIncorrect):
            await ev.confirm_verification(user_id, wrong)
        user = await db_client.get_user_by_id(user_id)
        assert user.email_verified_at is None

    async def test_guessing_is_cut_off_at_the_ceiling(
        self, db_session, async_session
    ):
        user_id = await _user(async_session, "brute")
        started = await ev.start_verification(user_id, EMAIL)

        for _ in range(VERIFICATION_MAX_ATTEMPTS):
            with pytest.raises(ev.CodeIncorrect):
                await ev.confirm_verification(user_id, "000000")

        # The correct code is refused too. A ceiling an attacker's final guess
        # can walk through is not a ceiling.
        with pytest.raises(ev.TooManyAttempts):
            await ev.confirm_verification(user_id, started.code)

    async def test_an_expired_code_is_refused(self, db_session, async_session):
        user_id = await _user(async_session, "expired")
        started = await ev.start_verification(user_id, EMAIL)

        with pytest.raises(ev.CodeExpired):
            await ev.confirm_verification(
                user_id, started.code, now=_later(minutes=60)
            )

    async def test_no_challenge_looks_the_same_as_a_wrong_code(
        self, db_session, async_session
    ):
        """So the endpoint cannot be used to find out whether a verification is
        outstanding for an account."""
        user_id = await _user(async_session, "none")
        with pytest.raises(ev.CodeIncorrect):
            await ev.confirm_verification(user_id, "123456")

    async def test_one_users_code_does_not_verify_another(
        self, db_session, async_session
    ):
        first = await _user(async_session, "first", email="a@example.com")
        second = await _user(async_session, "second", email="b@example.com")

        started = await ev.start_verification(first, "a@example.com")
        with pytest.raises(ev.CodeIncorrect):
            await ev.confirm_verification(second, started.code)


class TestTheFlow:
    async def test_a_mail_failure_reports_false_and_does_not_raise(
        self, db_session, async_session, monkeypatch
    ):
        """Signup completes either way. A mail server having a bad minute must
        not cost somebody the account they just created, so the flow reports
        the failure and lets the caller carry on."""
        from api.services.auth import email_verification_flow as flow

        user_id = await _user(async_session, "mail-down")
        monkeypatch.setattr(flow, "email_is_configured", lambda: True)

        async def _failing(**kwargs):
            class _Failed:
                ok = False
                error = "smtp is down"

            return _Failed()

        monkeypatch.setattr(flow, "send_email", _failing)
        assert await flow.issue_code(user_id, EMAIL) is False

        # The code was still issued, so the user can verify with a resend
        # rather than being stuck with an account that can never be proved.
        assert await db_client.get_email_challenge(user_id) is not None

    async def test_unconfigured_smtp_reports_false_rather_than_failing(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.auth import email_verification_flow as flow

        user_id = await _user(async_session, "no-smtp")
        monkeypatch.setattr(flow, "email_is_configured", lambda: False)
        assert await flow.issue_code(user_id, EMAIL) is False

    async def test_a_rate_limited_resend_is_not_an_error(
        self, db_session, async_session, monkeypatch
    ):
        """Asking twice quickly is a normal thing for a person to do, and the
        caller has already succeeded at the thing that mattered."""
        from api.services.auth import email_verification_flow as flow

        user_id = await _user(async_session, "quick")
        monkeypatch.setattr(flow, "email_is_configured", lambda: True)

        sent = []

        async def _capture(**kwargs):
            sent.append(kwargs)

            class _Ok:
                ok = True
                error = None

            return _Ok()

        monkeypatch.setattr(flow, "send_email", _capture)

        assert await flow.issue_code(user_id, EMAIL) is True
        # Second one is inside the cooldown: refused, reported, not raised.
        assert await flow.issue_code(user_id, EMAIL) is False
        assert len(sent) == 1

    async def test_the_code_is_in_the_body_and_the_subject_is_not_a_code(
        self,
    ):
        code = "482913"
        assert code in ev.body(code)
        assert code not in ev.subject()

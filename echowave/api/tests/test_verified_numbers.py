"""Proving a number can be answered before we agree to dial it.

The limits are the point of this module, so they are what is tested hardest.
Each closes a different abuse and none substitutes for another:

* attempts per code — six digits falls to exhaustive guessing in under a second
* sends per number — otherwise the endpoint texts a stranger on our carrier bill
* a cooldown between sends — the same abuse, slower

Plus the property the whole feature exists for: a number nobody answered is
never treated as verified.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.constants import (
    VERIFICATION_MAX_ATTEMPTS,
    VERIFICATION_MAX_SENDS,
)
from api.db import db_client
from api.db.models import OrganizationModel
from api.services.telephony import verified_numbers as vn

NUMBER = "9876543210"
NORMALISED = "919876543210"


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


def _later(minutes: int = 0, seconds: int = 0) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes, seconds=seconds)


class TestCodeGeneration:
    def test_a_code_is_six_digits(self):
        for _ in range(50):
            code = vn.generate_code()
            assert len(code) == 6 and code.isdigit()

    def test_codes_are_not_all_the_same(self):
        """A weak sanity check, but it catches the mistake where a constant is
        returned and every verification silently succeeds."""
        assert len({vn.generate_code() for _ in range(50)}) > 10

    def test_the_same_code_hashes_differently_under_different_salts(self):
        """This is the whole reason for the salt. Without it a leaked table of
        six-digit digests is a rainbow-table lookup, because there are only a
        million possible codes."""
        assert vn.hash_code("123456", "aaaa") != vn.hash_code("123456", "bbbb")

    def test_hashing_is_stable_for_one_salt(self):
        assert vn.hash_code("123456", "aaaa") == vn.hash_code("123456", "aaaa")


class TestStarting:
    async def test_it_issues_a_code_and_stores_only_the_hash(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "issue")
        started = await vn.start_verification(org_id, NUMBER)

        assert started.phone_number == NORMALISED
        assert len(started.code) == 6

        record = await db_client.get_verified_number(org_id, NORMALISED)
        assert record.status == "pending"
        # The plaintext must not be anywhere in the row.
        assert started.code not in (record.code_hash or "")
        assert record.code_hash == vn.hash_code(started.code, record.code_salt)

    async def test_the_number_is_normalised_before_storage(
        self, db_session, async_session
    ):
        """Verified and suppressed have to be asked about the same string, or a
        number verified one way is unrecognised when dialled another."""
        org_id = await _org(async_session, "normalise")
        started = await vn.start_verification(org_id, "+91 98765 43210")
        assert started.phone_number == NORMALISED

    async def test_an_undialable_number_is_refused(self, db_session, async_session):
        org_id = await _org(async_session, "junk")
        with pytest.raises(vn.NumberNotDialable):
            await vn.start_verification(org_id, "12345")

    async def test_a_resend_inside_the_cooldown_is_refused(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "cooldown")
        await vn.start_verification(org_id, NUMBER)
        with pytest.raises(vn.ResendTooSoon):
            await vn.start_verification(org_id, NUMBER)

    async def test_a_resend_after_the_cooldown_is_allowed(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "cooldown-passed")
        await vn.start_verification(org_id, NUMBER)
        second = await vn.start_verification(org_id, NUMBER, now=_later(minutes=5))
        assert len(second.code) == 6

    async def test_the_send_ceiling_stops_us_texting_a_stranger_forever(
        self, db_session, async_session
    ):
        """Without this the endpoint is an SMS cannon pointed at any number,
        billed to us."""
        org_id = await _org(async_session, "sms-cannon")
        for i in range(VERIFICATION_MAX_SENDS):
            await vn.start_verification(org_id, NUMBER, now=_later(minutes=10 * i))

        with pytest.raises(vn.TooManySends):
            await vn.start_verification(org_id, NUMBER, now=_later(minutes=999))

    async def test_a_new_code_clears_the_previous_failed_attempts(
        self, db_session, async_session
    ):
        """The attempt ceiling belongs to the code, not the number. Inheriting
        it would make a fresh code unusable after five mistypes."""
        org_id = await _org(async_session, "attempts-reset")
        await vn.start_verification(org_id, NUMBER)
        for _ in range(3):
            with pytest.raises(vn.CodeIncorrect):
                await vn.confirm_verification(org_id, NUMBER, "000000")

        await vn.start_verification(org_id, NUMBER, now=_later(minutes=5))
        record = await db_client.get_verified_number(org_id, NORMALISED)
        assert record.attempts == 0


class TestConfirming:
    async def test_the_right_code_verifies_the_number(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "confirm")
        started = await vn.start_verification(org_id, NUMBER)

        assert not await vn.is_verified(org_id, NUMBER)
        await vn.confirm_verification(org_id, NUMBER, started.code)
        assert await vn.is_verified(org_id, NUMBER)

    async def test_verifying_destroys_the_code(self, db_session, async_session):
        """A verified number keeping a usable code is a credential nobody needs,
        and the safest place for it is nowhere."""
        org_id = await _org(async_session, "destroy")
        started = await vn.start_verification(org_id, NUMBER)
        await vn.confirm_verification(org_id, NUMBER, started.code)

        record = await db_client.get_verified_number(org_id, NORMALISED)
        assert record.code_hash is None
        assert record.code_salt is None

    async def test_a_wrong_code_does_not_verify(self, db_session, async_session):
        org_id = await _org(async_session, "wrong")
        started = await vn.start_verification(org_id, NUMBER)
        wrong = "000000" if started.code != "000000" else "111111"

        with pytest.raises(vn.CodeIncorrect):
            await vn.confirm_verification(org_id, NUMBER, wrong)
        assert not await vn.is_verified(org_id, NUMBER)

    async def test_guessing_is_cut_off_at_the_attempt_ceiling(
        self, db_session, async_session
    ):
        """Six digits is a million possibilities and falls in under a second
        without this."""
        org_id = await _org(async_session, "bruteforce")
        started = await vn.start_verification(org_id, NUMBER)

        for _ in range(VERIFICATION_MAX_ATTEMPTS):
            with pytest.raises(vn.CodeIncorrect):
                await vn.confirm_verification(org_id, NUMBER, "000000")

        # Even the CORRECT code is refused now. The ceiling would be worthless
        # if an attacker's final guess still counted.
        with pytest.raises(vn.TooManyAttempts):
            await vn.confirm_verification(org_id, NUMBER, started.code)
        assert not await vn.is_verified(org_id, NUMBER)

    async def test_an_expired_code_is_refused(self, db_session, async_session):
        org_id = await _org(async_session, "expired")
        started = await vn.start_verification(org_id, NUMBER)

        with pytest.raises(vn.CodeExpired):
            await vn.confirm_verification(
                org_id, NUMBER, started.code, now=_later(minutes=60)
            )
        assert not await vn.is_verified(org_id, NUMBER)

    async def test_an_unknown_number_looks_the_same_as_a_wrong_code(
        self, db_session, async_session
    ):
        """Distinguishing them would let someone enumerate which numbers an
        organization has tried to verify."""
        org_id = await _org(async_session, "enumerate")
        with pytest.raises(vn.CodeIncorrect):
            await vn.confirm_verification(org_id, "9999999999", "123456")

    async def test_one_organizations_verification_does_not_verify_it_for_another(
        self, db_session, async_session
    ):
        """Otherwise anyone could dial any number that any other customer had
        ever verified."""
        first = await _org(async_session, "verifier")
        second = await _org(async_session, "freeloader")

        started = await vn.start_verification(first, NUMBER)
        await vn.confirm_verification(first, NUMBER, started.code)

        assert await vn.is_verified(first, NUMBER)
        assert not await vn.is_verified(second, NUMBER)

    async def test_a_code_from_one_number_does_not_verify_another(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "cross-number")
        started = await vn.start_verification(org_id, NUMBER)

        with pytest.raises(vn.CodeIncorrect):
            await vn.confirm_verification(org_id, "9123456789", started.code)


class TestTheGate:
    async def test_an_unknown_number_is_not_verified(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "unknown")
        assert not await vn.is_verified(org_id, NUMBER)

    async def test_a_pending_number_is_not_verified(
        self, db_session, async_session
    ):
        """The property the whole feature exists for: asking for a code is not
        the same as answering one."""
        org_id = await _org(async_session, "pending")
        await vn.start_verification(org_id, NUMBER)
        assert not await vn.is_verified(org_id, NUMBER)

    async def test_junk_is_never_verified(self, db_session, async_session):
        org_id = await _org(async_session, "junk-gate")
        assert not await vn.is_verified(org_id, "not-a-number")

    async def test_removing_a_number_revokes_it(self, db_session, async_session):
        org_id = await _org(async_session, "revoke")
        started = await vn.start_verification(org_id, NUMBER)
        await vn.confirm_verification(org_id, NUMBER, started.code)

        assert await db_client.remove_verified_number(org_id, NORMALISED) is True
        assert not await vn.is_verified(org_id, NUMBER)


class TestTheSender:
    """The transport is the part blocked on DLT registration, so what matters
    here is that an unconfigured deployment fails loudly rather than pretending
    to have sent something."""

    async def test_the_log_channel_refuses_to_run_in_production(self, monkeypatch):
        """Falling back to the log channel in production would tell every user
        'code sent' while sending nothing — a broken feature AND a credential
        written somewhere it does not belong."""
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "ENVIRONMENT", "production")
        result = await sender._send_log("919876543210", "123456")
        assert not result.ok
        assert "not configured" in (result.error or "")

    async def test_the_log_channel_works_in_test(self, monkeypatch):
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "ENVIRONMENT", "test")
        result = await sender._send_log("919876543210", "123456")
        assert result.ok and result.channel == "log"

    async def test_sms_without_platform_credentials_is_refused(self, monkeypatch):
        """And refused with a sentence that does not leak which of the three
        settings is missing."""
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "PLATFORM_PLIVO_AUTH_ID", None)
        monkeypatch.setattr(sender, "PLATFORM_PLIVO_AUTH_TOKEN", None)
        result = await sender._send_plivo_sms("919876543210", "123456")
        assert not result.ok

    async def test_sms_without_a_sender_number_is_refused(self, monkeypatch):
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "PLATFORM_PLIVO_AUTH_ID", "id")
        monkeypatch.setattr(sender, "PLATFORM_PLIVO_AUTH_TOKEN", "token")
        monkeypatch.setattr(sender, "PLATFORM_SMS_FROM_NUMBER", None)
        result = await sender._send_plivo_sms("919876543210", "123456")
        assert not result.ok

    async def test_voice_says_it_is_not_wired_rather_than_failing_silently(self):
        """STATUS.md records that outbound on the platform account has never
        completed a real call here. Shipping this as though it worked would
        produce a flow that silently never rings."""
        from api.services.telephony import verification_sender as sender

        result = await sender._send_voice("919876543210", "123456")
        assert not result.ok
        assert "not enabled" in (result.error or "")

    async def test_an_unknown_channel_is_refused_rather_than_guessed(
        self, monkeypatch
    ):
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "VERIFICATION_CHANNEL", "carrier-pigeon")
        result = await sender.deliver_code("919876543210", "123456")
        assert not result.ok

    def test_the_message_body_is_stable(self):
        """Once SMS is live the operator matches on the registered DLT template.
        A body that differs by a full stop is rejected as unregistered, so this
        text changing is a deploy-time incident, not a copy tweak."""
        from api.services.telephony.verification_sender import _body

        assert _body("123456") == (
            "Your Decibyl verification code is 123456. It expires in 10 minutes."
        )

    async def test_twilio_without_platform_credentials_is_refused(self, monkeypatch):
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "PLATFORM_TWILIO_ACCOUNT_SID", None)
        monkeypatch.setattr(sender, "PLATFORM_TWILIO_AUTH_TOKEN", None)
        result = await sender._send_twilio_sms("919876543210", "123456")
        assert not result.ok

    async def test_twilio_is_reachable_through_the_channel_switch(self, monkeypatch):
        """Switching carrier must be a config value, not a code change. It is
        NOT a way around DLT — that attaches to the sending entity and the
        Indian destination, so Twilio drops unregistered traffic as Plivo does."""
        from api.services.telephony import verification_sender as sender

        monkeypatch.setattr(sender, "VERIFICATION_CHANNEL", "twilio_sms")
        monkeypatch.setattr(sender, "PLATFORM_TWILIO_ACCOUNT_SID", None)
        result = await sender.deliver_code("919876543210", "123456")
        assert result.channel == "twilio_sms"


class TestTheGateDefault:
    def test_the_gate_is_off_until_a_code_can_actually_be_delivered(self):
        """A permission nobody can obtain is an outage, not a permission.

        VERIFICATION_CHANNEL is `log` on every real deployment today, and log
        refuses to run outside dev — so enforcing this before delivery works
        would refuse every test call with no way for the user to proceed.

        This assertion is here to be DELETED, in the same change that makes
        delivery work and flips the default. It failing means somebody turned
        the gate on; check that a code can actually reach a user first.
        """
        from api.constants import REQUIRE_VERIFIED_TEST_NUMBER

        assert REQUIRE_VERIFIED_TEST_NUMBER is False

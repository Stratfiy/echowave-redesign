"""Second-factor authentication.

MFA that mostly works is worse than none, because it is sold as protection. The
tests that matter are the ones about the failure modes a naive TOTP
implementation ships with: a code that can be used twice, a secret readable in
the database, a recovery code that never gets consumed, and a stolen session
being enough to switch the whole thing off.
"""

import time
from unittest.mock import patch

import pyotp
import pytest
from cryptography.fernet import Fernet

from api.services.auth import mfa

# Generated per run, never a real key — see the note in test_backup.py.
SECRET_KEY = Fernet.generate_key().decode()


def _code_now(secret: str, offset_steps: int = 0) -> str:
    counter = int(time.time()) // mfa.STEP_SECONDS + offset_steps
    return pyotp.TOTP(secret, interval=mfa.STEP_SECONDS).at(counter * mfa.STEP_SECONDS)


class TestVerifyingACode:
    def test_a_current_code_is_accepted(self):
        secret = pyotp.random_base32()

        assert mfa.verify_code(secret=secret, code=_code_now(secret)) is not None

    def test_a_wrong_code_is_rejected(self):
        secret = pyotp.random_base32()

        assert mfa.verify_code(secret=secret, code="000000") is None

    @pytest.mark.parametrize("junk", ["", "abcdef", "12 34 56 78", None])
    def test_nonsense_is_rejected_without_raising(self, junk):
        """These arrive from the network. A crash here is a 500 on the login
        endpoint, which is a denial of service with extra steps."""
        assert mfa.verify_code(secret=pyotp.random_base32(), code=junk) is None

    @pytest.mark.parametrize("drift", [-1, 1])
    def test_one_step_of_clock_drift_is_tolerated(self, drift):
        """Phone clocks are wrong often enough that zero tolerance produces
        support tickets rather than security."""
        secret = pyotp.random_base32()

        assert mfa.verify_code(secret=secret, code=_code_now(secret, drift)) is not None

    @pytest.mark.parametrize("drift", [-3, 3])
    def test_a_stale_code_is_rejected(self, drift):
        secret = pyotp.random_base32()

        assert mfa.verify_code(secret=secret, code=_code_now(secret, drift)) is None


class TestReplay:
    """The failure a naive implementation ships with. A TOTP code stays valid
    for its whole step, so one captured at a proxied login works again."""

    def test_the_same_code_cannot_be_used_twice(self):
        secret = pyotp.random_base32()
        code = _code_now(secret)

        first = mfa.verify_code(secret=secret, code=code)
        assert first is not None

        assert mfa.verify_code(secret=secret, code=code, last_counter=first) is None

    def test_an_older_counter_cannot_be_replayed_after_a_newer_one(self):
        secret = pyotp.random_base32()
        current = mfa.verify_code(secret=secret, code=_code_now(secret))

        assert (
            mfa.verify_code(
                secret=secret, code=_code_now(secret, -1), last_counter=current
            )
            is None
        )

    def test_the_next_step_is_still_accepted(self):
        """Replay protection must not lock the account out for the rest of the
        day — only the counters already used are refused."""
        secret = pyotp.random_base32()
        previous = int(time.time()) // mfa.STEP_SECONDS - 1

        assert (
            mfa.verify_code(
                secret=secret, code=_code_now(secret), last_counter=previous
            )
            is not None
        )


class TestSecretStorage:
    def test_the_secret_is_not_stored_in_the_clear(self):
        """A readable TOTP secret is password-equivalent: anyone with the table
        can mint valid codes indefinitely."""
        with patch.object(mfa, "PLATFORM_CREDENTIAL_SECRET", SECRET_KEY):
            stored = mfa.encrypt_secret("JBSWY3DPEHPK3PXP")

        assert "JBSWY3DPEHPK3PXP" not in stored

    def test_it_round_trips(self):
        with patch.object(mfa, "PLATFORM_CREDENTIAL_SECRET", SECRET_KEY):
            assert mfa.decrypt_secret(mfa.encrypt_secret("JBSWY3DPEHPK3PXP")) == (
                "JBSWY3DPEHPK3PXP"
            )

    def test_it_refuses_to_store_without_an_encryption_key(self):
        """Falling back to plaintext would silently downgrade every enrolment
        from here on, with nothing in the UI to show it."""
        with (
            patch.object(mfa, "PLATFORM_CREDENTIAL_SECRET", ""),
            pytest.raises(RuntimeError, match="PLATFORM_CREDENTIAL_SECRET"),
        ):
            mfa.encrypt_secret("JBSWY3DPEHPK3PXP")


class TestRecoveryCodes:
    def test_they_are_hashed_not_stored(self):
        """Ten spare passwords in readable form would be a worse liability than
        the password they back up."""
        codes = mfa.generate_recovery_codes()
        hashes = [mfa.hash_recovery_code(code) for code in codes]

        for code in codes:
            assert code not in hashes

    def test_a_valid_code_is_matched(self):
        codes = mfa.generate_recovery_codes()
        hashes = [mfa.hash_recovery_code(c) for c in codes]

        assert mfa.verify_recovery_code(codes[3], hashes) == hashes[3]

    def test_an_invalid_code_matches_nothing(self):
        hashes = [mfa.hash_recovery_code(c) for c in mfa.generate_recovery_codes()]

        assert mfa.verify_recovery_code("not-a-real-code", hashes) is None

    def test_formatting_does_not_defeat_a_code(self):
        """People retype these off paper, with dashes and capitals."""
        code = mfa.generate_recovery_codes()[0]
        hashes = [mfa.hash_recovery_code(code)]

        assert mfa.verify_recovery_code(f"  {code.upper()}  ", hashes) == hashes[0]

    def test_each_code_is_distinct(self):
        codes = mfa.generate_recovery_codes()

        assert len(set(codes)) == len(codes) == mfa.RECOVERY_CODE_COUNT


class TestEnrollment:
    def test_the_uri_is_scannable_and_names_the_issuer(self):
        enrollment = mfa.begin_enrollment(email="ops@decibyl.ai")

        assert enrollment.uri.startswith("otpauth://totp/")
        assert "Decibyl" in enrollment.uri
        assert "ops@decibyl.ai" in enrollment.uri.replace("%40", "@")

    def test_the_issued_secret_generates_accepted_codes(self):
        """Otherwise enrolment succeeds and the first real login fails."""
        enrollment = mfa.begin_enrollment(email="ops@decibyl.ai")

        assert (
            mfa.verify_code(secret=enrollment.secret, code=_code_now(enrollment.secret))
            is not None
        )

    def test_every_enrolment_gets_a_fresh_secret(self):
        first = mfa.begin_enrollment(email="a@decibyl.ai")
        second = mfa.begin_enrollment(email="a@decibyl.ai")

        assert first.secret != second.secret
        assert set(first.recovery_codes) & set(second.recovery_codes) == set()

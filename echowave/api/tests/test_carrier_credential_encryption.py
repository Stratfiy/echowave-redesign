"""Carrier credentials at rest.

Every other secret on the platform is Fernet-encrypted before storage. These
were not: `telephony_configurations.credentials` is plain JSONB, and one
`SELECT` against a replica or a backup yielded every tenant's carrier token —
enough to place calls billed to that customer, release their numbers, or
repoint their inbound answer_url.

The tests here are mostly about the *boundary*, because the obvious
implementation breaks inbound calling for everyone: the account id is matched
in SQL by `find_inbound_route_by_account`, so it has to stay readable while the
token beside it does not.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api.services.telephony import credential_encryption

TEST_SECRET = Fernet.generate_key().decode()
AUTH_ID = "MAXXXXXXXXXXXXXXXXXX"
AUTH_TOKEN = "the-carrier-token-nobody-should-read"


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(
        credential_encryption, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET
    )


class TestWhatIsEncrypted:
    def test_the_auth_token_does_not_survive_as_plaintext(self):
        stored = credential_encryption.encrypt(
            "plivo", {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}
        )

        assert AUTH_TOKEN not in str(stored)
        assert stored["auth_token"].startswith(credential_encryption.PREFIX)

    def test_the_account_id_stays_readable(self):
        """The one field that must not be encrypted.

        Inbound dispatch resolves which organization a call belongs to with
        `credentials ->> 'auth_id' = :account_id` in SQL. Encrypt it and every
        inbound call on the platform stops matching at once — a far worse
        outcome than the exposure, and the failure looks like "the number is
        not configured".
        """
        stored = credential_encryption.encrypt(
            "plivo", {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}
        )

        assert stored["auth_id"] == AUTH_ID

    def test_a_round_trip_returns_what_went_in(self):
        original = {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}

        stored = credential_encryption.encrypt("plivo", original)
        assert credential_encryption.decrypt("plivo", stored) == original

    def test_encrypting_twice_does_not_double_wrap(self):
        once = credential_encryption.encrypt(
            "plivo", {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}
        )
        twice = credential_encryption.encrypt("plivo", once)

        assert twice == once
        assert credential_encryption.decrypt("plivo", twice)["auth_token"] == AUTH_TOKEN


class TestRollout:
    def test_a_plaintext_row_written_before_this_still_reads(self):
        """What makes the migration optional rather than urgent: a row that has
        never been through `encrypt` is returned as it is."""
        legacy = {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}

        assert credential_encryption.decrypt("plivo", legacy) == legacy

    def test_no_secret_configured_stores_plaintext_rather_than_failing(
        self, monkeypatch
    ):
        """A deployment that has not set PLATFORM_CREDENTIAL_SECRET must keep
        working exactly as it did. Refusing to save a carrier configuration
        would turn a missing env var into an outage."""
        monkeypatch.setattr(credential_encryption, "PLATFORM_CREDENTIAL_SECRET", "")
        original = {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}

        assert credential_encryption.encrypt("plivo", original) == original

    def test_a_rotated_secret_leaves_the_value_alone_rather_than_raising(
        self, monkeypatch
    ):
        """One provider failing to authenticate is recoverable. An exception on
        every telephony read path is not."""
        stored = credential_encryption.encrypt(
            "plivo", {"auth_id": AUTH_ID, "auth_token": AUTH_TOKEN}
        )
        monkeypatch.setattr(
            credential_encryption,
            "PLATFORM_CREDENTIAL_SECRET",
            Fernet.generate_key().decode(),
        )

        recovered = credential_encryption.decrypt("plivo", stored)

        assert recovered["auth_id"] == AUTH_ID
        assert recovered["auth_token"].startswith(credential_encryption.PREFIX)

    def test_an_unknown_provider_encrypts_nothing(self):
        """Encrypt nothing rather than guess: encrypting a field something else
        matches on is how routing breaks silently."""
        original = {"whatever": "value"}

        assert credential_encryption.encrypt("not-a-provider", original) == original

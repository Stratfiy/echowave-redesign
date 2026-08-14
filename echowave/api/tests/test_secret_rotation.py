"""Rotating the one key that opens everything.

``PLATFORM_CREDENTIAL_SECRET`` does not only protect our provider keys. It also
covers every customer's own BYOK key, every TOTP secret, every Google Calendar
grant, and the database backups. Changing it used to be a one-line env edit
that broke all five at once — and broke them silently, because each decrypt
path degrades rather than raises. A managed call falls through to "no key" and
fails at the vendor; an MFA prompt rejects a correct code; a calendar sync just
stops.

So it was never rotated. That is the actual bug: a key that cannot be rotated
is a key that never is, and a leaked one then has no remedy short of
re-entering every credential on the platform by hand.

Two keys at once is the fix, and the tests below hold the four properties that
make it safe rather than merely possible.

**Old ciphertext stays readable** while the previous key is configured — that
is what removes the broken window.

**Nothing is ever written under the old key.** A rotation where writes kept
landing on the key being retired would never finish.

**Re-runnable.** An interrupted run is finished by running it again, which is
the only realistic operational story for a job over live data.

**Never destructive.** A row that decrypts under neither key is counted and
left exactly as it is. It is already lost; overwriting it destroys the only
evidence of what was there.
"""

import pytest
from cryptography.fernet import Fernet

from api.db.models import (
    OrganizationModel,
    OrganizationProviderCredentialModel,
    PlatformProviderCredentialModel,
)
from api.services import secret_rotation

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
KEY_C = Fernet.generate_key().decode()


def _use(monkeypatch, current, previous=None):
    """Point the shared cipher at a given pair of keys."""
    monkeypatch.setattr(secret_rotation, "PLATFORM_CREDENTIAL_SECRET", current)
    monkeypatch.setattr(
        secret_rotation, "PLATFORM_CREDENTIAL_SECRET_PREVIOUS", previous
    )


async def _platform_key(async_session, *, provider, token):
    row = PlatformProviderCredentialModel(
        component="llm",
        provider=provider,
        encrypted_key=token,
        key_last_four="abcd",
    )
    async_session.add(row)
    await async_session.flush()
    return row


class TestTheCipher:
    def test_it_refuses_when_no_secret_is_set(self, monkeypatch):
        """A fallback here would mean a deployment that forgot to configure it
        still appeared to work, with every key encrypted under a value
        published in this repository."""
        _use(monkeypatch, None)

        with pytest.raises(secret_rotation.SecretError):
            secret_rotation.cipher()

    def test_a_malformed_secret_says_what_is_wrong(self, monkeypatch):
        """ "Invalid token" on a call at 2am is not a diagnosis."""
        _use(monkeypatch, "not-a-fernet-key")

        with pytest.raises(secret_rotation.SecretError) as exc:
            secret_rotation.cipher()
        assert "base64" in str(exc.value)

    def test_it_writes_under_the_current_key_only(self, monkeypatch):
        """The property that lets a rotation finish. If writes kept landing on
        the key being retired, the count of outstanding rows would never
        reach zero."""
        _use(monkeypatch, KEY_B, KEY_A)
        token = secret_rotation.cipher().encrypt(b"hello")

        # The new key alone can read it; the old one cannot.
        assert Fernet(KEY_B.encode()).decrypt(token) == b"hello"
        with pytest.raises(Exception):
            Fernet(KEY_A.encode()).decrypt(token)

    def test_it_reads_under_either_key(self, monkeypatch):
        """The property that removes the broken window: everything written
        before the rotation is still readable during it."""
        old_token = Fernet(KEY_A.encode()).encrypt(b"secret")
        _use(monkeypatch, KEY_B, KEY_A)

        assert secret_rotation.cipher().decrypt(old_token) == b"secret"

    def test_a_key_from_neither_generation_is_refused(self, monkeypatch):
        """Not silently — this is the case where data really is lost, and
        reporting it as readable would hide that until an outage."""
        stray = Fernet(KEY_C.encode()).encrypt(b"secret")
        _use(monkeypatch, KEY_B, KEY_A)

        with pytest.raises(Exception):
            secret_rotation.cipher().decrypt(stray)


class TestKnowingWhereYouAre:
    def test_a_rotation_is_in_progress_while_a_previous_key_is_set(self, monkeypatch):
        _use(monkeypatch, KEY_B, KEY_A)
        assert secret_rotation.rotation_in_progress()

    def test_it_is_not_in_progress_with_one_key(self, monkeypatch):
        _use(monkeypatch, KEY_B, None)
        assert not secret_rotation.rotation_in_progress()


@pytest.mark.asyncio
class TestCountingWhatIsLeft:
    async def test_rows_on_the_old_key_are_counted_as_pending(
        self, db_session, async_session, monkeypatch
    ):
        """The number that has to reach zero. Dropping the previous key while
        it is non-zero strands every one of these."""
        _use(monkeypatch, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-old",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-old").decode(),
        )

        _use(monkeypatch, KEY_B, KEY_A)
        state = await secret_rotation.status(async_session)

        assert state.pending >= 1
        assert not state.safe_to_drop_previous

    async def test_rows_on_the_current_key_are_not_pending(
        self, db_session, async_session, monkeypatch
    ):
        _use(monkeypatch, KEY_B, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-new",
            token=secret_rotation.cipher().encrypt(b"sk-new").decode(),
        )

        state = await secret_rotation.status(async_session)

        assert state.current >= 1

    async def test_a_row_under_a_forgotten_key_reads_as_unreadable(
        self, db_session, async_session, monkeypatch
    ):
        """Distinct from pending on purpose. Pending is work outstanding;
        unreadable is data already lost, and conflating them would let a
        rotation report progress it is not making."""
        _use(monkeypatch, KEY_B, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-lost",
            token=Fernet(KEY_C.encode()).encrypt(b"sk-lost").decode(),
        )

        state = await secret_rotation.status(async_session)

        assert state.unreadable >= 1

    async def test_status_never_returns_plaintext(
        self, db_session, async_session, monkeypatch
    ):
        """It is called from a staff endpoint. Counts are safe to return;
        anything it decrypted must not leave the process."""
        _use(monkeypatch, KEY_B, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-quiet",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-quiet").decode(),
        )

        payload = (await secret_rotation.status(async_session)).as_dict()

        assert "sk-quiet" not in str(payload)
        assert set(payload) == {
            "current",
            "pending",
            "unreadable",
            "previous_key_configured",
            "safe_to_drop_previous",
        }


@pytest.mark.asyncio
class TestReencrypting:
    async def test_it_rewrites_an_old_row_under_the_new_key(
        self, db_session, async_session, monkeypatch
    ):
        _use(monkeypatch, KEY_B, KEY_A)
        row = await _platform_key(
            async_session,
            provider="rot-rewrite",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-rewrite").decode(),
        )

        await secret_rotation.reencrypt_all(async_session)
        await async_session.flush()

        # Readable under the new key alone, which is the point.
        assert (
            Fernet(KEY_B.encode()).decrypt(row.encrypted_key.encode()) == b"sk-rewrite"
        )

    async def test_the_plaintext_is_unchanged(
        self, db_session, async_session, monkeypatch
    ):
        """The obvious thing, and the one worth asserting: a rotation that
        changed a key's value would take down every managed call on that
        provider, and nothing else in the system would notice."""
        _use(monkeypatch, KEY_B, KEY_A)
        row = await _platform_key(
            async_session,
            provider="rot-same",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-live-value").decode(),
        )

        await secret_rotation.reencrypt_all(async_session)
        await async_session.flush()

        assert (
            secret_rotation.cipher().decrypt(row.encrypted_key.encode())
            == b"sk-live-value"
        )

    async def test_running_it_twice_changes_nothing_the_second_time(
        self, db_session, async_session, monkeypatch
    ):
        """Re-runnable is the whole operational story: an interrupted run is
        finished by running it again."""
        _use(monkeypatch, KEY_B, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-twice",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-twice").decode(),
        )

        first = await secret_rotation.reencrypt_all(async_session)
        await async_session.flush()
        second = await secret_rotation.reencrypt_all(async_session)

        assert first.rewritten >= 1
        assert second.rewritten == 0

    async def test_a_dry_run_writes_nothing(
        self, db_session, async_session, monkeypatch
    ):
        """So an operator can see the size of the job before starting it on
        live data."""
        _use(monkeypatch, KEY_B, KEY_A)
        row = await _platform_key(
            async_session,
            provider="rot-dry",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-dry").decode(),
        )
        before = row.encrypted_key

        result = await secret_rotation.reencrypt_all(async_session, dry_run=True)

        assert result.rewritten >= 1
        assert row.encrypted_key == before

    async def test_an_unreadable_row_is_left_exactly_as_it_was(
        self, db_session, async_session, monkeypatch
    ):
        """It is already lost. Overwriting it — with anything, including an
        empty string — destroys the only evidence of what was there, which a
        support case may still need."""
        _use(monkeypatch, KEY_B, KEY_A)
        row = await _platform_key(
            async_session,
            provider="rot-untouched",
            token=Fernet(KEY_C.encode()).encrypt(b"sk-gone").decode(),
        )
        before = row.encrypted_key

        result = await secret_rotation.reencrypt_all(async_session)

        assert result.unreadable >= 1
        assert row.encrypted_key == before

    async def test_it_covers_the_customer_vault_too(
        self, db_session, async_session, monkeypatch
    ):
        """The failure this catches is a rotation that fixes our own keys and
        strands every customer's — who would find out at their next call, on a
        deployment that reported the rotation complete."""
        _use(monkeypatch, KEY_B, KEY_A)
        org = OrganizationModel(provider_id="org-rot", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        row = OrganizationProviderCredentialModel(
            organization_id=org.id,
            component="stt",
            provider="deepgram",
            encrypted_key=Fernet(KEY_A.encode()).encrypt(b"cust-key").decode(),
            key_last_four="key",
        )
        async_session.add(row)
        await async_session.flush()

        result = await secret_rotation.reencrypt_all(async_session)
        await async_session.flush()

        assert "customer provider keys" in result.by_kind
        assert Fernet(KEY_B.encode()).decrypt(row.encrypted_key.encode()) == b"cust-key"

    async def test_afterwards_nothing_is_pending(
        self, db_session, async_session, monkeypatch
    ):
        """The signal an operator acts on: pending at zero is when dropping the
        previous key becomes safe, and dropping it is what actually retires the
        old secret."""
        _use(monkeypatch, KEY_B, KEY_A)
        await _platform_key(
            async_session,
            provider="rot-done",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-done").decode(),
        )

        await secret_rotation.reencrypt_all(async_session)
        await async_session.flush()
        state = await secret_rotation.status(async_session)

        assert state.pending == 0
        assert state.safe_to_drop_previous


@pytest.mark.asyncio
class TestTheSubsystemsActuallyUseIt:
    """The wiring, not the unit.

    Each of these fails if a subsystem still builds its own single-key cipher —
    which is exactly the bug the rotation exists to prevent, and the one a unit
    test of ``secret_rotation`` alone cannot see.
    """

    async def test_the_platform_vault_reads_a_key_written_before_the_rotation(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.configuration import platform_credentials

        _use(monkeypatch, KEY_A)
        await _platform_key(
            async_session,
            provider="wired-platform",
            token=Fernet(KEY_A.encode()).encrypt(b"sk-platform").decode(),
        )

        _use(monkeypatch, KEY_B, KEY_A)
        resolved = await platform_credentials.resolve_api_key(
            async_session, component="llm", provider="wired-platform"
        )

        assert resolved == "sk-platform"

    async def test_the_customer_vault_reads_a_key_written_before_the_rotation(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.configuration import organization_credentials

        _use(monkeypatch, KEY_A)
        org = OrganizationModel(provider_id="org-rot-wired", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        async_session.add(
            OrganizationProviderCredentialModel(
                organization_id=org.id,
                component="stt",
                provider="deepgram",
                encrypted_key=Fernet(KEY_A.encode()).encrypt(b"cust-live").decode(),
                key_last_four="live",
            )
        )
        await async_session.flush()

        _use(monkeypatch, KEY_B, KEY_A)
        resolved = await organization_credentials.resolve_api_key(
            async_session,
            organization_id=org.id,
            component="stt",
            provider="deepgram",
        )

        assert resolved == "cust-live"

    async def test_an_mfa_secret_survives_the_rotation(self, monkeypatch):
        """Left out of the rotation, changing the secret locks every person
        with MFA enabled out of their own account — and out of the recovery
        flow, since it is the same secret behind both."""
        from api.services.auth import mfa

        _use(monkeypatch, KEY_A)
        stored = mfa.encrypt_secret("JBSWY3DPEHPK3PXP")

        _use(monkeypatch, KEY_B, KEY_A)
        assert mfa.decrypt_secret(stored) == "JBSWY3DPEHPK3PXP"

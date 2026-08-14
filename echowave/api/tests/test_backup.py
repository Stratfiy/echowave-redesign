"""Taking, verifying and pruning a database backup.

The failure this guards against is not "the backup crashed" — that is loud and
someone fixes it. It is a backup that reports success while being unusable: an
upload that truncated, a dump written in plaintext, an object nobody can decrypt
because the secret was missing. Every one of those looks exactly like a working
backup until the day it is needed.
"""

import asyncio
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from api.services.backup import database


class FakeStorage:
    """An object store that can be told to misbehave in specific ways."""

    def __init__(self, *, truncate: bool = False):
        self.objects: dict[str, bytes] = {}
        self._truncate = truncate

    async def acreate_file_from_bytes(self, key: str, data: bytes) -> bool:
        self.objects[key] = data[:-1] if self._truncate else data
        return True

    async def aget_file_metadata(self, key: str):
        if key not in self.objects:
            return None
        return {"size": len(self.objects[key])}

    async def alist_files(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]

    async def adelete_file(self, key: str) -> bool:
        self.objects.pop(key, None)
        return True

    async def adownload_file(self, key: str, local_path: str) -> bool:
        if key not in self.objects:
            return False
        pathlib.Path(local_path).write_bytes(self.objects[key])
        return True


# Generated per run, never a real key. A fixture copied from a deployment's
# .env ends up in git history, where it is readable by anyone with repo access
# and outlives the rotation that was supposed to retire it.
SECRET = Fernet.generate_key().decode()


def _dump_writes(payload: bytes):
    """Stand in for pg_dump by writing the file it would have written."""

    async def _fake(destination: str) -> None:
        await asyncio.to_thread(pathlib.Path(destination).write_bytes, payload)

    return _fake


@pytest.mark.asyncio
class TestTakingABackup:
    async def test_the_dump_is_encrypted_before_it_is_uploaded(self):
        """A dump is every phone number, recording URL and invoice in one file.
        If the plaintext ever reaches the bucket, the bucket becomes the most
        sensitive thing in the system."""
        storage = FakeStorage()
        secret_row = b"PGDMP ... +919876543210 ... credit_ledger ..."

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(secret_row)),
        ):
            result = await database.run_backup()

        stored = storage.objects[result.key]
        assert b"+919876543210" not in stored
        assert b"PGDMP" not in stored

    async def test_the_upload_round_trips(self):
        storage = FakeStorage()
        payload = b"PGDMP-the-actual-dump"

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(payload)),
        ):
            result = await database.run_backup()

        assert Fernet(SECRET.encode()).decrypt(storage.objects[result.key]) == payload

    async def test_a_truncated_upload_is_caught(self):
        """The failure that looks most like success. Every layer reports OK and
        the object is one byte short of restorable."""
        storage = FakeStorage(truncate=True)

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-content")),
            pytest.raises(RuntimeError, match="verification failed"),
        ):
            await database.run_backup()

    async def test_it_refuses_to_write_without_an_encryption_secret(self):
        """Falling back to plaintext here would be a silent downgrade from
        'encrypted backup' to 'the whole database, readable, in a bucket'."""
        storage = FakeStorage()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", ""),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP")),
            pytest.raises(RuntimeError, match="PLATFORM_CREDENTIAL_SECRET"),
        ):
            await database.run_backup()

        assert storage.objects == {}

    async def test_an_empty_dump_is_refused(self):
        """pg_dump exiting 0 having written nothing would otherwise upload a
        perfectly valid encryption of zero bytes."""
        storage = FakeStorage()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"")),
            pytest.raises(RuntimeError, match="empty"),
        ):
            await database.run_backup()

        assert storage.objects == {}


class TestPgDumpMustExist:
    """The bug this guards against shipped once and was invisible: the package
    was added to a Dockerfile build stage that the runtime image throws away, so
    everything built, ran and reported healthy while backups could not run."""

    def test_a_missing_binary_names_the_actual_cause(self):
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="runner.*stage|not installed"),
        ):
            database._require_pg_dump()

    def test_it_returns_the_path_when_present(self):
        with patch("shutil.which", return_value="/usr/bin/pg_dump"):
            assert database._require_pg_dump() == "/usr/bin/pg_dump"


class TestTheDatabaseUrl:
    def test_the_sqlalchemy_driver_is_stripped(self):
        """pg_dump does not understand postgresql+asyncpg:// and fails with a
        message about an unrecognised host, which reads like a network problem
        rather than a URL problem."""
        with patch.object(
            database, "DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db"
        ):
            assert database._libpq_url() == "postgresql://u:p@host:5432/db"

    def test_a_plain_url_is_untouched(self):
        with patch.object(database, "DATABASE_URL", "postgresql://u:p@host:5432/db"):
            assert database._libpq_url() == "postgresql://u:p@host:5432/db"


@pytest.mark.asyncio
class TestPruning:
    @staticmethod
    def _storage_with_ages(days: list[int], now: datetime) -> FakeStorage:
        storage = FakeStorage()
        for offset in days:
            at = now - timedelta(days=offset)
            storage.objects[database._dump_key(at)] = b"x"
        return storage

    async def test_old_backups_go_and_recent_ones_stay(self):
        """Backups hold every phone number in the system, so they age under the
        same obligation as the data inside them."""
        now = datetime(2026, 7, 31, tzinfo=UTC)
        storage = self._storage_with_ages([1, 10, 45, 200], now)

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "BACKUP_RETENTION_DAYS", 30),
        ):
            removed = await database.prune(now=now)

        assert removed == 2
        assert len(storage.objects) == 2

    async def test_an_undeletable_object_does_not_stop_the_sweep(self):
        now = datetime(2026, 7, 31, tzinfo=UTC)
        storage = self._storage_with_ages([100, 200], now)
        storage.adelete_file = AsyncMock(side_effect=[False, True])

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "BACKUP_RETENTION_DAYS", 30),
        ):
            removed = await database.prune(now=now)

        assert removed == 1
        assert storage.adelete_file.await_count == 2

    async def test_unrelated_objects_are_never_touched(self):
        """The prefix listing is the only thing standing between this sweep and
        the recordings bucket."""
        now = datetime(2026, 7, 31, tzinfo=UTC)
        storage = self._storage_with_ages([200], now)
        storage.objects["recordings/2020/some-call.wav"] = b"audio"

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "BACKUP_RETENTION_DAYS", 30),
        ):
            await database.prune(now=now)

        assert "recordings/2020/some-call.wav" in storage.objects


@pytest.mark.asyncio
class TestEvidenceThatBackupsAreHappening:
    """The readiness check reads this. The presence of the backup code proves
    nothing — only the age of the newest object does."""

    async def test_no_backups_is_reported_not_raised(self):
        with patch.object(database, "get_storage", return_value=FakeStorage()):
            result = await database.last_successful()

        assert result["available"] is False
        assert result["count"] == 0

    async def test_the_newest_backup_and_its_age_are_reported(self):
        now = datetime(2026, 7, 31, 12, tzinfo=UTC)
        storage = FakeStorage()
        for offset in (1, 3):
            storage.objects[database._dump_key(now - timedelta(days=offset))] = b"x"

        with patch.object(database, "get_storage", return_value=storage):
            result = await database.last_successful(now=now)

        assert result["available"] is True
        assert result["count"] == 2
        assert result["age_hours"] == 24.0

    async def test_a_broken_object_store_does_not_raise(self):
        """A readiness probe that throws takes down the screen that would have
        told you why."""
        storage = FakeStorage()
        storage.alist_files = AsyncMock(side_effect=RuntimeError("bucket gone"))

        with patch.object(database, "get_storage", return_value=storage):
            result = await database.last_successful()

        assert result["available"] is False
        assert "bucket gone" in result["error"]


class TestRestore:
    def test_the_command_restores_into_an_empty_database(self):
        """An untested backup is a hypothesis. The command is printed so the
        rehearsal is cheap — and it must never point at a live database."""
        command = database.restore_command("backups/postgres/2026/07/31/x.dump.enc")

        assert "pg_restore" in command
        assert "PLATFORM_CREDENTIAL_SECRET" in command
        assert "EMPTY" in command


@pytest.mark.asyncio
class TestTheRotationThatStrandsEveryBackup:
    """A dump is restorable only while the secret it was written under is still
    configured.

    Rotate ``PLATFORM_CREDENTIAL_SECRET`` and every earlier backup becomes an
    unreadable file. It still lists. Its size is still right. Every "is there a
    recent backup" check ever written still passes. The discovery happens
    during a restore, at the one moment there is nothing to fall back to — so
    the canary exists to make the discovery happen on an ordinary Tuesday
    instead.
    """

    async def test_a_canary_is_written_beside_every_backup(self):
        storage = FakeStorage()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-x")),
        ):
            result = await database.run_backup()

        assert database._canary_key(result.key) in storage.objects

    async def test_the_current_secret_opening_the_canary_is_the_pass(self):
        storage = FakeStorage()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-x")),
        ):
            await database.run_backup()
            verdict = await database.newest_is_decryptable()

        assert verdict["checked"] is True
        assert verdict["decryptable"] is True

    async def test_a_rotated_secret_is_reported_rather_than_discovered_later(self):
        storage = FakeStorage()
        rotated = Fernet.generate_key().decode()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-x")),
        ):
            await database.run_backup()

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", rotated),
        ):
            verdict = await database.newest_is_decryptable()

        assert verdict["checked"] is True
        assert verdict["decryptable"] is False
        assert "unreadable" in verdict["reason"]

    async def test_a_backup_predating_canaries_is_unknown_not_a_failure(self):
        """Claiming an old dump is unreadable when it may be perfectly good
        sends somebody chasing the wrong problem during an incident."""
        storage = FakeStorage()
        storage.objects[
            "backups/postgres/2026/08/01/decibyl-20260801T020000Z.dump.enc"
        ] = b"written-before-canaries-existed"

        with (
            patch.object(database, "get_storage", return_value=storage),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
        ):
            verdict = await database.newest_is_decryptable()

        assert verdict["checked"] is False
        assert "predates" in verdict["reason"]

    async def test_no_backups_at_all_is_not_a_decryption_verdict(self):
        with (
            patch.object(database, "get_storage", return_value=FakeStorage()),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
        ):
            verdict = await database.newest_is_decryptable()

        assert verdict["checked"] is False


@pytest.mark.asyncio
class TestTheSecondCopy:
    """Backups beside the data they protect are a backup against hardware
    failure and nothing else. A compromised or deleted bucket takes both."""

    async def test_a_mirror_receives_the_same_ciphertext(self):
        from api.services.backup import mirror

        primary = FakeStorage()
        secondary = FakeStorage()

        with (
            patch.object(database, "get_storage", return_value=primary),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-x")),
            patch.object(mirror, "BACKUP_MIRROR_BUCKET", "decibyl-dr"),
            patch.object(mirror, "get_mirror_storage", return_value=secondary),
        ):
            result = await database.run_backup()

        # Byte-identical, deliberately: re-encrypting would produce a different
        # ciphertext and remove the ability to compare the two copies.
        assert secondary.objects[result.key] == primary.objects[result.key]

    async def test_a_mirror_failure_never_costs_us_the_backup(self):
        """The wrong trade is obvious once stated: failing the nightly job over
        an unreachable mirror leaves no backup at all."""
        from api.services.backup import mirror

        primary = FakeStorage()

        class BrokenMirror:
            async def acreate_file_from_bytes(self, key, data):
                raise RuntimeError("mirror account suspended")

        with (
            patch.object(database, "get_storage", return_value=primary),
            patch.object(database, "PLATFORM_CREDENTIAL_SECRET", SECRET),
            patch.object(database, "_run_pg_dump", _dump_writes(b"PGDMP-x")),
            patch.object(mirror, "BACKUP_MIRROR_BUCKET", "decibyl-dr"),
            patch.object(mirror, "get_mirror_storage", return_value=BrokenMirror()),
        ):
            result = await database.run_backup()

        assert result.key in primary.objects

    async def test_a_truncated_mirror_copy_is_not_reported_as_mirrored(self):
        from api.services.backup import mirror

        secondary = FakeStorage(truncate=True)

        with (
            patch.object(mirror, "BACKUP_MIRROR_BUCKET", "decibyl-dr"),
            patch.object(mirror, "get_mirror_storage", return_value=secondary),
        ):
            assert await mirror.mirror_object("k", b"payload") is False


class TestHowAMirrorIsConfigured:
    """Whether the second copy is protected by anything the first is not."""

    def test_a_mirror_without_its_own_credentials_is_reported_as_partial(self):
        """Same identity as the primary means the same blast radius for
        anything that is not a single bucket being deleted."""
        from api.services.backup import mirror

        with (
            patch.object(mirror, "BACKUP_MIRROR_BUCKET", "decibyl-dr"),
            patch.object(mirror, "BACKUP_MIRROR_ACCESS_KEY_ID", None),
            patch.object(mirror, "BACKUP_MIRROR_SECRET_ACCESS_KEY", None),
        ):
            assert mirror.is_configured() is True
            assert mirror.shares_credentials_with_primary() is True

    def test_separate_credentials_are_what_makes_it_a_mirror(self):
        from api.services.backup import mirror

        with (
            patch.object(mirror, "BACKUP_MIRROR_BUCKET", "decibyl-dr"),
            patch.object(mirror, "BACKUP_MIRROR_ACCESS_KEY_ID", "AKIAEXAMPLE"),
            patch.object(mirror, "BACKUP_MIRROR_SECRET_ACCESS_KEY", "secret"),
        ):
            assert mirror.shares_credentials_with_primary() is False

    def test_nothing_configured_is_a_no_op_rather_than_an_error(self):
        from api.services.backup import mirror

        with patch.object(mirror, "BACKUP_MIRROR_BUCKET", None):
            assert mirror.is_configured() is False
            assert mirror.get_mirror_storage() is None

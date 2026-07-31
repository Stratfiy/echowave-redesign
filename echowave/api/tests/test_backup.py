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

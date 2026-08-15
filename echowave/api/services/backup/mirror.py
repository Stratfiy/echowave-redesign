"""A second copy of each backup, somewhere the first one's failure cannot reach.

The primary dump is written to a prefix inside the same object store, under the
same credentials, in the same account as the call recordings. That is a backup
against hardware failure and against nothing else. A compromised or deleted
bucket takes the database and its backups together, and the two most common
ways to lose a bucket — ransomware and a mistaken ``aws s3 rm`` — both have
exactly that shape.

So the mirror is only worth configuring if it is genuinely elsewhere:

* **Different credentials.** Ideally write-only ones this deployment holds for
  no other purpose. A mirror written with the primary's keys is reachable by
  anything that reaches the primary.
* **Different account.** An account-wide compromise or a billing suspension
  takes every bucket in the account with it.
* **Object lock, in compliance mode, for at least the retention window.** This
  is the part that survives an attacker who has the write credentials: they can
  add objects and cannot remove them.

Unconfigured, everything here is a no-op that says so. Backups still work; they
simply have the blast radius they always had, and readiness reports it rather
than leaving it to be discovered.
"""

from __future__ import annotations

from loguru import logger

from api.constants import (
    BACKUP_MIRROR_ACCESS_KEY_ID,
    BACKUP_MIRROR_BUCKET,
    BACKUP_MIRROR_ENDPOINT_URL,
    BACKUP_MIRROR_REGION,
    BACKUP_MIRROR_SECRET_ACCESS_KEY,
    S3_REGION,
)


def is_configured() -> bool:
    return bool(BACKUP_MIRROR_BUCKET)


def shares_credentials_with_primary() -> bool:
    """Whether the mirror is protected by anything the primary is not.

    A mirror bucket with no credentials of its own falls back to the ambient
    chain — the same identity that writes the primary. That still survives a
    single bucket being deleted, and survives nothing about a compromised
    account, so it is reported as partial rather than as protection.
    """
    return is_configured() and not (
        BACKUP_MIRROR_ACCESS_KEY_ID and BACKUP_MIRROR_SECRET_ACCESS_KEY
    )


def get_mirror_storage():
    """The mirror filesystem, or ``None`` when none is configured."""
    if not BACKUP_MIRROR_BUCKET:
        return None

    from api.services.filesystem import S3FileSystem

    return S3FileSystem(
        bucket_name=BACKUP_MIRROR_BUCKET,
        region_name=BACKUP_MIRROR_REGION or S3_REGION,
        endpoint_url=BACKUP_MIRROR_ENDPOINT_URL,
        access_key_id=BACKUP_MIRROR_ACCESS_KEY_ID,
        secret_access_key=BACKUP_MIRROR_SECRET_ACCESS_KEY,
    )


async def mirror_object(key: str, payload: bytes) -> bool:
    """Copy one already-encrypted object to the mirror, and verify the size.

    Returns whether the copy landed. Never raises: a mirror failure must not
    fail the nightly backup, because the outcome of doing so is *no* backup
    rather than one. It is logged at error level and surfaces through the
    readiness check, which is where an operator will act on it.

    The bytes are passed in rather than re-read, so the mirror holds exactly
    what the primary holds — re-encrypting would produce a different ciphertext
    and remove the ability to compare them.
    """
    storage = get_mirror_storage()
    if storage is None:
        return False

    try:
        if not await storage.acreate_file_from_bytes(key, payload):
            logger.error("Backup mirror upload returned failure for {}", key)
            return False

        metadata = await storage.aget_file_metadata(key)
        mirrored = int((metadata or {}).get("size") or 0)
        if mirrored != len(payload):
            logger.error(
                "Backup mirror verification failed for {}: expected {} bytes, "
                "object holds {}",
                key,
                len(payload),
                mirrored,
            )
            return False
    except Exception as exc:  # noqa: BLE001 — see the docstring
        logger.error("Backup mirror write failed for {}: {}", key, exc)
        return False

    logger.info("Backup mirrored to {}/{}", BACKUP_MIRROR_BUCKET, key)
    return True


async def newest_mirrored_key() -> str | None:
    """The most recent object in the mirror, for the readiness comparison."""
    storage = get_mirror_storage()
    if storage is None:
        return None

    from api.services.backup.database import BACKUP_PREFIX

    try:
        keys = await storage.alist_files(BACKUP_PREFIX)
    except Exception as exc:  # noqa: BLE001 — a readiness probe must not raise
        logger.warning("Could not list mirrored backups: {}", exc)
        return None

    dumps = sorted(k for k in (keys or []) if k.endswith(".dump.enc"))
    return dumps[-1] if dumps else None

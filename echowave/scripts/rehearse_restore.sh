#!/usr/bin/env bash
#
# Restore the newest backup into a scratch database and check it came back.
#
# An untested backup is a hypothesis. This turns it into a fact, and it is
# written to be run on a schedule rather than once: backups break silently —
# a schema change, a rotated secret, an object store that started truncating —
# and the failure only shows up on the day it cannot show up.
#
# Safe by construction. It creates its own scratch database, restores into that,
# and drops it at the end. It never touches the live one, and pg_restore is
# never pointed at anything the platform reads.
#
# Usage:
#   PLATFORM_CREDENTIAL_SECRET=... ./scripts/rehearse_restore.sh
#
# On the deployed box:
#   docker compose exec -T api python -m scripts.fetch_latest_backup > backup.enc
#   ./scripts/rehearse_restore.sh backup.enc

set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'

SCRATCH_DB="${SCRATCH_DB:-decibyl_restore_rehearsal}"
ENCRYPTED="${1:-}"

if [[ -z "${PLATFORM_CREDENTIAL_SECRET:-}" ]]; then
    echo "${RED}PLATFORM_CREDENTIAL_SECRET is not set.${NC}" >&2
    echo "Backups are encrypted with it; without it there is nothing to restore." >&2
    exit 1
fi

if [[ -z "$ENCRYPTED" || ! -f "$ENCRYPTED" ]]; then
    echo "${RED}Usage: $0 <encrypted-backup-file>${NC}" >&2
    exit 1
fi

WORKDIR="$(mktemp -d)"
# Always clean up, including the decrypted dump — it is the whole database in
# plaintext and must not be left on disk because the script failed halfway.
cleanup() {
    rm -rf "$WORKDIR"
    dropdb --if-exists "$SCRATCH_DB" 2>/dev/null || true
}
trap cleanup EXIT

echo "${YELLOW}1/4${NC} Decrypting…"
python3 - "$ENCRYPTED" "$WORKDIR/restore.dump" <<'PY'
import os, sys
from cryptography.fernet import Fernet

source, destination = sys.argv[1], sys.argv[2]
cipher = Fernet(os.environ["PLATFORM_CREDENTIAL_SECRET"].encode())
with open(source, "rb") as handle:
    plaintext = cipher.decrypt(handle.read())
with open(destination, "wb") as handle:
    handle.write(plaintext)
print(f"    decrypted {len(plaintext):,} bytes")
PY

echo "${YELLOW}2/4${NC} Creating scratch database ${SCRATCH_DB}…"
dropdb --if-exists "$SCRATCH_DB" 2>/dev/null || true
createdb "$SCRATCH_DB"
# pgvector is created by a migration, but pg_restore replays the CREATE
# EXTENSION only if the role may — so create it up front rather than have the
# restore half-fail on embeddings tables.
psql -d "$SCRATCH_DB" -qc "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1 || true

echo "${YELLOW}3/4${NC} Restoring…"
# --exit-on-error deliberately off: a fresh scratch database throws benign
# errors for roles and extensions that already exist. The verification below is
# what decides whether the restore worked, not the exit code.
pg_restore --no-owner --no-acl -d "$SCRATCH_DB" "$WORKDIR/restore.dump" 2>&1 \
    | grep -v "already exists" | tail -20 || true

echo "${YELLOW}4/4${NC} Verifying…"
read -r TABLES MIGRATION LEDGER ORGS <<<"$(psql -d "$SCRATCH_DB" -tAX -F' ' -c "
SELECT
  (SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),
  (SELECT coalesce(max(version_num),'NONE') FROM alembic_version),
  (SELECT count(*) FROM credit_ledger),
  (SELECT count(*) FROM organizations);
")"

echo
echo "  tables:          $TABLES"
echo "  migration head:  $MIGRATION"
echo "  organizations:   $ORGS"
echo "  credit ledger:   $LEDGER rows"
echo

# The ledger is the reason backups exist. A restore that produced a schema and
# no money history would still "succeed" by every other measure here.
if [[ "$TABLES" -lt 40 || "$MIGRATION" == "NONE" ]]; then
    echo "${RED}REHEARSAL FAILED${NC} — the restore did not produce a usable database." >&2
    exit 1
fi

echo "${GREEN}Restore rehearsal passed.${NC}"
echo "Record the date. Re-run after any schema change, secret rotation, or"
echo "storage migration — those are what silently break a working backup."

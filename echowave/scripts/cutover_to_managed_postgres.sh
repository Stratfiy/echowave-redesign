#!/usr/bin/env bash
#
# Move the database off the bundled container and onto managed Postgres.
#
# MIGRATE-TO-MANAGED-POSTGRES.md is the reasoning; this is the same steps in an
# order that cannot be got wrong. It exists because the runbook's steps are
# individually easy and their ORDER is the whole risk: a dump taken while calls
# are still being costed loses every row written between the dump and the
# switch, and nothing about the result looks wrong afterwards.
#
# One job: turn a 24-hour recovery point into minutes, without losing a ledger
# row on the way.
#
# It is deliberately NOT idempotent-by-retry. If it stops, it stops with the
# write path down and the old container still holding the data, which is a
# recoverable place to be. Read what it printed before running it again.
#
# WHAT IT NEVER DOES, because these are yours to decide:
#   - provision the RDS instance (see the runbook; --storage-encrypted cannot
#     be added later)
#   - drop, truncate or scale down the bundled postgres container -- that
#     container IS the rollback until you are confident
#   - set DATABASE_PITR_ENABLED unless the verifier has confirmed PITR is on
#
# Usage, on the deployed box, from the repo root:
#
#   PLATFORM_CREDENTIAL_SECRET=... \
#   ./scripts/cutover_to_managed_postgres.sh \
#       --target 'postgresql://decibyl:<pw>@decibyl-prod.xxxx.ap-south-1.rds.amazonaws.com:5432/decibyl' \
#       --rds-instance decibyl-prod
#
#   --skip-rehearsal   you have already run rehearse_restore.sh today
#   --dry-run          print what would happen and touch nothing

set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BOLD=$'\033[1m'; NC=$'\033[0m'

TARGET=""
RDS_INSTANCE=""
SKIP_REHEARSAL=0
DRY_RUN=0

die() { echo "${RED}${BOLD}$*${NC}" >&2; exit 1; }
say() { echo "${BOLD}$*${NC}"; }
ok()  { echo "${GREEN}  ok  ${NC} $*"; }
warn(){ echo "${YELLOW} warn ${NC} $*"; }

run() {
    if (( DRY_RUN )); then
        echo "       would run: $*"
    else
        "$@"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)         TARGET="${2:-}"; shift 2 ;;
        --rds-instance)   RDS_INSTANCE="${2:-}"; shift 2 ;;
        --skip-rehearsal) SKIP_REHEARSAL=1; shift ;;
        --dry-run)        DRY_RUN=1; shift ;;
        -h|--help)        sed -n '2,32p' "$0"; exit 0 ;;
        *)                die "Unknown argument: $1" ;;
    esac
done

[[ -n "$TARGET" ]] || die "--target is required (the managed instance's libpq URL)"
[[ -f docker-compose.yaml ]] || die "Run this from the repository root."
[[ -f .env ]] || die "No .env here. This script edits it, and it must already exist."

# asyncpg wants postgresql://, SQLAlchemy wants postgresql+asyncpg://. Accepting
# either and normalising both ways removes the single most common way a
# migration like this fails on its first line.
LIBPQ_TARGET="${TARGET/postgresql+asyncpg:\/\//postgresql://}"
ASYNC_TARGET="${LIBPQ_TARGET/postgresql:\/\//postgresql+asyncpg://}"

if grep -q "^DATABASE_URL=" .env; then
    CURRENT="$(grep '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)"
    if [[ "$CURRENT" == *"${LIBPQ_TARGET#postgresql://}"* ]]; then
        die "DATABASE_URL in .env already points at this target. Already cut over?
If a previous run failed partway, read MIGRATE-TO-MANAGED-POSTGRES.md §5 before
running this again -- re-running would dump an empty local database over a
populated managed one."
    fi
    warn "DATABASE_URL is already set in .env and points somewhere else."
    warn "It will be replaced. The old line is kept in the .env backup below."
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="${WORKDIR:-./cutover-$STAMP}"
run mkdir -p "$WORKDIR"

say ""
say "=== 0. The target, before anything is taken down ==="
# Checked FIRST and with the write path still up, because every failure here is
# a reason not to start: a missing pgvector or a retention period of 0 is a
# ten-minute fix in the console, and discovering it after the dump means the
# business is down while you fix it.
VERIFY_ARGS=(--database-url "$LIBPQ_TARGET")
[[ -n "$RDS_INSTANCE" ]] && VERIFY_ARGS+=(--rds-instance "$RDS_INSTANCE")
if ! run docker compose exec -T api python -m scripts.verify_managed_database "${VERIFY_ARGS[@]}"; then
    die "The target database is not ready. Nothing has been changed.
A schema warning about a missing alembic_version is EXPECTED on an empty
instance. A pgvector or PITR failure is not -- fix it in the console and re-run."
fi
ok "Target reachable, pgvector present, PITR on."

if [[ -z "$RDS_INSTANCE" ]]; then
    warn "No --rds-instance given, so POINT-IN-TIME RECOVERY WAS NOT CHECKED."
    warn "That is the entire purpose of this migration. Verify it in your"
    warn "provider's console before trusting the result."
fi

say ""
say "=== 1. Rehearse the restore ==="
if (( SKIP_REHEARSAL )); then
    warn "Skipped at your request."
else
    # Done before the cutover rather than after, for two reasons: it is the
    # first honest measurement of recovery time, and if last night's backup does
    # not restore cleanly you want to know that before starting a migration that
    # depends on a dump.
    [[ -n "${PLATFORM_CREDENTIAL_SECRET:-}" ]] || die \
        "PLATFORM_CREDENTIAL_SECRET is not set. Backups are encrypted with it.
Pass --skip-rehearsal only if you have already rehearsed a restore today."
    run bash -c "docker compose exec -T api python -m scripts.fetch_latest_backup > '$WORKDIR/backup.enc'"
    run ./scripts/rehearse_restore.sh "$WORKDIR/backup.enc"
    ok "Last night's backup restores and reconciles."
fi

say ""
say "=== 2. Stop the write path ==="
# THE STEP THE ORDER EXISTS FOR. A dump taken while calls are being costed
# loses whatever is written between the dump and the switch, and the result
# looks entirely healthy afterwards.
run docker compose stop api worker
ok "api and worker stopped. The business is down from here until step 6."

say ""
say "=== 3. Final dump ==="
run bash -c "docker compose exec -T postgres pg_dump -U postgres --format=custom postgres > '$WORKDIR/cutover.dump'"
if ! (( DRY_RUN )); then
    SIZE=$(stat -c%s "$WORKDIR/cutover.dump" 2>/dev/null || echo 0)
    (( SIZE > 1024 )) || die "The dump is ${SIZE} bytes, which is not a database.
The write path is still down. Start it again with ./remote_up.sh and
investigate before retrying."
    ok "Dumped $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE bytes")."
fi

say ""
say "=== 4. Restore into the managed instance ==="
run pg_restore --no-owner --no-acl --dbname "$LIBPQ_TARGET" "$WORKDIR/cutover.dump"
ok "Restored."

say ""
say "=== 5. Point the application at it ==="
run cp .env "$WORKDIR/env.before-cutover"
ok "Kept the old .env at $WORKDIR/env.before-cutover -- this is your rollback."
if ! (( DRY_RUN )); then
    # Commented rather than deleted: the old line is the thing somebody will
    # want to read at 2am, and a backup file in a directory they have forgotten
    # about is not where they will look for it.
    sed -i "s|^DATABASE_URL=|# replaced by cutover $STAMP: DATABASE_URL=|" .env
    printf '\n# Managed Postgres, cut over %s. See MIGRATE-TO-MANAGED-POSTGRES.md\nDATABASE_URL=%s\n' \
        "$STAMP" "$ASYNC_TARGET" >> .env
fi
ok "DATABASE_URL now points at the managed instance."

say ""
say "=== 6. Schema to head, then start ==="
run docker compose run --rm api alembic upgrade head
run ./remote_up.sh
ok "Up."

say ""
say "=== 7. Verify what is actually running ==="
# Without --database-url the verifier reads DATABASE_URL from the running
# container, so this answers "which database is the application talking to"
# rather than "which one do I believe I configured". They are not the same
# question, and compose has silently answered the second one before.
POST_ARGS=()
[[ -n "$RDS_INSTANCE" ]] && POST_ARGS+=(--rds-instance "$RDS_INSTANCE")
if ! run docker compose exec -T api python -m scripts.verify_managed_database "${POST_ARGS[@]}"; then
    die "The application is up but the verifier is not green.
DO NOT announce this as finished, and do not set DATABASE_PITR_ENABLED.

If the LEDGER RECONCILIATION is what failed, treat it as data loss until proven
otherwise: every row records the running balance it produced, so a restore that
dropped rows out of the middle leaves the row count plausible and only this
notices.

Rollback (the bundled container still holds the pre-cutover data):
    cp '$WORKDIR/env.before-cutover' .env && ./remote_up.sh
Anything written to the managed instance since step 6 is lost by rolling back."
fi
ok "The application is on the managed instance and the ledger reconciles."

say ""
say "=== 8. Tell the application the gap is closed ==="
if [[ -z "$RDS_INSTANCE" ]]; then
    warn "PITR was never checked, so DATABASE_PITR_ENABLED is NOT being set."
    warn "The flag is a statement about the world. Setting it while it is"
    warn "untrue is worse than leaving the finding open -- privacy readiness"
    warn "would report a recovery point you do not have."
elif ! (( DRY_RUN )); then
    if grep -q "^DATABASE_PITR_ENABLED=" .env; then
        sed -i "s|^DATABASE_PITR_ENABLED=.*|DATABASE_PITR_ENABLED=true|" .env
    else
        printf 'DATABASE_PITR_ENABLED=true\n' >> .env
    fi
    docker compose restart api
    ok "DATABASE_PITR_ENABLED=true. recovery_point goes from action_required to ready."
fi

say ""
say "${GREEN}Done.${NC}"
cat <<NEXT

Still yours to do, in this order:

  1. Run the application-level checks the runbook lists:
         docker compose exec api python -m scripts.verify_payment_round_trip
         curl -s https://<host>/api/v1/admin/billing/readiness | jq '.blocking'
         python scripts/e2e_smoke.py

  2. Place one real test call. The verifier proves the database is sound; it
     does not prove a call can be costed against it.

  3. LEAVE THE BUNDLED POSTGRES RUNNING for a fortnight. It is your rollback and
     it costs nothing. Only then:
         docker compose up -d --scale postgres=0

  4. Not closed by this migration: RDS automated backups live in the same AWS
     account as everything else, so losing the account loses both. That is
     BACKUP_MIRROR_* and a different afternoon.

Everything this run produced is in $WORKDIR -- including the pre-cutover .env
and the dump. Both are the whole database. Delete them deliberately, not by
forgetting about them.
NEXT

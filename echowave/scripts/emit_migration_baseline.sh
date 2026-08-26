#!/usr/bin/env bash
#
# Capture what the old box holds, before it is stopped.
#
# scripts/verify_region_migration.py --emit-baseline does the same thing and is
# the better tool, but it runs inside the api container — and the box you are
# migrating away from usually has its stack down, which is when
# `docker compose exec api` answers `service "api" is not running`. That is
# exactly the moment the baseline has to be taken, so this path needs postgres
# and nothing else.
#
# Starts postgres if it is not already up, and deliberately does NOT start the
# api container: the arq worker, the scheduled backup and the campaign
# orchestrator all live inside it under supervisord, and waking them on a box
# you are decommissioning means backups written from a database that is now a
# fork, and campaigns dialling real numbers from it.
#
# Usage, on the OLD box:
#   cd /home/ubuntu/echowave-redesign/echowave
#   ./scripts/emit_migration_baseline.sh > /tmp/old-box.json
#
# Then, on the new one:
#   docker compose exec -T api python -m scripts.verify_region_migration \
#       --baseline /tmp/old-box.json
#
# Bytes go to stdout and progress to stderr, so the redirect captures the JSON
# and nothing else.

set -euo pipefail

PG_USER="${POSTGRES_USER:-postgres}"
# postgres, not decibyl. docker-compose.yaml hardcodes `POSTGRES_DB: postgres`
# on the postgres service and the default DATABASE_URL ends in /postgres, so
# that is the database the platform actually uses.
PG_DB="${POSTGRES_DB:-postgres}"

#: Kept in step with _CORE_TABLES in verify_region_migration.py. A table that
#: does not exist is skipped rather than fatal — this has to work against an
#: older schema, since an old box is the whole point.
TABLES=(
    organizations
    users
    organization_memberships
    workflows
    workflow_definitions
    workflow_runs
    workflow_recordings
    campaigns
    credit_ledger
    payments
    billing_profiles
    telephony_configurations
    telephony_phone_numbers
    platform_provider_credentials
    organization_provider_credentials
    provider_rates
    subscription_plans
)

note() { printf '%s\n' "$*" >&2; }

psql_q() {
    docker compose exec -T postgres \
        psql -U "$PG_USER" -d "$PG_DB" -At -c "$1" 2>/dev/null
}

if ! docker compose ps --status running --services 2>/dev/null | grep -qx postgres; then
    note "postgres is not running — starting it (and only it)"
    docker compose up -d postgres >&2
    for _ in $(seq 1 40); do
        docker compose exec -T postgres pg_isready -U "$PG_USER" >/dev/null 2>&1 && break
        sleep 2
    done
fi

if ! docker compose exec -T postgres pg_isready -U "$PG_USER" >/dev/null 2>&1; then
    note "postgres did not come up. Check: docker compose logs postgres"
    exit 1
fi

note "Reading $PG_DB as $PG_USER"

counts_json="{}"
for table in "${TABLES[@]}"; do
    # Ask whether the table exists before counting, so one missing table on an
    # older schema does not take the whole capture down with it.
    if [ "$(psql_q "SELECT to_regclass('public.${table}') IS NOT NULL")" != "t" ]; then
        note "  skipped ${table} (no such table)"
        continue
    fi
    n="$(psql_q "SELECT count(*) FROM ${table}")"
    [ -n "$n" ] || { note "  could not count ${table}"; continue; }
    note "  ${table} ${n}"
    counts_json="$(jq --arg t "$table" --argjson n "$n" '. + {($t): $n}' <<<"$counts_json")"
done

revision="$(psql_q 'SELECT version_num FROM alembic_version' || true)"
newest="$(psql_q "SELECT to_char(max(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00') FROM workflow_runs" || true)"

jq -n \
    --arg captured_at "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" \
    --arg alembic_version "${revision:-}" \
    --arg newest "${newest:-}" \
    --argjson counts "$counts_json" \
    '{
        captured_at: $captured_at,
        alembic_version: (if $alembic_version == "" then null else $alembic_version end),
        counts: $counts,
        newest_workflow_run: (if $newest == "" then null else $newest end)
     }'

note ""
note "Baseline written. Copy it off this box before stopping the instance."

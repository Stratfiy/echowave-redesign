#!/usr/bin/env bash
# The production half of the Ops workflow (.github/workflows/ops.yml).
#
# Runs ON the box, as root, via SSM AWS-RunShellScript -- the same path deploys
# take. scripts/ops/dispatch.sh ships this file and redact.sed inside the SSM
# command itself, so what runs is always the version on the branch the
# workflow was dispatched from, never whatever happens to be checked out on
# the box (the trap deploy.yml documents at length).
#
# Inputs, all already validated by dispatch.sh and re-validated here:
#   OPS_ACTION   status | logs | restart | migrate-status | fetch-chunk | cleanup
#   OPS_SERVICE  a compose service, or "all"
#   OPS_MINUTES  1..120 (logs)
#   OPS_GREP     fixed-string filter (logs), ^[A-Za-z0-9 _./:-]{0,60}$
#   OPS_RUN_ID   the Actions run id, numeric; names the logs spool file
#   OPS_CHUNK    chunk index (fetch-chunk)
#   OPS_REDACT   path to redact.sed
#   PROJECT_DIR  the compose directory
#
# Rules this file keeps: it never prints .env, never runs `docker compose
# config` (which prints interpolated values), and anything that could carry a
# secret -- logs, the health body, alembic output -- goes through redact.sed
# before it reaches stdout, which is what SSM hands back to GitHub.

set -euo pipefail
export HOME="${HOME:-/root}"
umask 077

: "${OPS_ACTION:?}" "${OPS_REDACT:?}" "${PROJECT_DIR:?}"
OPS_SERVICE="${OPS_SERVICE:-api}"
OPS_MINUTES="${OPS_MINUTES:-15}"
OPS_GREP="${OPS_GREP:-}"
OPS_RUN_ID="${OPS_RUN_ID:-0}"
OPS_CHUNK="${OPS_CHUNK:-0}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"

#: SSM keeps at most 24,000 characters of a command's stdout, so logs are
#: spooled on the box (gzip + base64) and fetched in chunks below that size.
CHUNK_CHARS=20000
#: Upper bound on redacted log text spooled per run, before compression.
MAX_LOG_BYTES=5000000
SPOOL_DIR=/var/tmp/decibyl-ops

die() { printf 'ops: %s\n' "$1" >&2; exit 2; }
say() { printf '\n=== %s ===\n' "$1"; }
redact() { sed -E -f "$OPS_REDACT"; }

[[ "$OPS_RUN_ID" =~ ^[0-9]{1,20}$ ]] || die "bad run id"
[[ "$OPS_CHUNK" =~ ^[0-9]{1,4}$ ]] || die "bad chunk"
[[ "$OPS_MINUTES" =~ ^[0-9]{1,3}$ ]] && [ "$OPS_MINUTES" -ge 1 ] && [ "$OPS_MINUTES" -le 120 ] \
    || die "minutes must be 1..120"
[[ "$OPS_GREP" =~ ^[A-Za-z0-9\ _./:-]{0,60}$ ]] || die "grep filter has disallowed characters"
[[ "$OPS_SERVICE" =~ ^[a-z0-9_-]{1,40}$ ]] || die "bad service name"

SPOOL="$SPOOL_DIR/logs-$OPS_RUN_ID.b64"

cd "$PROJECT_DIR"
compose() { docker compose --profile remote "$@"; }

# The service list comes from the compose file itself, so a service that is
# renamed or removed there is refused here rather than silently matching
# nothing. --services prints names only, never values.
require_service() {
    local allowed="$1"
    [ "$OPS_SERVICE" = "all" ] && return 0
    compose config --services 2>/dev/null | grep -qxF "$OPS_SERVICE" \
        || die "'$OPS_SERVICE' is not a service in docker-compose.yaml"
    tr ' ' '\n' <<< "$allowed" | grep -qxF "$OPS_SERVICE" \
        || die "'$OPS_SERVICE' is not allowed for $OPS_ACTION (allowed: $allowed all)"
}

health_once() {
    curl -sS -m 10 -o /tmp/ops-health.$$ -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000
}

show_health() {
    local code
    code="$(health_once)"
    echo "GET /api/v1/health -> HTTP $code"
    if [ -s "/tmp/ops-health.$$" ]; then
        head -c 4000 "/tmp/ops-health.$$" | redact
        echo
    fi
    rm -f "/tmp/ops-health.$$"
}

# Pin compose to the image tag that is running now. ci_deploy.sh exports
# IMAGE_TAG=<sha> only for its own shell in registry mode, so a bare `up -d`
# from here would resolve IMAGE_TAG from .env or fall back to `latest` -- and
# could swap the release under the restart. Same tag, same image.
pin_running_image_tag() {
    local cid image tag
    cid="$(compose ps -q api 2>/dev/null | head -1)"
    [ -n "$cid" ] || return 0
    image="$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)"
    case "$image" in
        *@*|"") return 0 ;;
    esac
    tag="${image##*:}"
    [[ "$tag" =~ ^[A-Za-z0-9_.-]{1,128}$ ]] || return 0
    [ "$tag" = "$image" ] && return 0
    export IMAGE_TAG="$tag"
    echo "Pinned IMAGE_TAG to the running release: $tag"
}

action_status() {
    say "Containers"
    # Names, state, health and image only -- nothing here can carry a value.
    compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}\t{{.Image}}'
    say "Health"
    show_health
    say "Disk"
    # df exits non-zero if any path is missing, and nothing in a status report
    # is worth failing the whole report over.
    { df -h / /var/lib/docker 2>/dev/null || true; } | awk '!seen[$0]++'
    say "Deployed commit"
    # A deploy detaches HEAD on the commit it shipped, so HEAD *is* the last
    # successful (or rolled-back-to) deploy. Printed outside redact.sed on
    # purpose: a 40-character SHA is exactly what the long-token rule masks.
    git -c safe.directory='*' -C "$PROJECT_DIR" log -1 --format='%H%n%cI  %an%n%s'
}

action_logs() {
    require_service "api ui sandbox nginx gotenberg coturn decibyl-init redis postgres minio falkordb"
    mkdir -p "$SPOOL_DIR"
    chmod 700 "$SPOOL_DIR"
    # Spool files from runs that died before cleanup.
    find "$SPOOL_DIR" -name 'logs-*.b64' -mmin +60 -delete 2>/dev/null || true

    local target=()
    [ "$OPS_SERVICE" = "all" ] || target=("$OPS_SERVICE")

    # Redact FIRST, filter second: grepping the raw text would let a caller
    # learn whether a secret appears in the logs from whether lines match.
    {
        printf '# %s logs, last %s min, filter %q, %s\n' \
            "$OPS_SERVICE" "$OPS_MINUTES" "$OPS_GREP" "$(date -u +%FT%TZ)"
        compose logs --no-color --timestamps --since "${OPS_MINUTES}m" "${target[@]}" 2>&1 \
            | redact \
            | { if [ -n "$OPS_GREP" ]; then grep -iF -- "$OPS_GREP" || true; else cat; fi; } \
            | tail -c "$MAX_LOG_BYTES"
    } | gzip -9 | base64 -w0 > "$SPOOL"

    local size
    size="$(wc -c < "$SPOOL")"
    # The one line dispatch.sh parses.
    echo "SPOOL_CHARS=$size CHUNK_CHARS=$CHUNK_CHARS"
}

action_fetch_chunk() {
    [ -f "$SPOOL" ] || die "no spooled logs for run $OPS_RUN_ID"
    tail -c +"$((OPS_CHUNK * CHUNK_CHARS + 1))" "$SPOOL" | head -c "$CHUNK_CHARS"
}

action_cleanup() {
    rm -f "$SPOOL"
    echo "cleaned"
}

action_restart() {
    require_service "api ui sandbox nginx"
    pin_running_image_tag
    if [ "$OPS_SERVICE" = "all" ]; then
        say "Recreating every service whose configuration changed"
        # Same as the deploy: --profile remote so decibyl-init re-renders nginx
        # and coturn config, then nginx restarted because it only reads conf.d
        # at start. No --force-recreate: compose recreates what .env changed.
        compose up -d 2>&1 | redact
        compose restart nginx 2>&1 | redact
    else
        say "Recreating $OPS_SERVICE"
        # --force-recreate so a restart restarts even if compose sees no diff;
        # --no-deps so recreating api does not touch postgres or redis.
        compose up -d --no-deps --force-recreate "$OPS_SERVICE" 2>&1 | redact
        if [ "$OPS_SERVICE" = "api" ]; then
            # nginx resolves upstreams at start; a recreated api gets a new IP.
            compose restart nginx 2>&1 | redact
        fi
    fi

    say "Waiting for health"
    local i code
    for i in $(seq 1 36); do
        code="$(health_once)"
        rm -f "/tmp/ops-health.$$"
        if [ "$code" = "200" ]; then
            echo "Healthy after ${i} attempt(s)"
            action_status
            return 0
        fi
        sleep 5
    done
    echo "NOT healthy after 180s (last HTTP $code)"
    say "api logs, last 80 lines (redacted)"
    compose logs --no-color --tail 80 api 2>&1 | redact
    action_status
    return 1
}

action_migrate_status() {
    # Read-only: `current` and `heads` never write. Same -c as ci_deploy.sh.
    say "alembic current (what the database is at)"
    compose exec -T api python -m alembic -c api/alembic.ini current 2>&1 | redact
    say "alembic heads (what this code expects)"
    compose exec -T api python -m alembic -c api/alembic.ini heads 2>&1 | redact
}

case "$OPS_ACTION" in
    status) action_status ;;
    logs) action_logs ;;
    fetch-chunk) action_fetch_chunk ;;
    cleanup) action_cleanup ;;
    restart) action_restart ;;
    migrate-status) action_migrate_status ;;
    *) die "unknown action" ;;
esac

#!/usr/bin/env bash
# The runner half of the Ops workflow (.github/workflows/ops.yml).
#
# Validates the inputs, then sends scripts/ops/remote.sh to the production box
# over SSM send-command -- the same AWS-RunShellScript document, OIDC role and
# instance that deploy.yml uses, so this needs no permission the deploy role
# does not already have (ssm:SendCommand, ssm:GetCommandInvocation,
# ssm:ListCommandInvocations).
#
# Required env: OPS_ACTION OPS_SERVICE OPS_MINUTES OPS_GREP INSTANCE_ID
#               PROJECT_DIR RUN_ID OUT_FILE
# Optional env: OPS_CONFIRM (config-import), AWS_REGION (set by
#               configure-aws-credentials; tells the box where Parameter Store is)
# Writes everything the box printed to OUT_FILE (and stdout).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDACT="$HERE/redact.sed"
REMOTE="$HERE/remote.sh"

: "${OPS_ACTION:?}" "${INSTANCE_ID:?}" "${PROJECT_DIR:?}" "${RUN_ID:?}" "${OUT_FILE:?}"
OPS_SERVICE="${OPS_SERVICE:-api}"
OPS_MINUTES="${OPS_MINUTES:-15}"
OPS_GREP="${OPS_GREP:-}"
OPS_CONFIRM="${OPS_CONFIRM:-}"
OPS_REGION="${AWS_REGION:-}"

#: Logs bigger than this many SSM round trips are refused rather than fetched
#: for minutes on end; narrow the window or add a filter instead.
MAX_CHUNKS=60

fail() { echo "::error::$1"; exit 1; }

# ---- validation: the workflow's choice inputs are enforced by GitHub for UI
# runs, but `gh workflow run` and the REST API can send anything, so every
# input is checked again here and once more on the box.
case "$OPS_ACTION" in
    status | logs | restart | migrate-status) ;;
    config-check | config-sync | config-import | config-rollback) ;;
    *) fail "action must be one of status, logs, restart, migrate-status, config-check, config-sync, config-import, config-rollback" ;;
esac
[[ "$OPS_SERVICE" =~ ^[a-z0-9_-]{1,40}$ ]] || fail "service has disallowed characters"
[[ "$OPS_MINUTES" =~ ^[0-9]{1,3}$ ]] && [ "$OPS_MINUTES" -ge 1 ] && [ "$OPS_MINUTES" -le 120 ] \
    || fail "minutes must be a whole number from 1 to 120"
[[ "$OPS_GREP" =~ ^[A-Za-z0-9\ _./:-]{0,60}$ ]] \
    || fail "grep may only contain letters, digits, space and _ . / : - (max 60)"
[[ "$RUN_ID" =~ ^[0-9]{1,20}$ ]] || fail "bad run id"
[[ "$OPS_REGION" =~ ^([a-z]{2}(-[a-z]+)+-[0-9])?$ ]] || fail "bad AWS region"
case "$OPS_ACTION" in
    restart | config-sync | config-rollback)
        case "$OPS_SERVICE" in
            api | ui | sandbox | nginx | all) ;;
            *) fail "$OPS_ACTION restarts only api, ui, sandbox, nginx or all (not stateful services)" ;;
        esac
        ;;
    config-import)
        # Writes to Parameter Store; typed on purpose, never a default.
        [ "$OPS_CONFIRM" = "import" ] || fail "config-import needs the confirm input set to exactly: import"
        ;;
esac

# Both files travel inside the command, gzip+base64, so nothing here depends
# on the box's checkout and no quoting survives into the remote shell: the
# only user-derived strings are the validated inputs, passed through jq's @sh.
REDACT_B64="$(gzip -9c "$REDACT" | base64 -w0)"
REMOTE_B64="$(gzip -9c "$REMOTE" | base64 -w0)"
# The Parameter Store tools, for config-* actions only (keeps the other
# commands as small as they were).
LIB_B64=""
case "$OPS_ACTION" in
    config-*)
        LIB_B64="$(tar -C "$HERE" -czf - config_sync.sh env_to_ssm.sh ssm_env.py | base64 -w0)"
        ;;
esac

# ssm_run <action> [chunk] [timeout-seconds] -> remote stdout on our stdout.
# Fails when the remote command did not succeed (stderr is printed redacted).
ssm_run() {
    local action="$1" chunk="${2:-0}" timeout="${3:-120}" params cid status
    params="$(mktemp)"
    jq -n \
        --arg redact "$REDACT_B64" --arg remote "$REMOTE_B64" \
        --arg action "$action" --arg service "$OPS_SERVICE" \
        --arg minutes "$OPS_MINUTES" --arg grep "$OPS_GREP" \
        --arg run "$RUN_ID" --arg chunk "$chunk" --arg dir "$PROJECT_DIR" \
        --arg lib "$LIB_B64" --arg confirm "$OPS_CONFIRM" --arg region "$OPS_REGION" \
        '{commands: ([
            "set -euo pipefail",
            "export HOME=\"${HOME:-/root}\"",
            "D=$(mktemp -d /tmp/decibyl-ops.XXXXXX)",
            "trap '\''rm -rf \"$D\"'\'' EXIT",
            ("printf %s " + ($redact|@sh) + " | base64 -d | gunzip > \"$D/redact.sed\""),
            ("printf %s " + ($remote|@sh) + " | base64 -d | gunzip > \"$D/remote.sh\"")
          ] + (if $lib == "" then [] else [
            "mkdir -p \"$D/lib\"",
            ("printf %s " + ($lib|@sh) + " | base64 -d | tar -xzf - -C \"$D/lib\"")
          ] end) + [
            ("OPS_ACTION=" + ($action|@sh) + " OPS_SERVICE=" + ($service|@sh)
              + " OPS_MINUTES=" + ($minutes|@sh) + " OPS_GREP=" + ($grep|@sh)
              + " OPS_RUN_ID=" + ($run|@sh) + " OPS_CHUNK=" + ($chunk|@sh)
              + " OPS_CONFIRM=" + ($confirm|@sh) + " OPS_REGION=" + ($region|@sh)
              + " PROJECT_DIR=" + ($dir|@sh)
              + " OPS_LIBDIR=\"$D/lib\" OPS_REDACT=\"$D/redact.sed\" bash \"$D/remote.sh\"")
          ])}' > "$params"

    cid="$(aws ssm send-command \
        --instance-ids "$INSTANCE_ID" \
        --document-name "AWS-RunShellScript" \
        --comment "gha ops ${action} run ${RUN_ID}" \
        --timeout-seconds "$timeout" \
        --parameters "file://$params" \
        --query "Command.CommandId" --output text)"
    rm -f "$params"

    status=Pending
    for _ in $(seq 1 $((timeout + 60))); do
        status="$(aws ssm list-command-invocations \
            --command-id "$cid" --instance-id "$INSTANCE_ID" \
            --query "CommandInvocations[0].Status" --output text 2>/dev/null || echo Pending)"
        case "$status" in
            Success | Failed | Cancelled | TimedOut) break ;;
        esac
        sleep 1
    done

    # stdout is NOT redacted a second time here. The box already put every
    # value-bearing line (logs, health body, alembic, compose output) through
    # redact.sed; what it deliberately left alone is the deployed commit SHA
    # and image tag, which the long-token rule would mask -- and showing them
    # is the point of `status`. Log chunks are base64 and get their second
    # pass after decoding, below. stderr is redacted: it is unplanned output.
    aws ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" \
        --query "StandardOutputContent" --output text
    if [ "$status" != "Success" ]; then
        aws ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" \
            --query "StandardErrorContent" --output text 2>/dev/null | sed -E -f "$REDACT" >&2 || true
        echo "::error::SSM command $cid for '$action' finished as $status" >&2
        return 1
    fi
}

: > "$OUT_FILE"
case "$OPS_ACTION" in
    status | migrate-status | config-check | config-import)
        ssm_run "$OPS_ACTION" 0 120 | tee "$OUT_FILE"
        ;;
    restart | config-sync | config-rollback)
        ssm_run "$OPS_ACTION" 0 600 | tee "$OUT_FILE"
        ;;
    logs)
        header="$(ssm_run logs 0 300)"
        chars="$(printf '%s\n' "$header" | sed -n 's/^SPOOL_CHARS=\([0-9]*\) CHUNK_CHARS=\([0-9]*\)$/\1 \2/p' | tail -1)"
        [ -n "$chars" ] || fail "box did not report a spool size: $header"
        read -r total size <<< "$chars"
        chunks=$(((total + size - 1) / size))
        if [ "$chunks" -gt "$MAX_CHUNKS" ]; then
            ssm_run cleanup >/dev/null || true
            fail "logs are $chunks chunks (> $MAX_CHUNKS); use fewer minutes or a grep filter"
        fi
        b64="$(mktemp)"
        trap 'rm -f "$b64"' EXIT
        for ((n = 0; n < chunks; n++)); do
            echo "fetching chunk $((n + 1))/$chunks" >&2
            ssm_run fetch-chunk "$n" 60 | tr -d '\n' >> "$b64"
        done
        ssm_run cleanup >/dev/null || echo "::warning::could not remove the spool file on the box" >&2
        # Second redaction pass on our side: belt and braces for anything the
        # box-side pass let through on an older redact.sed.
        base64 -d "$b64" | gunzip | sed -E -f "$REDACT" > "$OUT_FILE"
        cat "$OUT_FILE"
        ;;
esac

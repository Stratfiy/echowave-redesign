#!/usr/bin/env bash
# Render production configuration from AWS SSM Parameter Store into .env, as an
# overlay. Runs ON the box: called by scripts/ci_deploy.sh before every
# `docker compose up`, and by the Ops workflow (config-check, config-sync,
# config-rollback) through scripts/ops/remote.sh.
#
#   config_sync.sh                  overlay Parameter Store onto .env
#   config_sync.sh --check          report which keys differ (names only)
#   config_sync.sh --restore-latest put the newest .env.bak-* back
#
# One parameter per variable: /decibyl/prod/SLACK_SIGNING_SECRET sets the
# SLACK_SIGNING_SECRET line in .env. Keys that are not in Parameter Store are
# left exactly as they are, so keys can move over one at a time.
#
# Safe to run when Parameter Store is empty or unreachable, or when the aws CLI
# or the IAM permission is missing: it prints a warning, leaves .env alone and
# exits 0. It never prints a value. Exit 3 means a parameter could not be
# written safely (a multi-line value, say) and NOTHING was written.
#
# Env: ENV_FILE (default <echowave>/.env), CONFIG_SSM_PATH (default
# /decibyl/prod/), AWS_REGION (default: the instance's region, else ap-south-1).
# Logic and the escaping rules: scripts/ops/ssm_env.py. Runbook:
# docs/deployment/parameter-store.mdx.

set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$(cd "$HERE/../.." && pwd)/.env}"
CONFIG_SSM_PATH="${CONFIG_SSM_PATH:-/decibyl/prod/}"

case "${1:-}" in
    "") mode=sync ;;
    --check) mode=check ;;
    --restore-latest) mode=restore ;;
    -h | --help)
        sed -n '2,24p' "${BASH_SOURCE[0]}"
        exit 0
        ;;
    *)
        echo "usage: config_sync.sh [--check | --restore-latest]" >&2
        exit 2
        ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "WARNING: python3 is not installed on this box; .env left untouched"
    [ "$mode" = restore ] && exit 1
    exit 0
fi

exec python3 "$HERE/ssm_env.py" "$mode" --env-file "$ENV_FILE" --path "$CONFIG_SSM_PATH"

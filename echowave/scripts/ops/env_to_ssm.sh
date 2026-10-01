#!/usr/bin/env bash
# ONE-TIME migration: copy the production .env into AWS SSM Parameter Store.
#
# Run once on the box, by the founder, through the Ops workflow
# (action config-import, confirm "import"), or by hand as root:
#
#   sudo bash scripts/ops/env_to_ssm.sh --dry-run    # names it would create
#   sudo bash scripts/ops/env_to_ssm.sh              # create them
#
# For every KEY in .env that is not yet in Parameter Store it creates
# /decibyl/prod/KEY as a SecureString (KMS key alias/aws/ssm). Existing
# parameters are never replaced unless --overwrite is passed -- and even if
# the listing were stale, PutParameter without Overwrite is refused by AWS.
# Comments and blank lines are skipped; so are empty values and values compose
# would interpolate ($ outside single quotes), which are listed for doing by
# hand. Prints names only, never values.
#
# Afterwards: rotate the exposed secrets by editing their parameters in the AWS
# console, then run Ops -> config-sync (docs/deployment/parameter-store.mdx).
#
# Env: ENV_FILE (default <echowave>/.env), CONFIG_SSM_PATH (default
# /decibyl/prod/), AWS_REGION (default: the instance's region, else ap-south-1).

set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$(cd "$HERE/../.." && pwd)/.env}"
CONFIG_SSM_PATH="${CONFIG_SSM_PATH:-/decibyl/prod/}"

flags=()
for arg in "$@"; do
    case "$arg" in
        --overwrite | --dry-run) flags+=("$arg") ;;
        -h | --help)
            sed -n '2,23p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "usage: env_to_ssm.sh [--dry-run] [--overwrite]" >&2
            exit 2
            ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is not installed on this box" >&2
    exit 1
fi

exec python3 "$HERE/ssm_env.py" import --env-file "$ENV_FILE" --path "$CONFIG_SSM_PATH" "${flags[@]}"

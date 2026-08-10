#!/usr/bin/env bash
#
# Render deploy/templates/nginx.subdomains.conf.template into a real nginx
# config.
#
# This step used to be a paragraph of prose in docs/deployment/subdomains.mdx
# ("substitute the three hostnames and the upstream block, write the result
# where nginx reads its config"). Nobody ran it. The consequence was that
# app/api/docs.decibyl.ai all resolved to one default server block serving the
# dashboard — so the docs host returned a login page, and the API and the app
# were never actually split.
#
# A script beats a paragraph for a step you have to get exactly right at 2am.
#
# Usage:
#   scripts/render_subdomain_nginx.sh > /path/to/conf.d/decibyl.conf
#
# Environment (all have sensible defaults):
#   DECIBYL_APP_HOST     app.decibyl.ai
#   DECIBYL_API_HOST     api.decibyl.ai
#   DECIBYL_DOCS_HOST    docs.decibyl.ai
#   FASTAPI_WORKERS      number of uvicorn workers to load-balance across
#   FASTAPI_BASE_PORT    first worker port (workers occupy N consecutive ports)
#   API_UPSTREAM_HOST    hostname workers are reachable at (compose service name)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../deploy/templates/nginx.subdomains.conf.template"

APP_HOST="${DECIBYL_APP_HOST:-app.decibyl.ai}"
API_HOST="${DECIBYL_API_HOST:-api.decibyl.ai}"
DOCS_HOST="${DECIBYL_DOCS_HOST:-docs.decibyl.ai}"
WORKERS="${FASTAPI_WORKERS:-1}"
BASE_PORT="${FASTAPI_BASE_PORT:-8000}"
UPSTREAM_HOST="${API_UPSTREAM_HOST:-api}"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE" >&2
  exit 1
fi

# least_conn rather than round-robin: voice calls are long-lived WebSockets of
# wildly different durations, so request count is a far worse proxy for load
# than open connections.
build_upstream() {
  echo "upstream decibyl_api {"
  echo "    least_conn;"
  for ((i = 0; i < WORKERS; i++)); do
    echo "    server ${UPSTREAM_HOST}:$((BASE_PORT + i)) max_fails=3 fail_timeout=30s;"
  done
  echo "    keepalive 32;"
  echo "}"
}

UPSTREAM_BLOCK="$(build_upstream)"

# awk rather than sed for the upstream: the block is multi-line, and sed's
# substitution of a multi-line value is a portability minefield.
awk -v upstream="$UPSTREAM_BLOCK" \
    -v app="$APP_HOST" -v api="$API_HOST" -v docs="$DOCS_HOST" '
{
  if ($0 == "__DECIBYL_UPSTREAM_BLOCK__") {
    print upstream
    next
  }
  gsub(/__DECIBYL_APP_HOST__/, app)
  gsub(/__DECIBYL_API_HOST__/, api)
  gsub(/__DECIBYL_DOCS_HOST__/, docs)
  print
}' "$TEMPLATE"

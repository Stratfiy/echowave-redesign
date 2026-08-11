#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${DECIBYL_INIT_WORKSPACE_DIR:-/workspace}"
OUTPUT_ROOT="${DECIBYL_INIT_OUTPUT_ROOT:-/generated}"
NGINX_OUTPUT_DIR="$OUTPUT_ROOT/nginx"
COTURN_OUTPUT_DIR="$OUTPUT_ROOT/coturn"
CERTS_DIR="${DECIBYL_INIT_CERTS_DIR:-/certs}"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/setup_common.sh"

DECIBYL_DEPLOY_PROJECT_DIR="$WORKSPACE_DIR"

mkdir -p "$NGINX_OUTPUT_DIR" "$COTURN_OUTPUT_DIR"

if [[ "${ENVIRONMENT:-local}" == "production" ]]; then
    decibyl_validate_remote_runtime_env
    [[ -f "$CERTS_DIR/local.crt" ]] || decibyl_fail "certs/local.crt not found"
    [[ -f "$CERTS_DIR/local.key" ]] || decibyl_fail "certs/local.key not found"

    export TURN_EXTERNAL_IP="$SERVER_IP"

    # Subdomain topology when the operator has named *any* of the hosts,
    # single-host otherwise.
    #
    # This used to key on DECIBYL_APP_HOST alone, and the failure that caused
    # is the reason it no longer does. Naming only DECIBYL_DOCS_HOST — the
    # obvious thing to do when all you want is a docs subdomain — left the
    # single-host config in place, so docs.<domain> hit the one default server
    # block, which proxies to the app, whose middleware redirects anything
    # unauthenticated to /auth/login. The documentation site answered with a
    # login page and nothing anywhere said why.
    #
    # Any one of these being set is a deployment saying it has split hosts.
    # The template fills the unnamed ones in from the defaults.
    if [[ -n "${DECIBYL_APP_HOST:-}${DECIBYL_API_HOST:-}${DECIBYL_DOCS_HOST:-}${DECIBYL_ROOT_HOST:-}" ]]; then
        decibyl_render_subdomain_nginx_conf "$WORKSPACE_DIR" "$NGINX_OUTPUT_DIR/default.conf"
    else
        decibyl_render_remote_nginx_conf "$WORKSPACE_DIR" "$NGINX_OUTPUT_DIR/default.conf"
        # Built docs with no split-host config is the exact shape of the
        # failure above: the files exist, the hostname resolves, and every
        # request lands on the login page. Say so rather than let it be
        # discovered by a customer following a documentation link.
        if [[ -d "$WORKSPACE_DIR/docs/dist" ]]; then
            decibyl_warn "docs/dist exists but no DECIBYL_*_HOST is set — the docs hostname will serve the app's login page. Set DECIBYL_APP_HOST and DECIBYL_DOCS_HOST in .env."
        fi
    fi
    decibyl_render_remote_turn_conf "$WORKSPACE_DIR" "$COTURN_OUTPUT_DIR/turnserver.conf"
    decibyl_success "✓ decibyl-init rendered remote nginx and coturn config"
    exit 0
fi

if [[ -n "${TURN_SECRET:-}" && -n "${TURN_HOST:-}" ]]; then
    export TURN_EXTERNAL_IP="$TURN_HOST"
    decibyl_render_remote_turn_conf "$WORKSPACE_DIR" "$COTURN_OUTPUT_DIR/turnserver.conf"
    decibyl_success "✓ decibyl-init rendered local TURN config"
    exit 0
fi

decibyl_success "✓ decibyl-init no-op for current profile"

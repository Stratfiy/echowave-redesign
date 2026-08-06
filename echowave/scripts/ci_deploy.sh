#!/usr/bin/env bash
# The deploy, as run on the EC2 box by GitHub Actions over SSM.
#
# Lives in the repo rather than in the workflow YAML for three reasons: it can
# be read and reviewed as a script, it can be run by hand during an incident
# when Actions is not the thing you want in the loop, and the workflow stays
# short enough to see what it does at a glance.
#
# Deliberately does NOT touch .env. Configuration is the operator's, it holds
# live Razorpay keys and the credential secret, and a deploy that rewrites it is
# a deploy that can take the platform down by being run twice. If a release
# needs a new variable, add it by hand first — DEPLOY.md §3.
#
# Usage (normally via SSM, but a human can run it):
#   sudo -u <owner> REF=main ./scripts/ci_deploy.sh

set -euo pipefail

REF="${REF:-main}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
#: Who owns the checkout. SSM runs this as root, and root-owned files in the
#: working tree break every later sudo-less `git pull` and `nano .env` the
#: operator does by hand — the same trap `update_remote.sh` fixes with its own
#: ownership restore.
RUN_AS="${RUN_AS:-ubuntu}"

cd "$PROJECT_DIR"

say() { printf '\n=== %s ===\n' "$1"; }

restore_ownership() {
    if id -u "$RUN_AS" >/dev/null 2>&1; then
        chown -R "$RUN_AS:$RUN_AS" "$(git rev-parse --show-toplevel)" 2>/dev/null || true
    fi
}
trap restore_ownership EXIT

say "Deploying ref: $REF  (into $PROJECT_DIR)"

# The .git directory sits one level above the compose file in this repository —
# echowave-redesign/.git with echowave/docker-compose.yaml — which is the same
# layout quirk DEPLOY.md warns about for REPO_SOURCE.
GIT_DIR="$(git rev-parse --show-toplevel)"
say "Fetching"
git -C "$GIT_DIR" fetch --prune --recurse-submodules origin "$REF"

PREVIOUS_SHA="$(git -C "$GIT_DIR" rev-parse HEAD)"
say "Currently on $PREVIOUS_SHA — rolling back to this if the deploy fails"

git -C "$GIT_DIR" checkout --detach "origin/$REF" 2>/dev/null \
  || git -C "$GIT_DIR" checkout --detach "$REF"
git -C "$GIT_DIR" submodule update --init --recursive

NEW_SHA="$(git -C "$GIT_DIR" rev-parse HEAD)"
say "Now on $NEW_SHA"

rollback() {
    say "DEPLOY FAILED — rolling back to $PREVIOUS_SHA"
    git -C "$GIT_DIR" checkout --detach "$PREVIOUS_SHA" || true
    git -C "$GIT_DIR" submodule update --init --recursive || true
    docker compose up -d --build || true
    say "Rolled back. The stack is on the previous commit."
}
trap rollback ERR

say "Building"
docker compose build api ui

say "Starting"
docker compose up -d

# Migrations after the containers are up, because the api image is what carries
# alembic. Failing here rolls back the checkout but NOT the schema: a migration
# that half-applied needs a human, and pretending otherwise would turn one bad
# deploy into a corrupted database.
say "Migrations"
# -c is required: alembic.ini lives at api/alembic.ini, not the container's
# WORKDIR (/app). Without it alembic exits with "No 'script_location' key found
# in configuration", which reads like a broken config rather than a wrong path.
# Same invocation as scripts/migrate.sh.
docker compose exec -T api python -m alembic -c api/alembic.ini upgrade head

say "Health"
for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -fsS "$HEALTH_URL" -o /dev/null; then
        say "Healthy after ${i} attempt(s)"
        trap - ERR
        say "Deployed $NEW_SHA"
        # Read back what the deployment says about itself. Not fatal — a
        # readiness finding is a configuration gap, not a bad build, and
        # failing the deploy on one would block shipping a fix for it.
        say "Post-deploy readiness (informational)"
        docker compose exec -T api python -c "
import asyncio, json
from api.db import db_client
from api.services.billing import readiness as billing
from api.services.readiness import as_dict

async def main():
    async with db_client.async_session() as s:
        d = as_dict(await billing.assess(s))
    print('billing action_required:', d['action_required'])
    for c in d['checks']:
        if c['status'] != 'ready':
            print(f\"  [{c['status']}] {c['title']}\")
asyncio.run(main())
" || echo "  (readiness probe unavailable — not failing the deploy)"
        exit 0
    fi
    sleep 5
done

say "Never became healthy after $((HEALTH_RETRIES * 5))s"
docker compose logs --tail 100 api || true
exit 1

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

# git reads global config on every invocation, whatever level is being written,
# so with HOME unset *every* git command below fails with "fatal: $HOME not set"
# and exit 128 -- not just the config line, but the fetch and the checkout too.
# SSM does not guarantee HOME for root, so it is set here before any git runs.
export HOME="${HOME:-/root}"

# SSM always runs this as root, but the checkout is owned by $RUN_AS. Git's
# ownership check (since 2.35.2) refuses to operate on a directory it doesn't
# own unless told it's safe — and that check runs before the first `git`
# command below, so it has to be set before `cd` even happens.
#
# --replace-all, not --add: this runs on every deploy, and --add would append a
# duplicate line to the config file each time, forever. --system first because
# it is deterministic for root; the global fallback is for a human running this
# by hand as a non-root user, who cannot write /etc/gitconfig.
git config --system --replace-all safe.directory '*' 2>/dev/null \
  || git config --global --replace-all safe.directory '*' 2>/dev/null \
  || true

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

# The revision the database is actually at, or empty if it cannot be read.
#
# Read from postgres rather than from alembic, because the whole point is to
# answer this when the api container will not start.
db_revision() {
    docker compose exec -T postgres \
        psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-decibyl}" \
        -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]'
}

# Whether a commit's migration set contains a given revision.
#
# Greps the versions directory at that commit without checking it out, so this
# can be asked before deciding to move.
commit_has_revision() {
    local sha="$1" revision="$2"
    [ -n "$revision" ] || return 0
    # Anchored, because `down_revision = "<rev>"` contains `revision = "<rev>"`
    # as a substring — an unanchored match would report a commit as having a
    # revision when all it has is another migration pointing back at it, which
    # is precisely the case this guard exists to catch.
    git -C "$GIT_DIR" grep -qE "^revision(: str)? = \"$revision\"" \
        "$sha" -- 'echowave/api/alembic/versions/*.py' 2>/dev/null
}

# A rollback that cannot start is worse than no rollback.
#
# `alembic upgrade head` runs on the way up and migrations do not reverse, so
# once a deploy has migrated, the previous commit may no longer be able to read
# the database it is being pointed back at. The api then exits with "Can't
# locate revision identified by '<rev>'" and the container is gone — a failed
# deploy converted into an outage, which is exactly what happened on 24 Aug
# 2026 (deploy #89: rolled back past a3c9e1b47d02, api exited 255, stack down
# until someone rebuilt by hand).
#
# So the rollback asks first. If the database is at a revision the previous
# commit does not have, it stays on the new code and says so loudly. The new
# code is at least able to read the database; the old code demonstrably is not,
# and leaving a human a running stack with a failed deploy beats handing them a
# dead one.
rollback() {
    local revision
    revision="$(db_revision)"

    if [ -n "$revision" ] && ! commit_has_revision "$PREVIOUS_SHA" "$revision"; then
        say "DEPLOY FAILED — NOT rolling back"
        cat >&2 <<EOF
The database is at migration $revision, which $PREVIOUS_SHA does not contain.
Rolling back would leave the api unable to read its own database, and it would
not start at all.

The stack has been left on the new commit ($NEW_SHA). Fix forward: read the
failure above, then redeploy. If you must go back, you have to downgrade the
database first, and check what $revision did before you do — a downgrade drops
whatever it added.
EOF
        return 0
    fi

    say "DEPLOY FAILED — rolling back to $PREVIOUS_SHA"
    if [ -n "$revision" ]; then
        say "Database is at $revision, which $PREVIOUS_SHA has — safe to roll back"
    else
        say "Could not read the database revision; rolling back on the old behaviour"
    fi
    git -C "$GIT_DIR" checkout --detach "$PREVIOUS_SHA" || true
    git -C "$GIT_DIR" submodule update --init --recursive || true
    docker compose --profile remote up -d --build || true
    say "Rolled back. The stack is on the previous commit."
}
trap rollback ERR

say "Building"
docker compose build api ui

say "Starting"
# --profile remote is not optional, and leaving it off fails silently.
#
# nginx, coturn and decibyl-init are all declared under profiles: ["remote"].
# A bare `docker compose up -d` starts postgres, redis, minio, api and ui and
# reports success, while leaving nginx running on whatever configuration was
# last rendered — by a human running remote_up.sh, possibly months ago.
#
# That is why changing the hostname variables and deploying appeared to do
# nothing: decibyl-init never ran, so the nginx config was never re-rendered,
# so docs.<domain> kept falling through to the app and answering a login page
# after a deploy that said it succeeded. The same omission means a TURN
# credential change never reaches coturn.
docker compose --profile remote up -d

# nginx has to be restarted explicitly, and nothing about the deploy makes that
# obvious.
#
# decibyl-init renders the config into the nginx-generated volume. nginx is
# already running, with the same image and the same compose spec, so compose
# has no reason to recreate it — and nginx only reads conf.d at startup. The
# freshly rendered config therefore sits in the volume, unread, while the
# deploy reports success. This is the layer under the profile bug: even once
# decibyl-init runs, nothing tells nginx to look.
#
# remote_up.sh gets away without it by passing --force-recreate, which
# recreates everything. Doing that here would restart api and ui on every
# deploy for the sake of a config file, so the restart is targeted. Cheap and
# idempotent — nginx re-reads conf.d on start.
say "Reloading nginx"
docker compose --profile remote restart nginx

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

# The documentation is a build artifact, not a container. nginx mounts
# ./docs/dist read-only and serves it off disk, so a deploy that does not build
# it leaves the docs host answering whatever was last built by hand — or 404,
# if nobody ever did. That was the state this script shipped in.
#
# Built in a container rather than on the host: node is not installed on the
# box, and adding a host dependency to a deploy that otherwise only needs
# docker is a new way for the deploy to break. Same major version as the UI
# image builds with.
#
# Not fatal. A docs build failure must not roll back an API deploy that is
# otherwise healthy — the previous dist stays mounted and the failure is
# visible in the log.
say "Documentation"
if docker run --rm \
        -v "$PWD/docs:/docs" \
        -w /docs \
        --entrypoint sh \
        node:22-alpine \
        -c "npm ci --no-audit --no-fund && npm run build"; then
    say "Documentation built"
else
    say "WARNING: documentation build failed; the previously built docs/dist is still being served"
fi

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

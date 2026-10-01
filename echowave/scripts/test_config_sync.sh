#!/usr/bin/env bash
# Tests scripts/ops/config_sync.sh and scripts/ops/env_to_ssm.sh (logic in
# scripts/ops/ssm_env.py) against a fake `aws` on PATH and a throwaway .env.
#
#     bash scripts/test_config_sync.sh
#
# Never touches a real .env and never calls AWS.

# expect() takes its condition as a single-quoted string and evals it later, so
# the $ in those strings is meant not to expand yet (SC2016), and variables set
# for those conditions look unused to shellcheck (SC2034).
# shellcheck disable=SC2016,SC2034

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC="$HERE/ops/config_sync.sh"
IMPORT="$HERE/ops/env_to_ssm.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/app" "$WORK/puts"
ENV="$WORK/app/.env"
export ENV_FILE="$ENV" AWS_REGION=ap-south-1 FAKE_DIR="$WORK"
export PATH="$WORK/bin:$PATH"

pass=0
fail=0
ok() { printf 'ok   %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf 'FAIL %s\n' "$1"; fail=$((fail + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Every value the fake Parameter Store holds or the .env starts with. None of
# them may ever appear in anything the scripts print.
SECRETS=(
    'n3w-s1gn1ng$HOME=x #not-a-comment  spaced'
    'n3w-s1gn1ng'
    'p@ss$w0rd'
    'brand new value'
    "it's quoted"
    'old-signing-value'
    'olddbpass'
    'line-one'
    'pl41n-imp0rt-val'
)
ALL_OUTPUT="$WORK/all-output.txt"
: > "$ALL_OUTPUT"

# run <name> <cmd...>: runs a script, keeps stdout+stderr in $OUT and $RC.
run() {
    local name="$1"
    shift
    set +e
    OUT="$("$@" 2>&1)"
    RC=$?
    set -e
    printf '### %s\n%s\n' "$name" "$OUT" >> "$ALL_OUTPUT"
}

# ---- the fake aws ---------------------------------------------------------
# FAKE_MODE: ok | fail | empty | multiline. FAKE_GEN changes NEW_KEY's value so
# repeated syncs have something to write.
cat > "$WORK/bin/aws" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DIR/aws-calls.log"
mode="${FAKE_MODE:-ok}"
if [ "$2" = "put-parameter" ]; then
    src="${4#file://}"
    n="$(find "$FAKE_DIR/puts" -type f | wc -l)"
    cp "$src" "$FAKE_DIR/puts/$n.json"
    name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["Name"])' "$src")"
    if grep -qxF "$name" "$FAKE_DIR/existing-names" 2>/dev/null; then
        echo "An error occurred (ParameterAlreadyExists) when calling the PutParameter operation: The parameter already exists." >&2
        exit 254
    fi
    echo '{"Version": 1, "Tier": "Standard"}'
    exit 0
fi
[ "$2" = "get-parameters-by-path" ] || { echo "fake aws: unexpected $*" >&2; exit 99; }
case "$mode" in
    fail)
        echo "An error occurred (AccessDeniedException) when calling the GetParametersByPath operation: no" >&2
        exit 254 ;;
    nocreds)
        echo "Unable to locate credentials. You can configure credentials by running \"aws configure\"." >&2
        exit 253 ;;
    empty)
        echo '{"Parameters": []}'
        exit 0 ;;
esac
gen="${FAKE_GEN:-0}"
if [[ " $* " != *" --starting-token "* ]]; then
    cat <<JSON
{"Parameters": [
  {"Name": "/decibyl/prod/SLACK_SIGNING_SECRET", "Type": "SecureString", "Value": "n3w-s1gn1ng\$HOME=x #not-a-comment  spaced"},
  {"Name": "/decibyl/prod/DATABASE_URL", "Type": "SecureString", "Value": "postgresql+asyncpg://decibyl:p@ss\$w0rd@db:5432/postgres?a=b"},
  {"Name": "/decibyl/prod/NEW_KEY", "Type": "SecureString", "Value": "brand new value $gen"}
], "NextToken": "tok-page-2"}
JSON
    exit 0
fi
extra=""
if [ "$mode" = multiline ]; then
    extra=', {"Name": "/decibyl/prod/MULTI_KEY", "Type": "SecureString", "Value": "line-one\nline-two"}'
fi
cat <<JSON
{"Parameters": [
  {"Name": "/decibyl/prod/UNCHANGED_KEY", "Type": "String", "Value": "same-value"},
  {"Name": "/decibyl/prod/QUOTE_KEY", "Type": "SecureString", "Value": "it's quoted"},
  {"Name": "/decibyl/prod/nested/IGNORED", "Type": "String", "Value": "x"}$extra
]}
JSON
FAKE
chmod +x "$WORK/bin/aws"

write_env() {
    cat > "$ENV" <<'EOF'
# production settings -- a comment that must survive
SLACK_SIGNING_SECRET=old-signing-value
export DATABASE_URL="postgresql://decibyl:olddbpass@db/postgres"

UNCHANGED_KEY=same-value
UNTOUCHED_KEY=keep me as I am # trailing comment
QUOTE_KEY=old
EOF
    chmod 600 "$ENV"
}

sum() { sha256sum "$1" | cut -d' ' -f1; }
backups() { find "$WORK/app" -maxdepth 1 -name '.env.bak-*' | wc -l; }
# What bash (and therefore a `set -a; . .env` script) reads for one key.
bash_value() { (eval "$(grep -E "^(export )?$1=" "$ENV")" && eval "printf '%s' \"\$$1\""); }

# ---- 1. sync: overlay, pagination, untouched keys ---------------------------
write_env
before_inode="$(stat -c %i "$ENV")"
original_untouched="$(grep '^UNTOUCHED_KEY=' "$ENV")"
cp "$ENV" "$WORK/original.env"
if [ "$(id -u)" = 0 ]; then chown 65534:65534 "$ENV"; fi
: > "$WORK/aws-calls.log"
run "sync" bash "$SYNC"

expect "sync exits 0" '[ "$RC" = 0 ]'
expect "fetched with decryption, recursive" 'grep -q -- "--recursive" "$WORK/aws-calls.log" && grep -q -- "--with-decryption" "$WORK/aws-calls.log"'
expect "followed NextToken to page 2" 'grep -q -- "--starting-token tok-page-2" "$WORK/aws-calls.log"'
expect "page-2 key applied (QUOTE_KEY)" '[ "$(bash_value QUOTE_KEY)" = "it'"'"'s quoted" ]'
expect "value with \$ = # spaces is literal in bash" '[ "$(bash_value SLACK_SIGNING_SECRET)" = "n3w-s1gn1ng\$HOME=x #not-a-comment  spaced" ]'
expect "value written single-quoted" 'grep -qxF "SLACK_SIGNING_SECRET='"'"'n3w-s1gn1ng\$HOME=x #not-a-comment  spaced'"'"'" "$ENV"'
expect "export prefix kept" 'grep -q "^export DATABASE_URL='"'"'postgresql+asyncpg://decibyl:p@ss" "$ENV"'
expect "DATABASE_URL literal" '[ "$(bash_value DATABASE_URL)" = "postgresql+asyncpg://decibyl:p@ss\$w0rd@db:5432/postgres?a=b" ]'
expect "quote-bearing value double-quoted" 'grep -qxF "QUOTE_KEY=\"it'"'"'s quoted\"" "$ENV"'
expect "new key appended under marker" 'tail -2 "$ENV" | head -1 | grep -qF "# --- from AWS Parameter Store (/decibyl/prod/) ---" && tail -1 "$ENV" | grep -qxF "NEW_KEY='"'"'brand new value 0'"'"'"'
expect "key not in Parameter Store untouched" '[ "$(grep "^UNTOUCHED_KEY=" "$ENV")" = "$original_untouched" ]'
expect "unchanged key not rewritten" 'grep -qxF "UNCHANGED_KEY=same-value" "$ENV"'
expect "comment and blank line kept" 'grep -qxF "# production settings -- a comment that must survive" "$ENV" && grep -qx "" "$ENV"'
expect "nested parameter ignored" '! grep -q IGNORED "$ENV"'
expect "reports updated/added/unchanged by name" 'grep -qx "updated SLACK_SIGNING_SECRET" <<<"$OUT" && grep -qx "added NEW_KEY" <<<"$OUT" && grep -qx "unchanged UNCHANGED_KEY" <<<"$OUT"'
expect "machine-readable change count" 'grep -qx "CONFIG_SYNC_CHANGED=4" <<<"$OUT"'
expect ".env is chmod 600" '[ "$(stat -c %a "$ENV")" = 600 ]'
expect ".env replaced by rename (new inode)" '[ "$(stat -c %i "$ENV")" != "$before_inode" ]'
expect "no temp files left" '[ -z "$(find "$WORK/app" -name ".env.tmp-*")" ]'
expect "one backup" '[ "$(backups)" = 1 ]'
bak="$(find "$WORK/app" -maxdepth 1 -name '.env.bak-*' | head -1)"
expect "backup holds the previous .env" 'cmp -s <(grep -v "^$" "$bak") <(grep -v "^$" "$WORK/original.env")'
expect "backup is chmod 600" '[ "$(stat -c %a "$bak")" = 600 ]'
if [ "$(id -u)" = 0 ]; then
    expect "owner preserved on .env" '[ "$(stat -c %u:%g "$ENV")" = 65534:65534 ]'
    expect "owner preserved on backup" '[ "$(stat -c %u:%g "$bak")" = 65534:65534 ]'
fi

# What docker compose itself reads, when it is installed: the env_file
# semantics the api container gets, and .env interpolation of the project.
if docker compose version >/dev/null 2>&1; then
    cat > "$WORK/app/compose.yaml" <<'EOF'
services:
  probe:
    image: alpine
    env_file: [{path: .env, required: false}]
    labels:
      interpolated: "${SLACK_SIGNING_SECRET}"
EOF
    compose_json="$(docker compose --project-directory "$WORK/app" -f "$WORK/app/compose.yaml" config --format json 2>/dev/null || true)"
    compose_ok="$(python3 - "$compose_json" <<'PY'
import json, sys
try:
    svc = json.loads(sys.argv[1])["services"]["probe"]
except Exception:
    print("unparsed"); sys.exit()
env = svc["environment"]
want = {
    "SLACK_SIGNING_SECRET": "n3w-s1gn1ng$HOME=x #not-a-comment  spaced",
    "DATABASE_URL": "postgresql+asyncpg://decibyl:p@ss$w0rd@db:5432/postgres?a=b",
    "QUOTE_KEY": "it's quoted",
    "NEW_KEY": "brand new value 0",
}
# compose escapes a literal $ as $$ when it re-renders config, so $$ here
# means "a $ that was NOT interpolated" -- exactly what we want.
bad = [k for k, v in want.items() if (env.get(k) or "").replace("$$", "$") != v]
label = svc["labels"]["interpolated"].replace("$$", "$")
if label != want["SLACK_SIGNING_SECRET"]:
    bad.append("interpolation")
print("ok" if not bad else "mismatch " + " ".join(bad))
PY
)"
    expect "docker compose reads every value literally ($compose_ok)" '[ "$compose_ok" = ok ]'
    rm -f "$WORK/app/compose.yaml"
else
    printf 'skip docker compose not installed; compose parsing not checked\n'
fi

# ---- 2. idempotent -------------------------------------------------------
before="$(sum "$ENV")"
run "sync again" bash "$SYNC"
expect "second sync changes nothing" '[ "$RC" = 0 ] && [ "$(sum "$ENV")" = "$before" ] && grep -qx "CONFIG_SYNC_CHANGED=0" <<<"$OUT"'
expect "second sync makes no backup" '[ "$(backups)" = 1 ]'

# ---- 3. backup rotation keeps 3 -----------------------------------------
for gen in 1 2 3 4; do
    FAKE_GEN=$gen run "sync gen $gen" bash "$SYNC"
done
expect "only the last 3 backups kept" '[ "$(backups)" = 3 ]'
expect "latest value applied" '[ "$(bash_value NEW_KEY)" = "brand new value 4" ]'
newest="$(find "$WORK/app" -maxdepth 1 -name '.env.bak-*' -printf '%f\n' | python3 -c '
import sys; sys.path.insert(0, sys.argv[1]); import ssm_env
print(sorted(sys.stdin.read().split(), key=ssm_env._backup_sort_key)[-1])' "$HERE/ops")"
expect "newest backup is the previous generation" 'grep -qxF "NEW_KEY='"'"'brand new value 3'"'"'" "$WORK/app/$newest"'

# ---- 4. restore ----------------------------------------------------------
run "restore" bash "$SYNC" --restore-latest
expect "restore exits 0" '[ "$RC" = 0 ]'
expect "restore brings back the previous value" '[ "$(bash_value NEW_KEY)" = "brand new value 3" ]'
expect "restore keeps 3 backups" '[ "$(backups)" = 3 ]'

# ---- 5. AWS failures leave .env alone and exit 0 -------------------------
for mode in fail nocreds empty; do
    before="$(sum "$ENV")"
    nb="$(backups)"
    FAKE_MODE=$mode run "sync $mode" bash "$SYNC"
    expect "$mode: exit 0" '[ "$RC" = 0 ]'
    expect "$mode: warning printed" 'grep -q "^WARNING: .*left untouched" <<<"$OUT"'
    expect "$mode: .env untouched, no backup" '[ "$(sum "$ENV")" = "$before" ] && [ "$(backups)" = "$nb" ]'
    expect "$mode: no change count (so Ops does not restart)" '! grep -q CONFIG_SYNC_CHANGED <<<"$OUT"'
done
expect "AWS error code shown" 'grep -q "AccessDeniedException" "$ALL_OUTPUT"'

# aws not installed at all: a PATH with python3 and coreutils but no aws.
mkdir -p "$WORK/noaws"
for tool in python3 dirname; do ln -sf "$(command -v "$tool")" "$WORK/noaws/$tool"; done
before="$(sum "$ENV")"
PATH="$WORK/noaws" run "sync without aws" "$(command -v bash)" "$SYNC"
expect "no aws CLI: exit 0, warning, untouched" '[ "$RC" = 0 ] && grep -q "aws CLI is not installed" <<<"$OUT" && [ "$(sum "$ENV")" = "$before" ]'

# Missing .env: warn, create nothing.
ENV_FILE="$WORK/app/missing.env" run "sync no env" bash "$SYNC"
expect "missing .env: exit 0, nothing created" '[ "$RC" = 0 ] && [ ! -e "$WORK/app/missing.env" ]'

# ---- 6. multi-line value: refused, all or nothing ------------------------
before="$(sum "$ENV")"
nb="$(backups)"
FAKE_MODE=multiline FAKE_GEN=9 run "sync multiline" bash "$SYNC"
expect "multi-line value: exit 3" '[ "$RC" = 3 ]'
expect "multi-line value: names the key" 'grep -q "REFUSED: .*MULTI_KEY (value spans several lines" <<<"$OUT"'
expect "multi-line value: .env untouched (no partial write)" '[ "$(sum "$ENV")" = "$before" ] && [ "$(backups)" = "$nb" ]'

# ---- 7. --check ----------------------------------------------------------
write_env
before="$(sum "$ENV")"
run "check" bash "$SYNC" --check
expect "check exits 0" '[ "$RC" = 0 ]'
expect "check: differs" 'grep -qx "differs SLACK_SIGNING_SECRET" <<<"$OUT" && grep -qx "differs DATABASE_URL" <<<"$OUT"'
expect "check: missing from .env" 'grep -qx "missing-in-env NEW_KEY" <<<"$OUT"'
expect "check: .env-only keys" 'grep -qx "not-in-parameter-store UNTOUCHED_KEY" <<<"$OUT"'
expect "check: same keys not listed" '! grep -q "UNCHANGED_KEY" <<<"$OUT"'
expect "check: summary" 'grep -q "^config-check summary: 1 same, 3 differ, 1 missing from .env, 0 invalid, 1 only in .env" <<<"$OUT"'
expect "check never writes" '[ "$(sum "$ENV")" = "$before" ]'
FAKE_MODE=multiline run "check multiline" bash "$SYNC" --check
expect "check flags the multi-line value as invalid" 'grep -q "^invalid MULTI_KEY (value spans several lines" <<<"$OUT"'

# ---- 8. one-time import --------------------------------------------------
cat > "$ENV" <<'EOF'
# comment
SLACK_SIGNING_SECRET=old-signing-value

PLAIN_KEY='pl41n-imp0rt-val'
INTERP_KEY=prefix-$HOME
EMPTY_KEY=
EOF
printf '/decibyl/prod/SLACK_SIGNING_SECRET\n' > "$WORK/existing-names"
run "import dry run" bash "$IMPORT" --dry-run
expect "dry run creates nothing" '[ -z "$(find "$WORK/puts" -type f)" ] && grep -qx "would create /decibyl/prod/PLAIN_KEY" <<<"$OUT"'
run "import" bash "$IMPORT"
expect "import exits 0" '[ "$RC" = 0 ]'
expect "import creates the missing key" 'grep -qx "created /decibyl/prod/PLAIN_KEY" <<<"$OUT"'
expect "import keeps existing parameters" 'grep -qx "exists SLACK_SIGNING_SECRET (kept; --overwrite replaces it)" <<<"$OUT"'
expect "import skips interpolated and empty values" 'grep -q "^skipped INTERP_KEY" <<<"$OUT" && grep -q "^skipped EMPTY_KEY" <<<"$OUT"'
expect "import made exactly one PutParameter" '[ "$(find "$WORK/puts" -type f | wc -l)" = 1 ]'
expect "import: SecureString, alias/aws/ssm, no overwrite, exact value" 'python3 - "$WORK/puts/0.json" <<'"'"'PY'"'"'
import json, sys
r = json.load(open(sys.argv[1]))
assert r == {"Name": "/decibyl/prod/PLAIN_KEY", "Value": "pl41n-imp0rt-val", "Type": "SecureString",
             "KeyId": "alias/aws/ssm", "Tier": "Standard", "Overwrite": False}, r
PY'
expect "import: value never on the aws command line" '! grep -q "pl41n-imp0rt-val" "$WORK/aws-calls.log"'
: > "$WORK/existing-names"
rm -f "$WORK/puts/"*
run "import overwrite" bash "$IMPORT" --overwrite
expect "--overwrite is required to replace" 'grep -q "Overwrite\": true" "$WORK/puts/"*.json'

# ---- 9. nothing printed a value ------------------------------------------
leaked=0
for s in "${SECRETS[@]}"; do
    if grep -qF -- "$s" "$ALL_OUTPUT"; then
        bad "a value leaked into output: ${s:0:4}..."
        leaked=1
    fi
done
[ "$leaked" = 0 ] && ok "no value appears in any output"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = 0 ]

#!/usr/bin/env bash
# Tests scripts/ops/redact.sed, the filter the Ops workflow puts every log line
# through before it leaves the production box.
#
#     bash scripts/test_ops_redact.sh
#
# Each case is: input line, a secret that must NOT survive, and a piece of
# context that MUST survive (so a rule that blanks the whole line also fails).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDACT="$HERE/ops/redact.sed"

pass=0
fail=0

check() {
    local name="$1" input="$2" secret="$3" keep="$4" out
    out="$(printf '%s\n' "$input" | sed -E -f "$REDACT")"
    if [ -n "$secret" ] && [[ "$out" == *"$secret"* ]]; then
        printf 'FAIL %-28s secret survived: %s\n' "$name" "$out"
        fail=$((fail + 1))
        return
    fi
    if [[ "$out" != *"$keep"* ]]; then
        printf 'FAIL %-28s lost context %q: %s\n' "$name" "$keep" "$out"
        fail=$((fail + 1))
        return
    fi
    printf 'ok   %-28s %s\n' "$name" "$out"
    pass=$((pass + 1))
}

# Vendor-shaped fixtures are assembled at runtime so the file itself contains
# nothing GitHub push protection or a secret scanner reads as a live token.
XOX='xo''x'
SK='s''k-'
RZP='rz''p_live_'
AKIA='AK''IA'

JWT='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwib3JnIjo0Mn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'

check "db url asyncpg" \
    'sqlalchemy.exc.OperationalError: postgresql+asyncpg://decibyl:S3cr3t-P4ss@db.internal:5432/postgres' \
    'S3cr3t-P4ss' 'postgresql+asyncpg://decibyl:***REDACTED***@db.internal:5432/postgres'
check "db url plain postgres" \
    'connecting to postgres://admin:hunter2@10.0.0.5/app' 'hunter2' 'postgres://admin:***REDACTED***@10.0.0.5/app'
check "redis empty user" \
    'REDIS rediss://:Zx9pQw@cache.aws:6379/0 ok' 'Zx9pQw' 'rediss://:***REDACTED***@cache.aws'
check "slack bot token" \
    "posting with ${XOX}b-1234567890-0987654321-AbCdEfGhIjKlMnOp to #ops" 'AbCdEfGhIjKlMnOp' 'to #ops'
check "slack user token" \
    "${XOX}p-11-22-33-abcdef" 'abcdef' 'xox*-***REDACTED***'
check "jwt alone" \
    "session=$JWT ok" 'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c' ' ok'
check "jwt payload gone" \
    "cookie $JWT" 'eyJzdWIiOiIxMjM0NTY3ODkwIiwib3JnIjo0Mn0' 'cookie '
check "bearer header" \
    'headers={"Authorization": "Bearer abc123.def456-ghi"}' 'abc123.def456-ghi' 'Bearer ***REDACTED***'
check "bearer with jwt" \
    "Authorization: Bearer $JWT" 'SflKxwRJ' 'Authorization: Bearer'
check "basic auth" \
    'Authorization: Basic dXNlcjpwYXNzd29yZA==' 'dXNlcjpwYXNzd29yZA' 'Basic ***REDACTED***'
check "password=" \
    'login failed password=correct-horse user=bob' 'correct-horse' 'user=bob'
check "json password" \
    '{"user": "bob", "password": "pa55word"}' 'pa55word' '"user": "bob"'
check "api_key=" \
    'GET /v1?api_key=abcd1234efgh&page=2' 'abcd1234efgh' '&page=2'
check "x-api-key header" \
    'x-api-key: live_9f8e7d6c' 'live_9f8e7d6c' 'x-api-key'
check "apikey camel" \
    'ApiKey=QWERTY12345' 'QWERTY12345' 'ApiKey='
check "openai sk-" \
    "using ${SK}proj-AbCdEfGhIjKlMnOpQrStUv123 for llm" 'AbCdEfGhIjKlMnOpQrStUv123' 'for llm'
check "anthropic sk-" \
    "key ${SK}ant-api03-ZZZZZZZZZZZZZZZZZZZZ" 'ZZZZZZZZZZZZZZZZZZZZ' 'key sk-'
check "razorpay key" \
    "${RZP}Ab12Cd34Ef56Gh" 'Ab12Cd34Ef56Gh' 'rzp_live_'
check "aws access key" \
    "AccessKeyId ${AKIA}IOSFODNN7EXAMPLE" 'IOSFODNN7EXAMPLE' 'AccessKeyId AKIA'
check "long hex token" \
    'webhook sig 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 rejected' \
    '9f86d081884c7d659a2feaa0c55ad015' 'rejected'
check "long base64 token" \
    'token-ish Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdo== end' 'Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFi' ' end'
check "fernet-like key" \
    'PLATFORM key k3Jx_9aB-Qw7ErTyUiOp1AsDfGhJkLzXcVbNm2345678= set' 'k3Jx_9aB-Qw7ErTyUiOp1AsDf' ' set'
check "identifier kept" \
    'in run_pipeline_telephony_with_retries_and_backoff_handler' '' 'run_pipeline_telephony_with_retries_and_backoff_handler'
check "file path kept" \
    'File "/app/api/services/pipecat/run_pipeline_telephony.py", line 412' '' '/app/api/services/pipecat/run_pipeline_telephony.py'
check "uuid kept" \
    'call 3f2b9c1e-8a4d-4f6b-9e2a-1c7d5b3a9f10 ended' '' '3f2b9c1e-8a4d-4f6b-9e2a-1c7d5b3a9f10'
check "plain line untouched" \
    'INFO: 172.18.0.5:41234 - "GET /api/v1/health HTTP/1.1" 200 OK' '' '"GET /api/v1/health HTTP/1.1" 200 OK'
check "max_tokens kept" \
    'llm request max_tokens=512 temperature=0.2' '' 'max_tokens=512'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

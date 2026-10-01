# Masks credentials in text before it leaves the production box.
#
# Used by scripts/ops/remote.sh (on the box, over SSM) and again by
# scripts/ops/dispatch.sh (on the runner) as a second pass. GNU sed:
#     sed -E -f scripts/ops/redact.sed
# Tested by scripts/test_ops_redact.sh -- add a case there for every rule here.
#
# Best effort, not a guarantee: it masks the shapes secrets usually have. Order
# matters -- the specific shapes run first, the generic long-token rule last.

# scheme://user:password@host -- postgres, postgresql+asyncpg, redis (empty
# user), amqp, https basic auth. The user is kept, the password is not.
s#([A-Za-z][A-Za-z0-9+.-]*://[^:/@[:space:]]*):[^@[:space:]]+@#\1:***REDACTED***@#g

# Authorization headers.
s/(Bearer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1***REDACTED***/gI
s/(Basic[[:space:]]+)[A-Za-z0-9+\/=]{8,}/\1***REDACTED***/gI

# JWTs (header.payload.signature; the signature is empty for alg=none).
s/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/***JWT-REDACTED***/g

# Vendor-shaped keys.
s/xox[abposr]-[A-Za-z0-9-]+/xox*-***REDACTED***/g
s/xapp-[A-Za-z0-9-]+/xapp-***REDACTED***/g
s/sk-[A-Za-z0-9_-]{16,}/sk-***REDACTED***/g
s/rzp_(live|test)_[A-Za-z0-9]{8,}/rzp_\1_***REDACTED***/g
s/(AKIA|ASIA)[0-9A-Z]{16}/\1***REDACTED***/g

# key=value, key: value and "key": "value" for anything named like a secret.
s/((password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?token)["']?[[:space:]]*[=:][[:space:]]*["']?)[^"'&[:space:],;}]+/\1***REDACTED***/gI

# Long opaque tokens: 32+ characters of hex/base64url containing at least one
# digit. Pure-letter runs (long Python identifiers in a traceback) are put back
# untouched, and '/' and '.' are not token characters here so file paths
# survive. UUIDs (call ids, org ids -- the things you grep a log for) are
# protected first by swapping their hyphens for \x03, and restored after.
# \x01, \x02 and \x03 are temporary markers.
s/([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{12})/\1\x03\2\x03\3\x03\4\x03\5/g
s/[A-Za-z0-9+_-]{32,}={0,2}/\x01&\x02/g
s/\x01([A-Za-z+_-]+={0,2})\x02/\1/g
s/\x01[^\x02]*\x02/***TOKEN-REDACTED***/g
s/\x03/-/g

#!/usr/bin/env python3
"""Production configuration from AWS SSM Parameter Store, as an overlay on .env.

Runs ON the production box. Called by scripts/ops/config_sync.sh (modes
`sync`, `check`, `restore`) and scripts/ops/env_to_ssm.sh (mode `import`);
those wrappers are the interface, this file is the logic. Runbook:
docs/deployment/parameter-store.mdx.

One parameter per environment variable: /decibyl/prod/SLACK_SIGNING_SECRET is
the line SLACK_SIGNING_SECRET=... in .env. `sync` sets or replaces exactly the
keys Parameter Store has and leaves every other line of .env alone, so keys can
move over one at a time.

Hard rules, each covered by scripts/test_config_sync.sh:

* Never print a value. Output is key names, counts and AWS error codes only --
  never AWS error text from a write, which can echo the value back.
* If Parameter Store cannot be read, or holds nothing under the path, .env is
  not touched and the exit status is 0: a deploy must never fail because the
  overlay is unavailable.
* All or nothing. A value that cannot be written to .env unambiguously (a
  newline, for instance) refuses the whole sync with exit 3 and .env is left
  exactly as it was -- never half-applied.
* Atomic: the new .env is written to a temp file in the same directory,
  chmod 600, chowned to .env's owner, fsynced and renamed over .env. The
  previous file is kept as .env.bak-<UTC timestamp> (chmod 600, the last
  BACKUPS_KEPT only).

How values are written (see render_line): KEY='value' -- single quotes, which
both docker compose (compose-go dotenv) and a bash `. .env` read literally: no
$ interpolation, no escapes, no inline comments. A value containing a single
quote is written KEY="value" instead, which is only literal when it has no
backslash, $, " or backtick, so such a value is refused. A value that ends in a
backslash is refused too, because compose reads \\' as an escaped quote.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

DEFAULT_PATH = "/decibyl/prod/"
DEFAULT_REGION = "ap-south-1"
KMS_KEY = "alias/aws/ssm"
#: .env.bak-* files kept after a write.
BACKUPS_KEPT = 3
#: get-parameters-by-path returns at most 10 per API call; the CLI pages
#: through those. 50 per CLI call x 40 calls is far beyond what .env holds.
PAGE_ITEMS = "50"
MAX_PAGES = 40
#: Standard-tier parameters hold at most 4 KB.
MAX_VALUE_BYTES = 4096
AWS_TIMEOUT_S = 60

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: A definition in .env, the way compose-go reads one: optional `export`, the
#: key, optional spaces, `=`, the raw value.
LINE_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
PATH_RE = re.compile(r"^/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/$")
BACKUP_RE = re.compile(r"^\.env\.bak-\d{8}T\d{6}Z(-\d+)?$")
AWS_ERROR_RE = re.compile(r"An error occurred \(([A-Za-z0-9.]+)\)")

#: How a value read back from .env is classified.
LITERAL = "literal"  # we know exactly what compose will read
UNCERTAIN = "uncertain"  # compose would interpolate $ or process escapes
MULTILINE = "multiline"  # an opening quote with no closing quote on the line


class Unavailable(Exception):
    """Parameter Store could not be read; .env must be left alone."""


class Refused(Exception):
    """The change cannot be made safely; nothing is written."""


def out(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", flush=True)


# --------------------------------------------------------------------------
# .env parsing and rendering
# --------------------------------------------------------------------------


def _scan_quoted(raw: str, quote: str) -> str | None:
    """compose-go's quoted-value scan. None when the closing quote is missing.

    Inside quotes a backslash before the quote character escapes it (and is
    dropped); a backslash before anything else is kept along with that char.
    """
    chars = []
    escaped = False
    for ch in raw[1:]:
        if ch != quote:
            if not escaped and ch == "\\":
                escaped = True
                continue
            if escaped:
                escaped = False
                chars.append("\\")
            chars.append(ch)
            continue
        if escaped:
            escaped = False
            chars.append(ch)
            continue
        return "".join(chars)
    return None


def parse_value(raw: str) -> tuple[str, str]:
    """(value, kind) for the text after `=` on one .env line."""
    raw = raw.lstrip()
    if raw.startswith("'"):
        value = _scan_quoted(raw, "'")
        return ("", MULTILINE) if value is None else (value, LITERAL)
    if raw.startswith('"'):
        value = _scan_quoted(raw, '"')
        if value is None:
            return "", MULTILINE
        # Double quotes interpolate $ and expand \n, \t, ...: not compared.
        kind = UNCERTAIN if ("$" in value or "\\" in value) else LITERAL
        return value, kind
    # Unquoted: an inline comment starts at " #"; trailing space is dropped.
    value = raw.split(" #", 1)[0].rstrip()
    return value, (UNCERTAIN if "$" in value else LITERAL)


def render_line(key: str, value: str) -> str:
    """The .env line for key=value, or Refused. See the module docstring."""
    if "\n" in value or "\r" in value:
        raise Refused(f"{key} (value spans several lines; .env holds one line per key)")
    if "\x00" in value:
        raise Refused(f"{key} (value contains a NUL byte)")
    if "'" not in value and not value.endswith("\\"):
        line = f"{key}='{value}'"
    elif not any(c in value for c in "\"\\$`"):
        line = f'{key}="{value}"'
    else:
        raise Refused(
            f"{key} (value has a single quote or ends in a backslash, together "
            "with one of \\ $ \" ` -- .env cannot hold it unambiguously)"
        )
    # Belt and braces: what we write must read back as exactly the value.
    if parse_value(line.split("=", 1)[1]) != (value, LITERAL):
        raise Refused(f"{key} (value does not survive a round trip through .env)")
    return line


class EnvFile:
    """.env as lines, with the definitions indexed by key."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            self.lines = fh.read().splitlines(keepends=True)
        self.index: dict[str, list[int]] = {}
        self.multiline: list[str] = []
        for i, line in enumerate(self.lines):
            m = LINE_RE.match(line.rstrip("\r\n"))
            if not m:
                continue
            self.index.setdefault(m.group(2), []).append(i)
            if parse_value(m.group(3))[1] == MULTILINE:
                self.multiline.append(m.group(2))

    def value(self, key: str) -> tuple[str, str] | None:
        """The value compose reads for key (the last definition wins)."""
        idx = self.index.get(key)
        if not idx:
            return None
        m = LINE_RE.match(self.lines[idx[-1]].rstrip("\r\n"))
        assert m
        return parse_value(m.group(3))

    def keys(self) -> list[str]:
        return sorted(self.index)


# --------------------------------------------------------------------------
# AWS
# --------------------------------------------------------------------------


def resolve_region() -> str:
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(var):
            return os.environ[var]
    # The instance's own region, over IMDSv2. One second each; the box is in
    # ap-south-1, so that is the fallback when IMDS does not answer.
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urllib.request.urlopen(req, timeout=1).read().decode()
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token": token},
        )
        region = urllib.request.urlopen(req, timeout=1).read().decode().strip()
        if re.match(r"^[a-z]{2}(-[a-z]+)+-\d$", region):
            return region
    except Exception:  # noqa: BLE001 -- any failure means "use the fallback"
        pass
    return DEFAULT_REGION


def aws_error(stderr: bytes) -> str:
    """Just the error code from AWS CLI stderr, never the message text."""
    text = stderr.decode("utf-8", "replace")
    m = AWS_ERROR_RE.search(text)
    if m:
        return m.group(1)
    if "Unable to locate credentials" in text:
        return "NoCredentials (does the instance have an IAM role?)"
    if "Could not connect to the endpoint" in text:
        return "EndpointConnectionError"
    return "unknown error"


def aws(region: str, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, AWS_PAGER="")
    return subprocess.run(
        ["aws", *args, "--region", region, "--output", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=AWS_TIMEOUT_S,
        check=False,
    )


def fetch(region: str, path: str, decrypt: bool = True) -> dict[str, str]:
    """{KEY: value} for every parameter under path. Raises Unavailable."""
    params: dict[str, str] = {}
    token = None
    for _ in range(MAX_PAGES):
        args = ["ssm", "get-parameters-by-path", "--path", path, "--recursive",
                "--max-items", PAGE_ITEMS]
        if decrypt:
            args.append("--with-decryption")
        if token:
            args += ["--starting-token", token]
        try:
            proc = aws(region, args)
        except FileNotFoundError:
            raise Unavailable("the aws CLI is not installed on this box") from None
        except subprocess.TimeoutExpired:
            raise Unavailable(f"aws did not answer within {AWS_TIMEOUT_S}s") from None
        if proc.returncode != 0:
            raise Unavailable(f"aws exited {proc.returncode}: {aws_error(proc.stderr)}")
        try:
            page = json.loads(proc.stdout or b"{}")
        except ValueError:
            raise Unavailable("aws returned something that is not JSON") from None
        for p in page.get("Parameters") or []:
            name = p.get("Name", "")
            key = name[len(path):] if name.startswith(path) else ""
            if not KEY_RE.match(key):
                # Nested paths and names that are not env var names. The name
                # is safe to print; it is never a value.
                warn(f"skipped parameter {name!r} (not a valid env var name directly under {path})")
                continue
            params[key] = p.get("Value", "")
        token = page.get("NextToken")
        if not token:
            return params
    raise Unavailable(f"more than {MAX_PAGES} pages of parameters; refusing to guess")


# --------------------------------------------------------------------------
# Writing .env
# --------------------------------------------------------------------------


def _write_private(directory: str, prefix: str, data: str, uid: int, gid: int) -> str:
    """A new chmod-600 file with data, owned uid:gid, fsynced. Returns its path."""
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix)
    try:
        os.fchmod(fd, 0o600)
        if (uid, gid) != (os.geteuid(), os.getegid()):
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fd = -1
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        if fd >= 0:
            os.close(fd)
        os.unlink(tmp)
        raise
    return tmp


def _backup_name(directory: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    # Several writes in one second get -1, -2, ... -- always one past the
    # highest suffix still on disk, so a name freed by rotation is never
    # reused and sorting by (stamp, n) stays chronological.
    same = [_backup_sort_key(n)[1] for n in list_backups(directory)
            if _backup_sort_key(n)[0] == stamp]
    return f".env.bak-{stamp}" + (f"-{max(same) + 1}" if same else "")


def _backup_sort_key(name: str) -> tuple[str, int]:
    stamp, _, n = name[len(".env.bak-"):].partition("-")
    return stamp, int(n or 0)


def list_backups(directory: str) -> list[str]:
    """Backup file names, oldest first."""
    names = [n for n in os.listdir(directory) if BACKUP_RE.match(n)]
    return sorted(names, key=_backup_sort_key)


def atomic_replace(env_path: str, new_text: str) -> str:
    """Back up env_path, then atomically replace it with new_text.

    Returns the backup's file name. Old backups beyond BACKUPS_KEPT go.
    """
    directory = os.path.dirname(os.path.abspath(env_path))
    st = os.stat(env_path)
    with open(env_path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        old_text = fh.read()

    temps = [_write_private(directory, ".env.tmp-", new_text, st.st_uid, st.st_gid)]
    try:
        temps.append(_write_private(directory, ".env.tmp-", old_text, st.st_uid, st.st_gid))
        backup = _backup_name(directory)
        os.replace(temps[1], os.path.join(directory, backup))
        # The one step that changes what compose reads: a rename, so .env is
        # always either the whole old file or the whole new one.
        os.replace(temps[0], env_path)
    except BaseException:
        for tmp in temps:
            if os.path.exists(tmp):
                os.unlink(tmp)
        raise
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)

    for name in list_backups(directory)[:-BACKUPS_KEPT]:
        os.unlink(os.path.join(directory, name))
    return backup


def check_env_path(env_path: str) -> bool:
    if os.path.islink(env_path):
        raise Refused(f"{env_path} is a symlink; refusing to replace it")
    if not os.path.isfile(env_path):
        warn(f"no .env at {env_path}; nothing to overlay, nothing changed")
        return False
    return True


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def plan(env: EnvFile, params: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """(report lines, {key: new line}) for the overlay. Raises Refused."""
    if env.multiline:
        raise Refused(
            "these .env keys open a quoted value that continues on the next line, "
            "and this tool edits .env one line at a time -- fix them by hand: "
            + ", ".join(sorted(env.multiline))
        )
    refusals, report, changes = [], [], {}
    for key in sorted(params):
        try:
            line = render_line(key, params[key])
        except Refused as exc:
            refusals.append(str(exc))
            continue
        if len(params[key].encode()) > MAX_VALUE_BYTES:
            refusals.append(f"{key} (longer than {MAX_VALUE_BYTES} bytes)")
            continue
        current = env.value(key)
        if current is None:
            report.append(f"added {key}")
            changes[key] = line
        elif current == (params[key], LITERAL):
            report.append(f"unchanged {key}")
        else:
            report.append(f"updated {key}")
            changes[key] = line
    if refusals:
        raise Refused(
            "refusing the whole sync; nothing was written. Fix these parameters: "
            + "; ".join(refusals)
        )
    return report, changes


def apply(env: EnvFile, changes: dict[str, str], path: str) -> str:
    lines = list(env.lines)
    for key, line in changes.items():
        for i in env.index.get(key, []):
            m = LINE_RE.match(lines[i].rstrip("\r\n"))
            assert m
            ending = lines[i][len(lines[i].rstrip("\r\n")):] or "\n"
            lines[i] = m.group(1) + line + ending
    added = sorted(k for k in changes if k not in env.index)
    if added:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        marker = f"# --- from AWS Parameter Store ({path}) ---\n"
        if marker not in lines:
            lines.append(marker)
        lines.extend(changes[k] + "\n" for k in added)
    return "".join(lines)


def mode_sync(args: argparse.Namespace) -> int:
    if not check_env_path(args.env_file):
        return EXIT_OK
    try:
        params = fetch(args.region, args.path)
    except Unavailable as exc:
        warn(f"could not read Parameter Store ({exc}); .env left untouched")
        return EXIT_OK
    if not params:
        warn(f"Parameter Store has no parameters under {args.path}; .env left untouched")
        return EXIT_OK

    env = EnvFile(args.env_file)
    report, changes = plan(env, params)
    for line in report:
        out(line)
    only_env = [k for k in env.keys() if k not in params]
    n_added = sum(1 for k in changes if k not in env.index)
    out(f"config-sync summary: {len(changes) - n_added} updated, {n_added} added, "
        f"{len(params) - len(changes)} unchanged, {len(only_env)} only in .env (left as they are)")
    if changes:
        backup = atomic_replace(args.env_file, apply(env, changes, args.path))
        out(f"wrote {args.env_file} (previous version kept as {backup})")
    else:
        out(".env already matches Parameter Store; not rewritten")
    out(f"CONFIG_SYNC_CHANGED={len(changes)}")
    return EXIT_OK


def mode_check(args: argparse.Namespace) -> int:
    if not check_env_path(args.env_file):
        return EXIT_OK
    env = EnvFile(args.env_file)
    try:
        params = fetch(args.region, args.path)
    except Unavailable as exc:
        warn(f"could not read Parameter Store ({exc})")
        params = {}
    else:
        if not params:
            warn(f"Parameter Store has no parameters under {args.path}")

    counts = {"same": 0, "differs": 0, "missing-in-env": 0, "invalid": 0}
    for key in sorted(params):
        try:
            render_line(key, params[key])
        except Refused as exc:
            out(f"invalid {exc}")
            counts["invalid"] += 1
            continue
        current = env.value(key)
        if current is None:
            status = "missing-in-env"
        elif current == (params[key], LITERAL):
            status = "same"
        else:
            status = "differs"
        counts[status] += 1
        if status != "same":
            out(f"{status} {key}")
    only_env = [k for k in env.keys() if k not in params]
    for key in only_env:
        out(f"not-in-parameter-store {key}")
    out(f"config-check summary: {counts['same']} same, {counts['differs']} differ, "
        f"{counts['missing-in-env']} missing from .env, {counts['invalid']} invalid, "
        f"{len(only_env)} only in .env")
    return EXIT_OK


def mode_restore(args: argparse.Namespace) -> int:
    if not check_env_path(args.env_file):
        return EXIT_FAILED
    directory = os.path.dirname(os.path.abspath(args.env_file))
    backups = list_backups(directory)
    if not backups:
        out(f"no .env.bak-* in {directory}; nothing to restore")
        return EXIT_FAILED
    latest = backups[-1]
    with open(os.path.join(directory, latest), "r", encoding="utf-8",
              errors="surrogateescape", newline="") as fh:
        text = fh.read()
    saved = atomic_replace(args.env_file, text)
    out(f"restored .env from {latest}; the .env it replaced is kept as {saved}")
    out("CONFIG_SYNC_CHANGED=1")
    return EXIT_OK


def mode_import(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.env_file):
        out(f"no .env at {args.env_file}")
        return EXIT_FAILED
    env = EnvFile(args.env_file)
    try:
        existing = set(fetch(args.region, args.path, decrypt=False))
    except Unavailable as exc:
        out(f"cannot list Parameter Store ({exc}); nothing imported")
        return EXIT_FAILED

    counts = {"created": 0, "overwritten": 0, "exists": 0, "skipped": 0, "failed": 0}
    with tempfile.TemporaryDirectory(prefix="decibyl-ssm-import-") as tmpdir:
        os.chmod(tmpdir, 0o700)
        for key in env.keys():
            value, kind = env.value(key) or ("", LITERAL)
            reason = None
            if kind == MULTILINE:
                reason = "value spans several lines"
            elif kind == UNCERTAIN:
                reason = "compose interpolates $ or escapes in it; add it by hand with the literal value"
            elif value == "":
                reason = "empty; Parameter Store cannot hold an empty value"
            elif len(value.encode()) > MAX_VALUE_BYTES:
                reason = f"longer than {MAX_VALUE_BYTES} bytes"
            else:
                try:
                    render_line(key, value)
                except Refused as exc:
                    reason = str(exc)
            if reason:
                out(f"skipped {key} ({reason})")
                counts["skipped"] += 1
                continue
            if key in existing and not args.overwrite:
                out(f"exists {key} (kept; --overwrite replaces it)")
                counts["exists"] += 1
                continue
            name = args.path + key
            if args.dry_run:
                out(f"would {'overwrite' if key in existing else 'create'} {name}")
                continue
            # The value goes through a 600 file, never argv: argv is visible in
            # /proc, and `--value` would also expand file:// and http:// itself.
            req = os.path.join(tmpdir, "put.json")
            with open(os.open(req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump({"Name": name, "Value": value, "Type": "SecureString",
                           "KeyId": KMS_KEY, "Tier": "Standard",
                           "Overwrite": bool(args.overwrite and key in existing)}, fh)
            try:
                proc = aws(args.region, ["ssm", "put-parameter", "--cli-input-json", f"file://{req}"])
            finally:
                os.unlink(req)
            if proc.returncode == 0:
                verb = "overwritten" if key in existing else "created"
                out(f"{verb} {name}")
                counts[verb] += 1
            elif b"ParameterAlreadyExists" in proc.stderr:
                out(f"exists {key} (kept; --overwrite replaces it)")
                counts["exists"] += 1
            else:
                # The error code only: AWS validation messages can quote the value.
                out(f"failed {name} ({aws_error(proc.stderr)})")
                counts["failed"] += 1
    out("env-to-ssm summary: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return EXIT_FAILED if counts["failed"] else EXIT_OK


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["sync", "check", "restore", "import"])
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--overwrite", action="store_true", help="import: replace existing parameters")
    ap.add_argument("--dry-run", action="store_true", help="import: list what would be created")
    args = ap.parse_args(argv)
    if not PATH_RE.match(args.path):
        print(f"bad parameter path {args.path!r}; want /a/b/ with a trailing slash", file=sys.stderr)
        return EXIT_USAGE
    args.region = resolve_region() if args.mode != "restore" else ""
    handler = {"sync": mode_sync, "check": mode_check,
               "restore": mode_restore, "import": mode_import}[args.mode]
    try:
        return handler(args)
    except Refused as exc:
        out(f"REFUSED: {exc}")
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

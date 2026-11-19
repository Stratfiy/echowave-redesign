#!/usr/bin/env python3
"""Copy /decibyl/prod/ from one AWS account's Parameter Store into another's.

Why this exists rather than re-running ``scripts/ops/env_to_ssm.sh`` against
the ``.env`` you hold: Parameter Store is the source of truth, not ``.env``.
``docs/deployment/parameter-store.mdx`` tells the founder to rotate exposed
secrets *by editing their parameters in the console*, and the box only learns
those values on the next ``config-sync``. So a parameter can be newer than any
``.env``, and copying parameter-to-parameter is the only way to be sure.

Keep ``env_to_ssm.sh`` as the fallback for a key this misses.

    export PATH_PREFIX=/decibyl/prod/
    python3 copy_parameter_store.py \
        --source-profile decibyl-old --target-profile decibyl-new --dry-run
    python3 copy_parameter_store.py \
        --source-profile decibyl-old --target-profile decibyl-new

Properties worth knowing, all borrowed from ``scripts/ops/ssm_env.py``:

* **Values never reach the process list.** ``put-parameter`` is called with
  ``--cli-input-json file://`` against a 0600 temp file, never with the value
  as an argument, because anything in argv is visible to every other process
  on the machine.
* **Names only in the output.** A value is never printed, not even truncated.
* **Nothing is replaced unless you ask.** Without ``--overwrite`` an existing
  parameter in the target is left alone and reported as ``exists``.
* **Type and tier are carried across**, so a ``SecureString`` stays one. New
  ``SecureString`` parameters are written under ``alias/aws/ssm``, the same
  AWS-managed key the import script uses.

It does not copy KMS key policies, parameter policies or tags.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

DEFAULT_PATH = "/decibyl/prod/"


def run_aws(profile: str, region: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["aws", *args, "--profile", profile, "--region", region, "--output", "json"],
        capture_output=True,
        check=False,
    )


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def aws_json(profile: str, region: str, args: list[str], what: str) -> dict:
    proc = run_aws(profile, region, args)
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        die(f"{what} failed: {detail[-1] if detail else 'no stderr'}")
    try:
        return json.loads(proc.stdout.decode() or "{}")
    except json.JSONDecodeError:
        die(f"{what} returned output that is not JSON")
    return {}


def whoami(profile: str, region: str) -> str:
    got = aws_json(
        profile, region, ["sts", "get-caller-identity"], f"sts:GetCallerIdentity ({profile})"
    )
    return str(got.get("Account", "unknown"))


def read_all(profile: str, region: str, path: str) -> list[dict]:
    """Every parameter under path, decrypted, following NextToken."""
    out: list[dict] = []
    token: str | None = None
    while True:
        args = [
            "ssm",
            "get-parameters-by-path",
            "--path",
            path,
            "--recursive",
            "--with-decryption",
        ]
        if token:
            args += ["--next-token", token]
        got = aws_json(profile, region, args, "ssm:GetParametersByPath")
        out.extend(got.get("Parameters", []))
        token = got.get("NextToken")
        if not token:
            return out


def existing_names(profile: str, region: str, path: str) -> set[str]:
    # No decryption: we only need to know what is already there.
    names: set[str] = set()
    token: str | None = None
    while True:
        args = ["ssm", "get-parameters-by-path", "--path", path, "--recursive"]
        if token:
            args += ["--next-token", token]
        got = aws_json(profile, region, args, "ssm:GetParametersByPath (target)")
        names.update(p["Name"] for p in got.get("Parameters", []))
        token = got.get("NextToken")
        if not token:
            return names


def put(profile: str, region: str, param: dict, overwrite: bool) -> None:
    body: dict[str, object] = {
        "Name": param["Name"],
        "Value": param["Value"],
        "Type": param.get("Type", "SecureString"),
    }
    if body["Type"] == "SecureString":
        body["KeyId"] = "alias/aws/ssm"
    if param.get("Tier") and param["Tier"] != "Standard":
        body["Tier"] = param["Tier"]
    if overwrite:
        body["Overwrite"] = True

    fd, tmp = tempfile.mkstemp(prefix="ssmput-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(body, fh)
        proc = run_aws(
            profile, region, ["ssm", "put-parameter", "--cli-input-json", f"file://{tmp}"]
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip().splitlines()
            die(f"put-parameter {param['Name']} failed: {detail[-1] if detail else '?'}")
    finally:
        os.unlink(tmp)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-profile", required=True)
    ap.add_argument("--target-profile", required=True)
    ap.add_argument("--source-region", default=os.getenv("AWS_REGION", "ap-south-1"))
    ap.add_argument("--target-region", default=os.getenv("AWS_REGION", "ap-south-1"))
    ap.add_argument("--path", default=os.getenv("PATH_PREFIX", DEFAULT_PATH))
    ap.add_argument("--overwrite", action="store_true", help="replace parameters that already exist")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.path.startswith("/") or not args.path.endswith("/"):
        die("--path must start and end with '/' (e.g. /decibyl/prod/)")
    if args.source_profile == args.target_profile and args.source_region == args.target_region:
        die("source and target are the same account and region; nothing to copy")

    src_acct = whoami(args.source_profile, args.source_region)
    dst_acct = whoami(args.target_profile, args.target_region)
    if src_acct == dst_acct:
        die(f"both profiles resolve to account {dst_acct}; check your profiles")

    print(f"source : account {src_acct}  {args.source_region}  {args.path}")
    print(f"target : account {dst_acct}  {args.target_region}  {args.path}")
    print()

    params = read_all(args.source_profile, args.source_region, args.path)
    if not params:
        print("nothing found at that path in the source account")
        return 1
    present = existing_names(args.target_profile, args.target_region, args.path)

    created = skipped = replaced = 0
    for p in sorted(params, key=lambda x: x["Name"]):
        name = p["Name"]
        if name in present and not args.overwrite:
            print(f"exists   {name}")
            skipped += 1
            continue
        verb = "replace " if name in present else "create  "
        if args.dry_run:
            print(f"would {verb.strip()} {name}")
        else:
            put(args.target_profile, args.target_region, p, overwrite=args.overwrite)
            print(f"{verb} {name}")
        if name in present:
            replaced += 1
        else:
            created += 1

    print()
    verb = "would be" if args.dry_run else ""
    print(
        f"{len(params)} in source; {created} {verb} created, "
        f"{replaced} {verb} replaced, {skipped} left alone"
    )
    if args.dry_run:
        print("dry run: nothing was written")
    else:
        print("Now run Ops -> config-check against the new box; every key should read 'same'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

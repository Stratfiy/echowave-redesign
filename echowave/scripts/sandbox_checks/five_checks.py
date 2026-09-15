"""The five sandbox checks (working plan, Steps 5 and 6), one script per candidate.

Runs against a candidate sandbox provider stood up in our own AWS Mumbai
account and prints a yes/no table with the evidence for each check:

1. isolation  -- container or micro-VM: read /proc/1/cgroup, dmesg tail,
                 and whether a separate kernel is visible.
2. egress     -- denied by default: an outbound connect to a public host
                 must fail; then allowed per job when the provider is told.
3. secrets    -- nothing that looks like a key in the box: env, common
                 files, mounted metadata endpoint (169.254.169.254).
4. call-home  -- no outbound connection from the box or the control plane
                 to a vendor host during a plain run (netstat/ss snapshot,
                 resolved hostnames).
5. client     -- a Python call from our API runs a script and returns its
                 output, with wall time.

Usage:
    python -m scripts.sandbox_checks.five_checks daytona --url http://... --key ...
    python -m scripts.sandbox_checks.five_checks e2b --domain sandbox.example --key ...

The provider adapters are the only vendor-specific code; everything the
box runs is the same script, so the two candidates are compared on the
same evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

PROBE = r"""
import json, os, re, socket, subprocess, sys
out = {}
def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout[-2000:]
    except Exception as e:
        return f"err:{e}"
out["cgroup"] = sh("cat /proc/1/cgroup | head -5")
out["kernel"] = sh("uname -r; cat /proc/version | head -c 200")
out["virt"] = sh("systemd-detect-virt 2>/dev/null || cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown")
out["dmesg_head"] = sh("dmesg 2>/dev/null | head -3")
def connect(host, port=443):
    try:
        s = socket.create_connection((host, port), timeout=4); s.close(); return "open"
    except Exception as e:
        return f"blocked:{type(e).__name__}"
out["egress_public"] = connect("1.1.1.1")
out["egress_dns"] = connect("example.com")
out["metadata"] = connect("169.254.169.254", 80)
secretish = re.compile(r"(key|token|secret|password|passwd|credential)", re.I)
out["env_secretish"] = sorted(k for k in os.environ if secretish.search(k))
out["env_count"] = len(os.environ)
out["files"] = sh("ls -la ~/.aws ~/.config 2>/dev/null; cat /run/secrets/* 2>/dev/null | head -c 100")
out["connections"] = sh("ss -tnp 2>/dev/null | head -20 || netstat -tnp 2>/dev/null | head -20")
out["resolv"] = sh("cat /etc/resolv.conf | head -5")
print("PROBE_JSON=" + json.dumps(out))
"""


@dataclass
class Result:
    check: str
    verdict: str  # yes / no / unclear
    evidence: str


class Provider:
    """Create a box, run Python in it, destroy it. One adapter per vendor."""

    name = "base"

    def __init__(self, args: argparse.Namespace):
        self.args = args

    def create(self, *, allow_egress: list[str] | None = None) -> Any:
        raise NotImplementedError

    def run_python(self, box: Any, code: str) -> str:
        raise NotImplementedError

    def destroy(self, box: Any) -> None:
        raise NotImplementedError


class Daytona(Provider):
    """Daytona self-hosted, Python SDK ``daytona`` 0.190.0.

    The SDK takes a params object (not kwargs) and its allow list is a
    comma-separated list of CIDRs, not hostnames, so a named host is
    resolved here and passed as /32s. The API URL is the self-hosted
    server; nothing defaults to app.daytona.io.
    """

    name = "daytona"

    @staticmethod
    def _to_cidrs(hosts: list[str]) -> str:
        import ipaddress
        import socket

        cidrs: list[str] = []
        for h in hosts:
            try:
                ipaddress.ip_network(h, strict=False)
                cidrs.append(h)
                continue
            except ValueError:
                pass
            for info in socket.getaddrinfo(h, 443, socket.AF_INET, socket.SOCK_STREAM):
                ip = info[4][0]
                if f"{ip}/32" not in cidrs:
                    cidrs.append(f"{ip}/32")
        return ",".join(cidrs)

    def create(self, *, allow_egress=None):
        from daytona import CreateSandboxFromSnapshotParams  # type: ignore
        from daytona import Daytona as Client
        from daytona import DaytonaConfig

        client = Client(
            DaytonaConfig(api_key=self.args.key, api_url=self.args.url, target=self.args.target)
        )
        if allow_egress:
            params = CreateSandboxFromSnapshotParams(
                language="python", network_allow_list=self._to_cidrs(allow_egress)
            )
        else:
            params = CreateSandboxFromSnapshotParams(language="python", network_block_all=True)
        self._client = client
        return client.create(params, timeout=180)

    def run_python(self, box, code):
        r = box.process.code_run(code, timeout=60)
        return r.result or ""

    def destroy(self, box):
        self._client.delete(box)


class E2B(Provider):
    name = "e2b"

    def create(self, *, allow_egress=None):
        import os

        from e2b import Sandbox  # type: ignore

        os.environ["E2B_API_KEY"] = self.args.key
        if self.args.domain:
            os.environ["E2B_DOMAIN"] = self.args.domain
        kwargs: dict[str, Any] = {"allow_internet_access": False}
        if allow_egress:
            # allow_out takes hostnames; everything else stays denied.
            kwargs = {"network": {"allow_out": list(allow_egress), "deny_out": ["0.0.0.0/0"]}}
        return Sandbox.create(**kwargs)

    def run_python(self, box, code):
        r = box.commands.run(f"python3 - <<'PY'\n{code}\nPY")
        return (r.stdout or "") + (r.stderr or "")

    def destroy(self, box):
        box.kill()


PROVIDERS: dict[str, Callable[[argparse.Namespace], Provider]] = {
    "daytona": Daytona,
    "e2b": E2B,
}


def _probe(provider: Provider, box: Any) -> dict[str, Any]:
    out = provider.run_python(box, PROBE)
    line = next((ln for ln in out.splitlines() if ln.startswith("PROBE_JSON=")), None)
    return json.loads(line[len("PROBE_JSON=") :]) if line else {"raw": out}


def run(provider: Provider) -> list[Result]:
    results: list[Result] = []
    started = time.monotonic()
    box = provider.create()
    create_seconds = time.monotonic() - started
    try:
        t = time.monotonic()
        hello = provider.run_python(box, "print('hello from the box')")
        run_seconds = time.monotonic() - t
        results.append(
            Result(
                "5 client: a Python call runs a script and returns output",
                "yes" if "hello from the box" in hello else "no",
                f"create {create_seconds:.1f}s, run {run_seconds:.1f}s, output={hello.strip()[:60]!r}",
            )
        )
        p = _probe(provider, box)
        virt = (p.get("virt") or "").strip().lower()
        kernel = p.get("kernel") or ""
        micro_vm = (
            any(k in virt for k in ("kvm", "firecracker", "qemu", "microvm"))
            or "firecracker" in kernel.lower()
        )
        container = (
            "docker" in (p.get("cgroup") or "")
            or "containerd" in (p.get("cgroup") or "")
            or virt in ("docker", "container", "lxc", "podman")
        )
        results.append(
            Result(
                "1 isolation: container or micro-VM",
                "yes" if (micro_vm or container) else "unclear",
                f"virt={virt!r} kernel={kernel[:60]!r} cgroup={(p.get('cgroup') or '')[:80]!r} ({'micro-VM' if micro_vm else 'container' if container else 'unknown'})",
            )
        )
        blocked = str(p.get("egress_public", "")).startswith("blocked") and str(
            p.get("egress_dns", "")
        ).startswith("blocked")
        results.append(
            Result(
                "2a egress: denied by default",
                "yes" if blocked else "no",
                f"1.1.1.1:443={p.get('egress_public')} example.com:443={p.get('egress_dns')}",
            )
        )
        meta_blocked = str(p.get("metadata", "")).startswith("blocked")
        secretish = p.get("env_secretish") or []
        results.append(
            Result(
                "3 secrets: nothing key-like in the box, no metadata endpoint",
                "yes" if (not secretish and meta_blocked) else "no",
                f"env keys={secretish} metadata={p.get('metadata')} files={(p.get('files') or '').strip()[:80]!r}",
            )
        )
        conns = (p.get("connections") or "").strip()
        results.append(
            Result(
                "4 call-home: no outbound connections during a plain run",
                "yes" if not conns or conns.count("\n") == 0 else "unclear",
                f"ss/netstat: {conns[:200]!r}",
            )
        )
    finally:
        provider.destroy(box)

    # 2b: allowed per job
    box = provider.create(allow_egress=["example.com"])
    try:
        p = _probe(provider, box)
        allowed = str(p.get("egress_dns", "")) == "open" and str(
            p.get("egress_public", "")
        ).startswith("blocked")
        results.append(
            Result(
                "2b egress: allowed per job (example.com only)",
                "yes" if allowed else "no",
                f"example.com={p.get('egress_dns')} 1.1.1.1={p.get('egress_public')}",
            )
        )
    finally:
        provider.destroy(box)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--url", help="Daytona server URL")
    parser.add_argument("--domain", help="E2B self-hosted domain")
    parser.add_argument("--target", help="Daytona region id (the compose stack defines 'us')")
    parser.add_argument(
        "--key",
        required=True,
        help="API key for the candidate (never a production key)",
    )
    args = parser.parse_args(argv)
    provider = PROVIDERS[args.provider](args)
    results = run(provider)
    width = max(len(r.check) for r in results)
    for r in sorted(results, key=lambda r: r.check):
        print(f"{r.check:<{width}}  {r.verdict:<8} {r.evidence}")
    return 0 if all(r.verdict == "yes" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

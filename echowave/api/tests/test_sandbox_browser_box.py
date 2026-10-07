"""The browser box as the sandbox starts it, and the box's own proxy.

Docker is not on the test machine, so the box's ``docker run`` line is read
rather than run; the proxy is run for real, on loopback, against a small
server this test starts.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shlex
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("SANDBOX_SECRET", "s")
    return _load("sandbox_server_browser", ROOT / "sandbox" / "server.py")


class TestTheBox:
    def test_a_browser_box_has_the_internet_and_nothing_to_escalate(self, server):
        server.BROWSER_NETWORK = "decibyl_sandbox_browser"
        line = " ".join(
            shlex.quote(p)
            for p in server._browser_command("b1", server.BrowserRequest())
        )
        for flag in (
            "--network decibyl_sandbox_browser",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--read-only",
            "--user 1000:1000",
            "--pids-limit 512",
            "--tmpfs /work:rw,size=512m",
        ):
            assert flag in line, flag
        assert "--privileged" not in line
        assert "docker.sock" not in line
        assert "SECRET" not in line and "KEY" not in line

    def test_its_network_keeps_boxes_apart(self, server):
        command = " ".join(server.browser_network_command())
        assert "com.docker.network.bridge.enable_icc=false" in command
        assert "--internal" not in command  # it is meant to reach the web

    def test_unconfigured_browsers_are_refused_not_run_elsewhere(self, server):
        server.BROWSER_NETWORK = ""
        with TestClient(server.app) as client:
            response = client.post(
                "/browsers", json={}, headers={"x-sandbox-secret": "s"}
            )
        assert response.status_code == 503
        assert "SANDBOX_BROWSER_NETWORK" in response.json()["detail"]

    def test_the_secret_is_still_required(self, server):
        with TestClient(server.app) as client:
            assert client.post("/browsers", json={}).status_code == 401


class TestTheProxy:
    async def _origin(self):
        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            body = b"hello from the origin"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                % (len(body), body)
            )
            await writer.drain()
            writer.close()

        origin = await asyncio.start_server(handle, "127.0.0.1", 0)
        return origin, origin.sockets[0].getsockname()[1]

    async def _through(self, proxy_port: int, request: bytes) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(request)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), timeout=10)
        writer.close()
        return data

    async def test_private_and_denied_addresses_are_refused(self):
        netguard = _load(
            "browser_netguard_t", ROOT / "sandbox" / "browser" / "netguard.py"
        )
        origin, port = await self._origin()
        guard = netguard.Guard([{"site": "shady.example", "rule": "deny"}])
        proxy = await netguard.serve(guard)
        proxy_port = proxy.sockets[0].getsockname()[1]
        try:
            for request in (
                f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
                b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n",
                b"CONNECT localhost:443 HTTP/1.1\r\n\r\n",
                b"CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n",
                b"GET http://intranet/ HTTP/1.1\r\n\r\n",
                b"CONNECT pay.shady.example:443 HTTP/1.1\r\n\r\n",
                b"CONNECT example.com:22 HTTP/1.1\r\n\r\n",
            ):
                answer = await self._through(proxy_port, request)
                assert answer.startswith(b"HTTP/1.1 403"), request
                assert b"Decibyl's browser does not open this address" in answer
        finally:
            proxy.close()
            origin.close()

    async def test_a_name_resolving_to_a_private_address_is_refused(self):
        netguard = _load(
            "browser_netguard_r", ROOT / "sandbox" / "browser" / "netguard.py"
        )
        guard = netguard.Guard()
        address, why = await guard.resolve("localhost", 443)
        assert address is None and "private" in why

    async def test_an_allowed_host_is_passed_through(self):
        """Development's test host stands in for a public site: the proxy
        connects to the address it checked and relays the answer."""
        netguard = _load(
            "browser_netguard_ok", ROOT / "sandbox" / "browser" / "netguard.py"
        )
        origin, port = await self._origin()
        proxy = await netguard.serve(netguard.Guard(test_hosts=["127.0.0.1"]))
        proxy_port = proxy.sockets[0].getsockname()[1]
        try:
            answer = await self._through(
                proxy_port,
                f"GET http://127.0.0.1:{port}/x HTTP/1.1\r\nHost: a\r\n\r\n".encode(),
            )
            assert answer.startswith(b"HTTP/1.1 200")
            assert b"hello from the origin" in answer
        finally:
            proxy.close()
            origin.close()


class TestDrivers:
    def test_the_fake_and_local_browsers_are_refused_in_production(self, monkeypatch):
        from api import constants
        from api.services.browser import drivers

        monkeypatch.setattr(constants, "DEPLOYMENT_MODE", "production")
        for name in ("fake", "local"):
            monkeypatch.setattr(constants, "BROWSER_DRIVER", name)
            with pytest.raises(drivers.BrowserUnavailable):
                drivers.get()

    def test_production_without_a_sandbox_has_no_browser(self, monkeypatch):
        from api import constants
        from api.services.browser import drivers

        monkeypatch.setattr(constants, "BROWSER_DRIVER", "")
        monkeypatch.setattr(constants, "SANDBOX_URL", None)
        assert drivers.configured() == ""
        with pytest.raises(drivers.BrowserUnavailable):
            drivers.get()

    def test_box_lines_are_read_only_with_the_prefix(self):
        from api.services.browser import drivers

        assert drivers.decode(drivers.SENTINEL + '{"type": "hello"}') == {
            "type": "hello"
        }
        assert drivers.decode('{"type": "hello"}') is None
        assert drivers.decode(drivers.SENTINEL + "not json") is None

    def test_the_box_script_is_where_the_local_driver_looks(self):
        from api.services.browser import drivers

        assert drivers.BOX_SCRIPT.exists()
        source = drivers.BOX_SCRIPT.read_text()
        assert drivers.SENTINEL in source

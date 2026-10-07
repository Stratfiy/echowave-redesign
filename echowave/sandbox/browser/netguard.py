"""The browser box's own proxy: every request the page makes goes through it.

Chromium is started with ``--proxy-server`` pointing here and loopback not
bypassed, so a page's navigations, frames, scripts, images, fetches and
websockets all arrive at this one door. For each, before a byte leaves:

1. the port is a web port (80 or 443);
2. the host is not a private address or a bare hostname -- the same rule as
   the api's ``web_tools._is_private``, kept in step by
   ``api/tests/test_browser_sites.py``;
3. the host is not on the deny list the api sent (the staff's and the
   defaults, most specific rule first);
4. **every** address the name resolves to is public, and the connection is
   made to the address that was checked, so a name cannot resolve to a
   public address for the check and a private one for the connect.

A refusal is a 403 with a one-line page, which the browser shows and the
model reads. Standard library only: this file runs in the box.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable

WEB_PORTS = frozenset({80, 443})
BUFFER = 64 * 1024
REFUSED = (
    b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\nContent-Length: %d\r\n\r\n%s"
)


def is_private_host(host: str) -> bool:
    """A loopback, link-local or private address, or a bare hostname.
    Identical to ``api/services/workflow/web_tools._is_private``."""
    if not host or "." not in host and host != "localhost":
        return True
    if host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def covers(site: str, host: str) -> bool:
    return host == site or host.endswith("." + site)


class Guard:
    """The rules for one box: the deny list, and development's test hosts."""

    def __init__(
        self, rules: Iterable[dict] = (), test_hosts: Iterable[str] = ()
    ) -> None:
        self.rules = [
            (str(r.get("site") or "").lower(), str(r.get("rule") or ""))
            for r in rules
            if r.get("site")
        ]
        #: Development only (``BROWSER_TEST_HOSTS``): a page this machine
        #: serves. The sandbox never sends any.
        self.test_hosts = {h.strip().lower() for h in test_hosts if h.strip()}
        self.refused: list[str] = []

    def denied(self, host: str) -> bool:
        best: tuple[str, str] | None = None
        for site, rule in self.rules:
            if covers(site, host) and (best is None or len(site) > len(best[0])):
                best = (site, rule)
        return best is not None and best[1] == "deny"

    def allows_name(self, host: str, port: int) -> str | None:
        """None if the name may be tried, else why not."""
        host = host.lower().rstrip(".").strip("[]")
        if host in self.test_hosts:
            return None
        if port not in WEB_PORTS:
            return "only web ports are opened"
        if is_private_host(host):
            return "that address is not a page on the web"
        if self.denied(host.removeprefix("www.")):
            return "that site is not opened by Decibyl's browser"
        return None

    async def resolve(self, host: str, port: int) -> tuple[str | None, str]:
        """(an address to connect to, why not). Every address must be public."""
        host = host.lower().rstrip(".").strip("[]")
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=10
            )
        except (OSError, asyncio.TimeoutError):
            return None, "that name does not resolve"
        addresses = [info[4][0] for info in infos]
        if not addresses:
            return None, "that name does not resolve"
        if host not in self.test_hosts:
            for address in addresses:
                try:
                    public = ipaddress.ip_address(address.split("%", 1)[0]).is_global
                except ValueError:
                    public = False
                if not public:
                    return None, "that name points at a private address"
        return addresses[0], ""


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(BUFFER)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def _refuse(writer: asyncio.StreamWriter, why: str) -> None:
    body = (
        "<!doctype html><title>Not opened</title><p>Decibyl's browser does not "
        f"open this address: {why}.</p>"
    ).encode("utf-8")
    writer.write(REFUSED % (len(body), body))


def split_target(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = (
            target.rpartition(":") if target.count(":") == 1 else (target, "", "")
        )
    try:
        return host, int(port) if port else default_port
    except ValueError:
        return host, -1


async def _handle(
    guard: Guard, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        asyncio.TimeoutError,
        ConnectionError,
    ):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return
    if method.upper() == "CONNECT":
        host, port = split_target(target, 443)
        rest = b""
    else:
        if not target.lower().startswith("http://"):
            _refuse(writer, "only web addresses are opened")
            await writer.drain()
            writer.close()
            return
        hostport, _, path = target[len("http://") :].partition("/")
        host, port = split_target(hostport, 80)
        headers = [
            line
            for line in lines[1:]
            if line
            and not line.lower().startswith(
                ("proxy-connection:", "proxy-authorization:")
            )
        ]
        rest = (
            f"{method} /{path} {version}\r\n".encode("latin-1")
            + "\r\n".join(headers).encode("latin-1")
            + b"\r\n\r\n"
        )
    why = guard.allows_name(host, port)
    address = None
    if why is None:
        address, why = await guard.resolve(host, port)
    if address is None:
        guard.refused.append(host)
        _refuse(writer, why or "refused")
        await writer.drain()
        writer.close()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), timeout=20
        )
    except (OSError, asyncio.TimeoutError):
        _refuse(writer, "the site did not answer")
        await writer.drain()
        writer.close()
        return
    if method.upper() == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
    else:
        upstream_writer.write(rest)
        await upstream_writer.drain()
    await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


async def serve(
    guard: Guard, host: str = "127.0.0.1", port: int = 0
) -> asyncio.base_events.Server:
    """Start the proxy; ``server.sockets[0].getsockname()[1]`` is its port."""
    return await asyncio.start_server(
        lambda r, w: _handle(guard, r, w), host=host, port=port, limit=BUFFER
    )

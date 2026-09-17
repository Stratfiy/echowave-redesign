"""Where a URL somebody typed is allowed to point, when our servers will
fetch it.

Two classes of address, and the split matters.

**Blocked in every deployment mode.** The cloud-metadata service
(169.254.169.254 and the rest of link-local), multicast, the reserved
blocks and the unspecified address. None of these is ever a model server,
a webhook receiver or a lookup endpoint; the only thing a URL pointing at
one can do is make this process read its own instance credentials on a
customer's behalf. So there is no mode in which that is permitted.

This used to sit behind the SaaS check below, on the reasoning that a
self-hosted deployment is the operator's own box. It is -- and it is also
usually an EC2 instance with a metadata service on it. Production ran with
the default mode for long enough that the gate was a no-op there, and the
event-webhook endpoint accepted the metadata URL on the night it shipped.
The mode flag is still worth setting in SaaS; it is no longer the only
thing between a typed URL and the instance role.

**Blocked in SaaS only.** Private ranges, carrier-grade NAT, loopback and
the name "localhost". A self-hoster pointing STT at localhost:8000 or a LAN
model server is the normal OSS case and must keep working; a SaaS tenant
has no business reaching any of those from inside our network.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from api.constants import DEPLOYMENT_MODE

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """Refuse a URL that would turn a server-side fetch inward.

    Raises ``ValueError`` with a sentence naming the field, so the caller can
    put it in front of the person who typed the URL.
    """
    saas = DEPLOYMENT_MODE != "oss"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        if saas:
            raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")
        # OSS accepts a bare hostname for a model server ("api.elevenlabs.io",
        # "localhost:8443") and always has; the provider adds its own scheme.
        # Read the host off it anyway, so a bare metadata address is caught
        # the same as a schemed one, and if there is no host at all there is
        # nothing to check.
        parsed = urlparse(f"//{url}")
        if not parsed.hostname:
            return

    hostname = parsed.hostname

    if saas and hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    # A name that does not resolve cannot point anywhere yet. In SaaS that
    # is refused, as it always was -- the fetch is about to be made with a
    # credential on it and "unknown" is not "public". In OSS it is let
    # through: the check here is only that a resolvable name is not pointed
    # at the metadata service, and the fetch itself fails on DNS a moment
    # later in a way the operator can read.
    for ip in _resolve_hostname_ips(hostname, parsed.port, fail_closed=saas):
        if _is_blocked_everywhere(ip):
            raise ValueError(
                f"{field_name} cannot point at a link-local, multicast or "
                "reserved address"
            )
        if saas and _is_blocked_in_saas(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )


def _resolve_hostname_ips(
    hostname: str, port: int | None, *, fail_closed: bool
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        if fail_closed:
            raise ValueError("Could not resolve service URL hostname") from e
        return []

    return [ipaddress.ip_address(addr_info[4][0]) for addr_info in addr_infos]


def _is_blocked_everywhere(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Never a legitimate target, whoever owns the box.

    Loopback is answered first. Python files ``::1`` under ``is_reserved``
    (all of ``::/8`` is), and a runner that resolves ``localhost`` to both
    ``127.0.0.1`` and ``::1`` would otherwise refuse the IPv6 half of the one
    address OSS most needs. Loopback is the SaaS-only class, decided below.
    """
    if ip.is_loopback:
        return False
    return ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def _is_blocked_in_saas(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """The operator's own network, which a SaaS tenant has no business in."""
    return ip.is_private or ip.is_loopback or (ip.version == 4 and ip in _CGNAT_NETWORK)

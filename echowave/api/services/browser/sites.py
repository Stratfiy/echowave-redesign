"""Where the private browser may go.

Three rules, in this order, and the first that speaks decides:

1. **The address.** A page on the web, and nothing else: the same rule as
   ``web_tools.check_address`` (no loopback, link-local, private address or
   bare hostname), plus a DNS check that every address a name resolves to is
   public, because ``127.0.0.1.nip.io`` passes a check on the name alone.
   The box enforces this again at its own proxy for every request the page
   makes (``sandbox/browser/netguard.py``); this copy refuses early and says
   why on the panel.
2. **The staff's list.** ``browser_site_rules``: allow or deny a site and its
   subdomains, with a reason. The most specific rule wins, so staff can deny
   a whole domain and allow one subdomain of it.
3. **The default deny list.** Sites whose terms forbid automated access.
   Staff can allow one of them after reading its terms; the default is no.

A site on no list is allowed. That is deliberate (api/AGENTS.md, "Silent
absence"): an allowlist of the web would refuse most of what a person asks
for with nothing to show for it, while a deny list's worst case is a site
somebody notices and adds.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from api.services.workflow import web_tools

ALLOW = "allow"
DENY = "deny"

#: Sites whose terms of use forbid automated access, refused unless staff
#: allow one. The social networks are the founder's rule as well as their
#: terms (decided 21 Sept 2026, see web_tools.BLOCKED_DOMAINS); the rest say
#: so in their own terms.
DEFAULT_DENY: dict[str, str] = {
    **{
        domain: "Its terms forbid automated access, and Decibyl does not use social networks for anyone."
        for domain in web_tools.BLOCKED_DOMAINS
    },
    "craigslist.org": "Its terms of use forbid robots and automated access.",
    "ticketmaster.com": "Its terms of use forbid automated ticket buying.",
    "ticketmaster.co.in": "Its terms of use forbid automated ticket buying.",
}

#: Where a search goes when the browser searches. Always reachable for a
#: search, even when the task names its sites.
SEARCH_ENGINES = ("duckduckgo.com", "google.com", "bing.com")


@dataclass(frozen=True)
class Rule:
    site: str
    rule: str
    reason: str = ""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    #: ``address`` | ``staff`` | ``default`` | ``off_task`` | ``""``
    source: str = ""


def host_of(url: str) -> str:
    return web_tools.domain_of(url)


def normalise_site(raw: str) -> str:
    """A site as a bare host: lower-case, no scheme, path, port or ``www.``."""
    sites = web_tools.normalise_domains([raw])
    host = sites[0] if sites else ""
    return host.split(":", 1)[0]


def covers(site: str, host: str) -> bool:
    return host == site or host.endswith("." + site)


def _match(host: str, rules: Iterable[Rule]) -> Rule | None:
    best: Rule | None = None
    for rule in rules:
        if covers(rule.site, host) and (
            best is None or len(rule.site) > len(best.site)
        ):
            best = rule
    return best


def decide(url: str, rules: Iterable[Rule] = ()) -> Decision:
    """Whether the browser may open ``url``, without touching the network."""
    try:
        web_tools.check_address(url)
    except web_tools.FetchRefused as exc:
        return Decision(False, str(exc), "address")
    host = host_of(url)
    staff = _match(host, rules)
    if staff is not None:
        if staff.rule == DENY:
            return Decision(
                False,
                f"{host} is not opened: {staff.reason or 'staff have turned it off'}",
                "staff",
            )
        return Decision(True, "", "staff")
    for site, reason in DEFAULT_DENY.items():
        if covers(site, host):
            return Decision(False, f"{host} is not opened: {reason}", "default")
    return Decision(True)


def on_task(url: str, sites: Iterable[str], *, searching: bool = False) -> bool:
    """Whether ``url`` is on one of the task's sites (or a search engine, for
    a search). A task with no sites named can go anywhere ``decide`` allows."""
    wanted = [s for s in sites if s]
    if not wanted:
        return True
    host = host_of(url)
    if any(covers(site, host) for site in wanted):
        return True
    return searching and any(covers(engine, host) for engine in SEARCH_ENGINES)


def _public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_global
    except ValueError:
        return False


async def resolves_public(host: str, *, timeout: float = 5.0) -> bool:
    """Every address ``host`` resolves to is a public one. A name that does
    not resolve is not public either: there is nothing to open."""
    host = (host or "").strip().strip("[]").lower()
    if not host:
        return False
    if web_tools.is_private_host(host):
        return False
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(_public(a) for a in addresses)


async def staff_rules() -> list[Rule]:
    from api.db import db_client

    return [
        Rule(site=row.site, rule=row.rule, reason=row.reason or "")
        for row in await db_client.list_browser_site_rules()
    ]


def effective_list(rules: Iterable[Rule]) -> list[dict[str, str]]:
    """The defaults with the staff's rules laid over them, for the staff
    screen and for the box's proxy. Every default shows, allowed or not, so
    nothing on the list is invisible."""
    staff = {r.site: r for r in rules}
    out: list[dict[str, str]] = []
    for site, reason in sorted(DEFAULT_DENY.items()):
        if site in staff:
            continue
        out.append({"site": site, "rule": DENY, "reason": reason, "source": "default"})
    for site, rule in sorted(staff.items()):
        out.append(
            {"site": site, "rule": rule.rule, "reason": rule.reason, "source": "staff"}
        )
    return out


def site_of_url(url: str) -> str:
    """The host to file a login under: the page's host without ``www.``."""
    return normalise_site(urlsplit(url).hostname or "")

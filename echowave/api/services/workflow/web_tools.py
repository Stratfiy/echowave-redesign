"""Decibyl reaches the web (D-1b): a search on the platform's key, a page.

Two tools, both reads that run in the turn, both bounded the way every
other read is.

**Search** goes to Serper on a key we hold (the ``data`` credential row, or
``SERPER_API_KEY``). Each search is a tool-call event plus the vendor's price
passed through at cost -- see ``billing/data_costs.py`` -- which is the
exchange-table rule for anything bought on our key. A workspace on its own
search connector pays the event alone, through the connected-apps path, not
this one.

**Fetch** reads one page and hands back its text. It is our own fetcher,
not a crawler: ``robots.txt`` is honoured, a domain is read at most a few
times a minute across the whole platform, the social networks are refused
outright (decided 21 Sept 2026: no LinkedIn scraping, ever), the body is
cut at a size a model can use, and a page that is mostly script comes back
as the little text it has rather than a pretend article. When a Crawl4AI
server is deployed (``CRAWL4AI_URL``) the page goes through it for a
cleaner reduction; the rules above hold either way, because they are ours.

**Bounded per run, and to a list when the agent has one (OP-2).** A run
reads at most ``WEB_FETCH_MAX_PAGES_PER_RUN`` pages, whatever the model
asks for, and an agent whose web tool carries an allow-list reads and
searches those domains only. A fetch can say what it is looking for, and
gets the parts of the page that match first rather than the first twelve
thousand characters of a homepage; and every page comes back with the
email addresses and phone numbers it carries, which is what a prospecting
agent opened the contact page for.
"""

from __future__ import annotations

import html
import ipaddress
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib import robotparser
from urllib.parse import urlsplit

import httpx
from loguru import logger

from api import constants
from api.enums import CostComponent

SEARCH_TOOL_NAME = "web_search"
FETCH_TOOL_NAME = "web_fetch"

SEARCH_PROVIDER = "serper"
SEARCH_KIND = "search"
SERPER_URL = "https://google.serper.dev/search"

USER_AGENT = "DecibylBot/1.0 (+https://decibyl.ai/bot)"
TIMEOUT_SECS = 20.0
MAX_RESULTS = 10
MAX_BODY_BYTES = 2_000_000
RUN_BUCKET = "web_fetch_run"
RUN_WINDOW_SECS = 24 * 60 * 60
MAX_FOUND = 10

#: Refused outright, whatever robots.txt says. The social networks' terms
#: forbid it and the founder decided it; a page behind a login is not ours
#: to read.
BLOCKED_DOMAINS = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "threads.net",
        "snapchat.com",
    }
)


def enabled() -> bool:
    return constants.DECIBYL_TOOLS_2026_09_ENABLED


def search_tool_schema() -> dict[str, Any]:
    return {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the web. Runs now, on Decibyl's own search key; each "
            "search costs a tool call plus the search itself. Use it for "
            "anything outside the workspace -- a company, a price, a news "
            "item, a phone number to verify. Comes back as titles, links and "
            "snippets; read a page with web_fetch when a snippet is not "
            "enough."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "count": {
                    "type": "integer",
                    "description": f"How many results, 1 to {MAX_RESULTS}. Default 5.",
                },
                "country": {
                    "type": "string",
                    "description": "Two-letter country code to search from, e.g. in.",
                },
            },
            "required": ["query"],
        },
    }


def fetch_tool_schema() -> dict[str, Any]:
    return {
        "name": FETCH_TOOL_NAME,
        "description": (
            "Read one web page and get its text. Runs now; one tool call. "
            "Honours the site's robots rules, never reads the social "
            "networks, and cuts the text at what fits, so ask for a specific "
            "page rather than a homepage when you can."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The page's full address."},
                "looking_for": {
                    "type": "string",
                    "description": (
                        "What you want from the page, in a few words (e.g. "
                        "'opening hours', 'who runs it', 'pricing'). The "
                        "parts that match come first; leave it out to read "
                        "the page from the top."
                    ),
                },
            },
            "required": ["url"],
        },
    }


# --- search -----------------------------------------------------------------


async def _search_key() -> str | None:
    """The platform's search key: the credential row first, then the env."""
    try:
        from api.db import db_client
        from api.services.configuration import platform_credentials

        async with db_client.async_session() as session:
            key = await platform_credentials.resolve_api_key(
                session, component=CostComponent.DATA, provider=SEARCH_PROVIDER
            )
        if key:
            return key
    except Exception as exc:  # noqa: BLE001 - the env fallback still stands
        logger.warning("Could not read the {} key: {}", SEARCH_PROVIDER, exc)
    return constants.SERPER_API_KEY or None


async def search(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    ref_id: str,
    workflow_id: int | None = None,
    allowed_domains: Iterable[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering.

    ``allowed_domains`` (OP-2) narrows the search to those sites, both in
    the query sent and in the results kept, so an agent kept to a list
    never sees a page it could not then read."""
    query = str(arguments.get("query") or "").strip()[:400]
    if not query:
        return {"status": "error", "error": "Say what to search for."}
    domains = normalise_domains(allowed_domains)
    if domains:
        sites = " OR ".join(f"site:{d}" for d in domains)
        query = (
            f"{query} ({sites})" if len(domains) > 1 else f"{query} site:{domains[0]}"
        )
    try:
        count = max(1, min(int(arguments.get("count") or 5), MAX_RESULTS))
    except (TypeError, ValueError):
        count = 5
    country = str(arguments.get("country") or "").strip().lower()[:2]

    key = await _search_key()
    if not key:
        return {
            "status": "unavailable",
            "reason": (
                "No search key is set up on this deployment. Ask an operator "
                "to add a Serper key under provider keys, component data."
            ),
        }
    body: dict[str, Any] = {"q": query, "num": count}
    if country:
        body["gl"] = country
    try:
        async with _client(client) as http:
            response = await http.post(
                SERPER_URL,
                json=body,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Search failed for org {}: {}", organization_id, exc)
        return {
            "status": "error",
            "error": f"The search vendor answered {exc.response.status_code}.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Search failed for org {}: {}", organization_id, exc)
        return {"status": "error", "error": "The search did not answer just now."}

    results = [
        {
            "title": str(item.get("title") or "")[:200],
            "link": str(item.get("link") or ""),
            "snippet": str(item.get("snippet") or "")[:500],
        }
        for item in (payload.get("organic") or [])
        if isinstance(item, dict)
        and (not domains or on_list(str(item.get("link") or ""), domains))
    ][:count]
    answer = (
        payload.get("answerBox") if isinstance(payload.get("answerBox"), dict) else None
    )

    # The fee and the pass-through, both keyed on the call so a retried turn
    # pays once.
    from api.services.billing import data_costs
    from api.services.billing import events as billing_events

    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.TOOL_CALL,
        ref_id=ref_id,
        note=f"web search: {query[:60]}",
        workflow_id=workflow_id,
    )
    await data_costs.debit_lookup_in_own_session(
        organization_id=organization_id,
        provider=SEARCH_PROVIDER,
        kind=SEARCH_KIND,
        requests=1,
        ref_id=ref_id,
        workflow_id=workflow_id,
    )
    out: dict[str, Any] = {"status": "success", "query": query, "results": results}
    if answer:
        out["answer"] = {
            k: str(answer.get(k) or "")[:500]
            for k in ("title", "answer", "snippet")
            if answer.get(k)
        }
    if not results:
        out["note"] = "Nothing came back for that; try other words."
    return out


# --- fetch ------------------------------------------------------------------


class FetchRefused(ValueError):
    """Told to the model in its own words; never raised past the tool."""


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def is_blocked(url: str) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def _is_private(host: str) -> bool:
    """A loopback, link-local or private address, or a bare hostname: not a
    page on the web, and a fetcher that reaches it is reading our own
    network for whoever types the address."""
    if not host or "." not in host and host != "localhost":
        return True
    if host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def check_url(url: str) -> str:
    """The URL as it will be fetched, or a refusal."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchRefused("Give a full web address starting with http or https.")
    if is_blocked(url):
        raise FetchRefused(
            f"{domain_of(url)} is not read: the social networks are off limits, by rule."
        )
    if _is_private(parts.hostname.lower()):
        raise FetchRefused("That address is not a page on the web.")
    return url


def normalise_domains(domains: Iterable[str] | None) -> list[str]:
    """An allow-list as hosts: lower-case, no scheme, no path, no ``www.``,
    blanks dropped, order kept, each once."""
    out: list[str] = []
    for raw in domains or ():
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if "://" in text:
            text = urlsplit(text).hostname or ""
        else:
            text = text.split("/", 1)[0]
        text = text.strip().rstrip(".").removeprefix("www.")
        if text and text not in out:
            out.append(text)
    return out


def on_list(url: str, domains: list[str]) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)


async def _run_allows(run_key: str | None, max_pages: int | None) -> tuple[bool, int]:
    """Whether this run may read one more page: (allowed, cap). A run with
    no key is uncounted, which is only the case in a test."""
    if not run_key:
        return True, 0
    cap = int(max_pages or constants.WEB_FETCH_MAX_PAGES_PER_RUN)
    if cap <= 0:
        cap = constants.WEB_FETCH_MAX_PAGES_PER_RUN
    from api.services.rate_limit import rate_limiter

    allowed, _ = await rate_limiter.check(
        bucket=RUN_BUCKET, identity=run_key, limit=cap, window_secs=RUN_WINDOW_SECS
    )
    return allowed, cap


_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "and",
        "or",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "its",
        "what",
        "who",
        "how",
        "where",
        "when",
        "which",
        "their",
        "our",
        "your",
        "about",
        "page",
    ]
)


def _terms(looking_for: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (looking_for or "").lower())
    return [w for w in words if len(w) > 1 and w not in _STOP]


def focus(text: str, looking_for: str, limit: int) -> tuple[str, int]:
    """The page's lines, the ones that speak to ``looking_for`` first.

    Lexical, not a model: a line scores by the distinct terms it carries, a
    matching line brings its neighbours so a heading keeps its paragraph,
    and the kept lines are laid out in page order up to ``limit``. Returns
    (text, matched_lines); with no match the caller falls back to the top
    of the page and says so."""
    terms = _terms(looking_for)
    lines = text.split("\n")
    if not terms or not lines:
        return text[:limit], 0
    scores = [sum(1 for t in terms if t in ln.lower()) for ln in lines]
    matched = sum(1 for sc in scores if sc)
    if not matched:
        return text[:limit], 0
    # A matching line brings its neighbours, the one after it first: a
    # heading that matches is followed by the paragraph it heads.
    weight: dict[int, float] = {}
    for i, sc in enumerate(scores):
        if not sc:
            continue
        weight[i] = max(weight.get(i, 0.0), float(sc))
        if i + 1 < len(lines):
            weight[i + 1] = max(weight.get(i + 1, 0.0), sc * 0.5)
        if i > 0:
            weight[i - 1] = max(weight.get(i - 1, 0.0), sc * 0.25)
    # Best lines first, so the cut falls on the weakest when there are
    # more than fit; then back into page order.
    ordered = sorted(weight, key=lambda i: (-weight[i], i))
    chosen: list[int] = []
    used = 0
    for i in ordered:
        cost = len(lines[i]) + 1
        if used + cost > limit:
            continue
        chosen.append(i)
        used += cost
    chosen.sort()
    return "\n".join(lines[i] for i in chosen), matched


_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w/])(?:\+?\d[\d\s().-]{7,}\d)(?![\w/])")


def found_on(text: str) -> dict[str, list[str]]:
    """The email addresses and phone numbers a page shows, each once, at
    most ``MAX_FOUND`` of each. What a business publishes on its own page
    is what a prospecting agent came for; it is never a guess."""
    emails: list[str] = []
    for m in _EMAIL.findall(text):
        e = m.lower().rstrip(".")
        if e not in emails and not e.endswith((".png", ".jpg", ".svg", ".gif")):
            emails.append(e)
    phones: list[str] = []
    for m in _PHONE.findall(text):
        digits = re.sub(r"\D", "", m)
        if not 8 <= len(digits) <= 15:
            continue
        shown = re.sub(r"\s+", " ", m.strip())
        if shown not in phones:
            phones.append(shown)
    out: dict[str, list[str]] = {}
    if emails:
        out["emails"] = emails[:MAX_FOUND]
    if phones:
        out["phones"] = phones[:MAX_FOUND]
    return out


async def _robots_allow(url: str, http: httpx.AsyncClient) -> bool:
    """Whether the site's robots.txt lets us read this page. A robots file
    that cannot be read allows; one that reads and says no, refuses."""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = await http.get(robots_url)
    except Exception:  # noqa: BLE001 - no robots file is the common case
        return True
    if response.status_code >= 400:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url) and parser.can_fetch("*", url)


async def _domain_allowed(host: str) -> tuple[bool, int]:
    from api.services.rate_limit import rate_limiter

    return await rate_limiter.check(
        bucket="web_fetch_domain",
        identity=host,
        limit=constants.WEB_FETCH_PER_DOMAIN_PER_MINUTE,
        window_secs=60,
    )


class _Text(HTMLParser):
    """Strip a page to its readable text: no script, style or markup, one
    line per block, headings and links kept as text."""

    SKIP: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "head",
    }
    BLOCK: ClassVar[set[str]] = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "table",
        "ul",
        "ol",
        "pre",
        "blockquote",
        "dd",
        "dt",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self._skip:
            self.parts.append(data)


def text_of(page: str) -> tuple[str, str]:
    """(title, text) from HTML: one block per line, single spaces, no
    blank lines."""
    parser = _Text()
    try:
        parser.feed(page)
    except Exception as exc:  # noqa: BLE001 - a broken page still has words in it
        logger.debug("Page did not parse cleanly; keeping what was read: {}", exc)
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
    return html.unescape(parser.title).strip()[:200], "\n".join(
        ln for ln in lines if ln
    )


def _client(client: httpx.AsyncClient | None) -> httpx.AsyncClient:
    if client is not None:
        return _Borrowed(client)
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECS,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.5",
        },
    )


class _Borrowed:
    """A caller's client used inside ``async with`` without being closed."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def _via_crawl4ai(url: str, http: httpx.AsyncClient) -> tuple[str, str] | None:
    """Crawl4AI's reduction of the page, when a server is configured."""
    if not constants.CRAWL4AI_URL:
        return None
    try:
        response = await http.post(
            f"{constants.CRAWL4AI_URL}/crawl", json={"urls": [url], "priority": 10}
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") or []
        first = results[0] if results else payload
        markdown = first.get("markdown") or first.get("fit_markdown") or ""
        if isinstance(markdown, dict):
            markdown = (
                markdown.get("fit_markdown") or markdown.get("raw_markdown") or ""
            )
        title = str((first.get("metadata") or {}).get("title") or "")[:200]
        return (title, str(markdown)) if markdown else None
    except Exception as exc:  # noqa: BLE001 - fall back to our own reduction
        logger.warning("Crawl4AI could not read {}: {}", url, exc)
        return None


async def fetch(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    ref_id: str,
    workflow_id: int | None = None,
    run_key: str | None = None,
    max_pages: int | None = None,
    allowed_domains: Iterable[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """The tool call. Never raises.

    ``run_key`` counts this read against the run's page cap (OP-2);
    ``max_pages`` is the agent's own lower cap when it has one;
    ``allowed_domains`` keeps the read to the agent's list."""
    try:
        url = check_url(str(arguments.get("url") or ""))
    except FetchRefused as exc:
        return {"status": "refused", "reason": str(exc)}
    host = domain_of(url)
    domains = normalise_domains(allowed_domains)
    if domains and not on_list(url, domains):
        return {
            "status": "refused",
            "reason": (
                f"{host} is not on this agent's list of sites; it reads "
                + ", ".join(domains[:8])
                + " only."
            ),
        }
    run_ok, cap = await _run_allows(run_key, max_pages)
    if not run_ok:
        return {
            "status": "refused",
            "reason": (
                f"This run has read its {cap} pages; answer with what you "
                "have rather than reading more."
            ),
        }
    allowed, retry_after = await _domain_allowed(host)
    if not allowed:
        return {
            "status": "refused",
            "reason": f"{host} has been read enough this minute; try again in {retry_after}s.",
        }
    try:
        async with _client(client) as http:
            if not await _robots_allow(url, http):
                return {
                    "status": "refused",
                    "reason": f"{host} asks not to be read by bots on that page (robots.txt).",
                }
            reduced = await _via_crawl4ai(url, http)
            if reduced is None:
                response = await http.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                body = response.content[:MAX_BODY_BYTES].decode(
                    response.encoding or "utf-8", errors="replace"
                )
                if "html" in content_type or body.lstrip()[:1] == "<":
                    reduced = text_of(body)
                else:
                    reduced = ("", body.strip())
    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "error": f"{host} answered {exc.response.status_code}.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch {} for org {}: {}", url, organization_id, exc)
        return {"status": "error", "error": f"{host} could not be read just now."}

    title, text = reduced
    found = found_on(text)
    looking_for = str(arguments.get("looking_for") or "").strip()[:200]
    matched = 0
    if looking_for:
        text, matched = focus(text, looking_for, constants.WEB_FETCH_MAX_CHARS)
    cut = len(text) > constants.WEB_FETCH_MAX_CHARS
    text = text[: constants.WEB_FETCH_MAX_CHARS]

    from api.services.billing import events as billing_events

    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.TOOL_CALL,
        ref_id=ref_id,
        note=f"web fetch: {host}",
        workflow_id=workflow_id,
    )
    out: dict[str, Any] = {
        "status": "success",
        "url": url,
        "title": title,
        "text": text,
    }
    if found:
        out["found"] = found
    if looking_for:
        out["looking_for"] = looking_for
        out["note"] = (
            f"{matched} line(s) speak to '{looking_for}'; those and their "
            "neighbours are shown, in page order."
            if matched
            else f"Nothing on the page matches '{looking_for}'; showing it from the top."
        )
    if cut:
        out["note"] = f"Cut at {constants.WEB_FETCH_MAX_CHARS} characters."
    if not text:
        out["note"] = "The page has almost no readable text; it may be built by script."
    return out

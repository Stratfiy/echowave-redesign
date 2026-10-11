"""Learn a website: read a site's pages that matter and keep them as a file.

The thing every competitor sells as "live in nine minutes" -- paste your
website and the agent knows your hours, prices and services -- and the thing
anyone wants of an assistant: "read this site and remember it".

One tool, ``learn_website``. It reads the page it is given and up to a few
more pages of the same site, chosen by what a business's customers ask about
(contact, hours, prices, services, menu, FAQ...) rather than in link order.
The text of every page goes into one Markdown file in Files, which is read
and indexed like any upload. Kept for the agents (the default) it is company
knowledge -- scope ``org`` -- which every agent answers from on calls and in
chats without a step naming it. Kept for the person alone it stays in Files,
where Decibyl reads it like any other file.

Learning the same site again is the same file, newer: it becomes the next
version of the file already there (``versions``), so a business that changed
its prices says "learn my website again" and every agent has the new ones.

The fetcher's rules are ``web_tools``': robots.txt is honoured, the social
networks and private addresses are refused, a domain is read at most a few
times a minute, and a redirect that leaves the site is not followed into the
file. A page that cannot be read is listed as skipped, never silently lost.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from loguru import logger

from api.services import features
from api.services.workflow import web_tools

TOOL_NAME = "learn_website"
FLAG = "learn_website"

#: Pages read in one go, the one given included. Under the per-domain rate
#: (``WEB_FETCH_PER_DOMAIN_PER_MINUTE``, ten) so one learn never trips it.
MAX_PAGES = 8
#: Text kept per page, and for the whole file. A business site's useful text
#: fits well inside both; a blog's archive does not need to.
PAGE_CHARS = 8_000
FILE_CHARS = 60_000
#: What the model is handed back to use in this turn (the file holds all).
EXCERPT_CHARS = 6_000
PAGE_TIMEOUT_SECS = 12.0

#: What customers ask a business about, best first. A link whose address or
#: words carry one of these is read before one that does not.
WANTED: tuple[str, ...] = (
    "contact",
    "about",
    "hour",
    "timing",
    "price",
    "pricing",
    "fee",
    "rate",
    "tariff",
    "service",
    "menu",
    "faq",
    "question",
    "product",
    "shop",
    "course",
    "treatment",
    "doctor",
    "team",
    "room",
    "book",
    "appointment",
    "location",
    "branch",
    "deliver",
    "shipping",
    "return",
    "refund",
    "policy",
    "offer",
)
#: Pages that are never what a customer asks about, or that act when read.
SKIPPED_PATHS = re.compile(
    r"(login|log-in|signin|sign-in|signup|sign-up|register|account|cart|"
    r"checkout|basket|wishlist|wp-admin|wp-login|logout|search|feed|"
    r"/tag/|/tags/|/author/|/page/\d|cdn-cgi)",
    re.IGNORECASE,
)
NOT_PAGES = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|ico|mp4|mp3|wav|zip|rar|docx?|xlsx?|"
    r"pptx?|css|js|json|xml|rss)$",
    re.IGNORECASE,
)
#: A line this short seen on an earlier page is the menu or the footer again.
REPEATED_LINE_CHARS = 120


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Learn a website: read the page given and up to "
            f"{MAX_PAGES - 1} more pages of the same site (contact, hours, "
            "prices, services, menu, FAQ first) and keep them as one file in "
            "Files. Kept for the agents, every agent answers from it on "
            "calls and in chats. Runs now; one tool call per page read. "
            "Comes back with what the site says, so use it straight away -- "
            "for example to fill an agent's answers instead of asking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The site's address, e.g. https://example.in",
                },
                "for_agents": {
                    "type": "boolean",
                    "description": (
                        "True (default) when it is the person's own business "
                        "site: every agent answers from it. False to keep it "
                        "in Files for the person only -- a site they want "
                        "read and remembered, not their business."
                    ),
                },
            },
            "required": ["url"],
        },
    }


# --- reading ----------------------------------------------------------------


class _Links(HTMLParser):
    """Every ``<a href>`` on a page, with the words it is shown as."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._words: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._words = []

    def handle_data(self, data):
        if self._href is not None:
            self._words.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._words).split())))
            self._href = None


def _canonical(url: str) -> str:
    """One address per page: no fragment, no query, no trailing slash."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


def same_site(url: str, site: str) -> bool:
    host = web_tools.domain_of(url)
    return host == site or host.endswith("." + site)


def links_of(page_url: str, page: str, site: str) -> list[tuple[str, str]]:
    """The same site's pages linked from ``page``, once each, as
    (address, link words). Files, scripts and account pages are left out."""
    parser = _Links()
    try:
        parser.feed(page)
    except Exception as exc:  # noqa: BLE001 - a broken page still has links
        logger.debug("Links of {} did not parse cleanly: {}", page_url, exc)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for href, words in parser.links:
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _canonical(urljoin(page_url, href))
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not same_site(url, site):
            continue
        if NOT_PAGES.search(parts.path) or SKIPPED_PATHS.search(parts.path):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append((url, words))
    return out


def ranked(links: list[tuple[str, str]], *, skip: set[str]) -> list[str]:
    """Links in the order to read them: what customers ask about first (by
    how early its word is in ``WANTED``), then shallow pages before deep."""

    def score(item: tuple[int, tuple[str, str]]) -> tuple[int, int, int]:
        position, (url, words) = item
        said = f"{urlsplit(url).path} {words}".lower()
        hit = next((i for i, w in enumerate(WANTED) if w in said), len(WANTED))
        depth = urlsplit(url).path.strip("/").count("/")
        return hit, depth, position

    order = sorted(enumerate(links), key=score)
    return [url for _, (url, _w) in order if url not in skip]


@dataclass
class Page:
    url: str
    title: str
    text: str


@dataclass
class Site:
    url: str
    host: str
    pages: list[Page] = field(default_factory=list)
    #: (address, why) for each page that was chosen and could not be read.
    skipped: list[tuple[str, str]] = field(default_factory=list)


class SiteRefused(ValueError):
    """The site as a whole cannot be read; said to the model as is."""


async def _robots(start: str, http: httpx.AsyncClient):
    """The site's robots rules, read once for the whole learn. None when
    there are none to read, which allows."""
    parts = urlsplit(start)
    try:
        response = await http.get(
            f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=PAGE_TIMEOUT_SECS
        )
    except Exception:  # noqa: BLE001 - no robots file is the common case
        return None
    if response.status_code >= 400:
        return None
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser


def _robots_allow(rules, url: str) -> bool:
    if rules is None:
        return True
    return rules.can_fetch(web_tools.USER_AGENT, url) and rules.can_fetch("*", url)


async def _get(url: str, site: str, http: httpx.AsyncClient) -> tuple[str, str]:
    """(final address, HTML) of one page, or a reason it was not read."""
    allowed, retry_after = await web_tools._domain_allowed(site)
    if not allowed:
        raise SiteRefused(f"read enough this minute; try again in {retry_after}s")
    response = await http.get(url, timeout=PAGE_TIMEOUT_SECS)
    response.raise_for_status()
    final = str(response.url)
    host = urlsplit(final).hostname or ""
    if web_tools.is_private_host(host):
        raise SiteRefused("it redirected to an address that is not on the web")
    if not same_site(final, site):
        raise SiteRefused(
            f"it redirected off the site, to {web_tools.domain_of(final)}"
        )
    content_type = response.headers.get("content-type", "")
    body = response.content[: web_tools.MAX_BODY_BYTES].decode(
        response.encoding or "utf-8", errors="replace"
    )
    if "html" not in content_type and body.lstrip()[:1] != "<":
        raise SiteRefused("it is not a web page")
    return final, body


async def read_site(
    url: str,
    *,
    max_pages: int = MAX_PAGES,
    client: httpx.AsyncClient | None = None,
) -> Site:
    """Read the page at ``url`` and the best of the same site's pages it
    links to. Raises ``SiteRefused`` only when the first page cannot be read;
    a later page that fails is recorded in ``skipped``."""
    url = (url or "").strip()
    if url and "://" not in url:
        # "lotusdental.in" is how people write their site.
        url = f"https://{url}"
    try:
        start = web_tools.check_url(url)
    except web_tools.FetchRefused as exc:
        raise SiteRefused(str(exc)) from exc
    site = web_tools.domain_of(start)
    out = Site(url=start, host=site)
    async with web_tools._client(client) as http:
        rules = await _robots(start, http)
        if not _robots_allow(rules, start):
            raise SiteRefused(
                f"{site} asks not to be read by agents on that page (robots.txt)."
            )
        try:
            final, first = await _get(start, site, http)
        except SiteRefused as exc:
            raise SiteRefused(f"{site} could not be read: {exc}.") from exc
        except httpx.HTTPStatusError as exc:
            raise SiteRefused(
                f"{site} answered {exc.response.status_code}; check the address."
            ) from exc
        except Exception as exc:
            logger.warning("Could not read {}: {}", start, exc)
            raise SiteRefused(f"{site} could not be read just now.") from exc
        title, text = web_tools.text_of(first)
        out.pages.append(Page(url=_canonical(final), title=title, text=text))

        read = {_canonical(start), _canonical(final)}
        chosen: list[str] = []
        for candidate in ranked(links_of(final, first, site), skip=read):
            if len(chosen) >= max_pages - 1:
                break
            if not _robots_allow(rules, candidate):
                out.skipped.append((candidate, "the site asks agents not to read it"))
                continue
            chosen.append(candidate)

        async def one(page_url: str) -> Page | tuple[str, str]:
            try:
                landed, body = await _get(page_url, site, http)
            except SiteRefused as exc:
                return page_url, str(exc)
            except httpx.HTTPStatusError as exc:
                return page_url, f"it answered {exc.response.status_code}"
            except Exception as exc:  # noqa: BLE001 - one page, not the site
                logger.debug("Could not read {}: {}", page_url, exc)
                return page_url, "it could not be read just now"
            page_title, page_text = web_tools.text_of(body)
            return Page(url=_canonical(landed), title=page_title, text=page_text)

        for result in await asyncio.gather(*(one(u) for u in chosen)):
            if isinstance(result, Page):
                if result.url not in {p.url for p in out.pages}:
                    out.pages.append(result)
            else:
                out.skipped.append(result)
    return out


# --- the file ---------------------------------------------------------------


def _without_repeats(pages: list[Page]) -> list[Page]:
    """Each page's text without the short lines an earlier page already had
    -- the menu, the footer, the cookie notice, said once rather than eight
    times -- cut to ``PAGE_CHARS``."""
    seen: set[str] = set()
    out: list[Page] = []
    for page in pages:
        kept: list[str] = []
        for line in page.text.split("\n"):
            key = line.strip().lower()
            if not key:
                continue
            if len(key) <= REPEATED_LINE_CHARS:
                if key in seen:
                    continue
                seen.add(key)
            kept.append(line.strip())
        out.append(Page(page.url, page.title, "\n".join(kept)[:PAGE_CHARS]))
    return out


def as_markdown(site: Site, *, read_on: datetime | None = None) -> str:
    """The file: the site's name and address, when it was read, then one
    section per page with its address -- so an answer can say where a fact
    came from."""
    when = (read_on or datetime.now(UTC)).strftime("%d %b %Y")
    name = (site.pages[0].title if site.pages else "") or site.host
    lines = [
        f"# {name}",
        "",
        f"Website: {site.url}",
        f"Read on {when}, {len(site.pages)} page(s).",
    ]
    for page in _without_repeats(site.pages):
        if not page.text:
            continue
        lines += ["", f"## {page.title or page.url}", "", f"Page: {page.url}", ""]
        lines.append(page.text)
    return "\n".join(lines)[:FILE_CHARS].rstrip() + "\n"


def found_in(site: Site) -> dict[str, list[str]]:
    """The email addresses and phone numbers the site gives, once each."""
    out: dict[str, list[str]] = {}
    for page in site.pages:
        for kind, values in web_tools.found_on(page.text).items():
            bucket = out.setdefault(kind, [])
            for value in values:
                if value not in bucket:
                    bucket.append(value)
    return {k: v[: web_tools.MAX_FOUND] for k, v in out.items() if v}


def filename_for(site: Site) -> str:
    return f"Website - {site.host}.md"


async def _owner(organization_id: int, author_id: int | None) -> tuple[int, str]:
    """(user id, provider id) of who the file is filed under: the person who
    asked when they are a member here, else the workspace's first member."""
    from api.db import db_client

    users = await db_client.get_organization_users(organization_id)
    if not users:
        raise SiteRefused("This workspace has no member to own the file.")
    user = next((u for u in users if author_id and u.id == author_id), users[0])
    return int(user.id), str(getattr(user, "provider_id", "") or "")


async def keep(
    organization_id: int,
    site: Site,
    *,
    for_agents: bool,
    author_id: int | None = None,
) -> dict[str, Any]:
    """Store the site as a file and start reading it; a site learnt before
    becomes the next version of its file. Returns the file's uuid and name,
    and whether it was new."""
    from api.db import db_client
    from api.services import storage
    from api.services.knowledge_base import upload_keys, versions
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    user_id, provider_id = await _owner(organization_id, author_id)
    filename = filename_for(site)
    data = as_markdown(site).encode("utf-8")
    scope = "org" if for_agents else "library"
    previous = await db_client.find_document_by_name_in_file_folder(
        organization_id=organization_id,
        filename=filename,
        file_folder_id=None,
        scope=scope,
    )
    document_uuid = previous.document_uuid if previous else str(uuid.uuid4())
    # A new version is stored under a key of its own, as a re-upload is.
    key = upload_keys.build_document_key(
        organization_id,
        document_uuid if previous is None else str(uuid.uuid4()),
        filename,
    )
    if not await storage.storage_fs.acreate_file_from_bytes(key, data):
        raise SiteRefused("The file could not be stored just now.")
    pages = [{"url": p.url, "title": p.title} for p in site.pages]
    if previous is not None:
        document = await versions.begin(
            previous,
            organization_id=organization_id,
            s3_key=key,
            user_id=user_id,
            retrieval_mode="chunked",
        )
        await db_client.merge_document_custom_metadata(
            previous.id,
            organization_id=organization_id,
            patch={"pages": pages, "read_at": datetime.now(UTC).isoformat()},
        )
    else:
        document = await db_client.create_document(
            organization_id=organization_id,
            created_by=user_id,
            filename=filename,
            file_size_bytes=len(data),
            file_hash=hashlib.sha256(data).hexdigest(),
            mime_type="text/markdown",
            source_url=site.url,
            custom_metadata={
                "s3_key": key,
                "source": "website",
                "site": site.host,
                "pages": pages,
                "read_at": datetime.now(UTC).isoformat(),
                versions.VERSIONS_KEY: versions.first_version(
                    s3_key=key, user_id=user_id
                ),
            },
            document_uuid=document_uuid,
            scope=scope,
        )
    if document is None:
        raise SiteRefused("The file could not be saved just now.")
    try:
        await enqueue_job(
            FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
            document.id,
            key,
            organization_id,
            provider_id,
            128,
            "chunked",
        )
    except Exception as exc:  # noqa: BLE001 - stored; reading can be retried
        logger.warning("Could not queue reading of {}: {}", filename, exc)
    return {
        "document_uuid": str(document.document_uuid),
        "filename": filename,
        "new": previous is None,
    }


async def learn_for_thread(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    ref_id: str,
    author_id: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """The tool call. Never raises."""
    for_agents = arguments.get("for_agents")
    for_agents = True if for_agents is None else bool(for_agents)
    try:
        site = await read_site(str(arguments.get("url") or ""), client=client)
    except SiteRefused as exc:
        return {"status": "refused", "reason": str(exc)}
    if not any(p.text for p in site.pages):
        return {
            "status": "error",
            "error": (
                f"{site.host} has almost no readable text; it may be built by "
                "script. Nothing was saved. Ask for a page with the details, "
                "or for a document with them."
            ),
        }
    try:
        kept = await keep(
            organization_id, site, for_agents=for_agents, author_id=author_id
        )
    except SiteRefused as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Could not keep {} for org {}: {}", site.host, organization_id, exc
        )
        return {"status": "error", "error": "The site was read but could not be saved."}

    from api.services.billing import events as billing_events

    fee = await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.TOOL_CALL,
        ref_id=ref_id,
        quantity=len(site.pages),
        note=f"learn website: {site.host} ({len(site.pages)} pages)",
    )
    markdown = as_markdown(site)
    out: dict[str, Any] = {
        "status": "success",
        "site": site.host,
        "file": kept,
        "pages": [{"url": p.url, "title": p.title} for p in site.pages],
        "for_agents": for_agents,
        "what_it_says": markdown[:EXCERPT_CHARS],
        "charged_paise": int(fee or 0),
        "note": (
            (
                "Kept as company knowledge: every agent answers from it"
                if for_agents
                else "Kept in Files for the person"
            )
            + " once it has been read, in a minute or so. "
            + ("It replaced the copy learnt before." if not kept["new"] else "")
        ).strip(),
    }
    found = found_in(site)
    if found:
        out["found"] = found
    if site.skipped:
        out["skipped"] = [{"url": u, "why": why} for u, why in site.skipped]
    if len(markdown) > EXCERPT_CHARS:
        out["cut"] = (
            f"Shown up to {EXCERPT_CHARS} characters here; the file has all "
            f"{len(markdown)}."
        )
    return out


__all__ = [
    "MAX_PAGES",
    "TOOL_NAME",
    "Site",
    "SiteRefused",
    "as_markdown",
    "enabled",
    "found_in",
    "keep",
    "learn_for_thread",
    "links_of",
    "ranked",
    "read_site",
    "tool_schema",
]

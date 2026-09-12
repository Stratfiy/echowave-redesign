"""The connector list an operator actually sees.

Composio publishes 1,540 toolkits. That number is the wrong one to put on a
screen, for two reasons that both matter more than the size of it.

**Most of them cannot be connected in one click.** Only 122 carry
Composio-managed auth, where the customer presses a button, signs in, and is
done. Another 33 need no auth at all. The remaining 1,385 need somebody to
register an OAuth application with that provider first -- us, per provider,
before any customer can use it -- or need the customer to find and paste an
API key. Listing all of them undifferentiated would be advertising 1,540
integrations and delivering 155, and the customer would find out at the worst
moment: after choosing one.

**And most of them are for somebody else.** Composio's own largest category is
"developer tools", with Bitbucket, Daytona and Hugging Face in it. A dental
clinic in Hosur is not looking for Hugging Face. Composio's 81 categories are
organised for the developer buying Composio; ours are organised for the person
running the business, which is a different list with different words on it.

So this module does two things: classifies each toolkit by how hard it is to
turn on, and re-files it under a heading our customer would look under.
Anything we cannot offer today is not listed at all -- an integration you
cannot have is not a feature, it is a disappointment with a logo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import (
    COMPOSIO_BASE_URL,
    COMPOSIO_TIMEOUT_SECS,
    REDIS_URL,
)
from api.services.integrations.composio.client import _headers

#: Composio's catalogue moves slowly -- toolkits are added, not reshuffled --
#: and every page load paying for four upstream requests would be absurd. A day
#: is long enough to be free and short enough that a new app appears without a
#: deploy.
CACHE_KEY = "composio:catalogue:v1"
CACHE_TTL_SECONDS = 24 * 60 * 60

#: How a connector is turned on, worst case first. The screen sorts by this,
#: because "press a button" and "go and find your API key" are different
#: promises and a customer should be able to see which one they are getting
#: before they pick.
SETUP_ONE_CLICK = "one_click"  # Composio-managed OAuth: sign in, done.
SETUP_NO_AUTH = "no_auth"  # Nothing to connect at all.
SETUP_API_KEY = "api_key"  # Customer pastes a key from that provider.
#: OAuth with no Composio-managed application behind it: usable only once we
#: have registered our own app with that provider. Listed anyway, and labelled,
#: because the alternative is a silent gap -- a customer who wants Shopify or
#: DocuSign should be able to see that we know it exists and say they want it.
#: Which ones get asked for is the only honest signal about which app to
#: register next, and it costs nothing to collect.
SETUP_NEEDS_APPROVAL = "needs_approval"

#: Composio's categories, re-filed under headings a business owner would look
#: under. Order matters: it is the order of the screen, most-asked-for first.
#: A toolkit lands in the first group that claims any of its categories.
GROUPS: list[tuple[str, set[str]]] = [
    ("Messaging", {"team chat", "communication", "phone & sms", "notifications"}),
    ("Email", {"email", "email newsletters"}),
    ("Calendar & booking", {"scheduling & booking", "calendar"}),
    (
        "Customers & sales",
        {
            "crm",
            "contact management",
            "sales & crm",
            "ai sales tools",
            "customer support",
        },
    ),
    (
        "Money",
        {
            "accounting",
            "payment processing",
            "proposal & invoice management",
            "billing",
            "finance",
        },
    ),
    (
        "Shop & shipping",
        {"ecommerce", "e-commerce", "commerce", "retail", "point of sale"},
    ),
    (
        "Spreadsheets & data",
        {"spreadsheets", "databases", "analytics", "business intelligence"},
    ),
    ("Forms & intake", {"forms & surveys"}),
    (
        "Files & documents",
        {
            "documents",
            "file management & storage",
            "notes",
            "signatures",
            "content & files",
        },
    ),
    (
        "Work management",
        {
            "project management",
            "task management",
            "team collaboration",
            "productivity",
            "time tracking software",
            "product management",
        },
    ),
    (
        "Marketing",
        {
            "marketing automation",
            "marketing",
            "social media accounts",
            "social media marketing",
            "ads & conversion",
            "url shortener",
        },
    ),
    ("Meetings", {"video conferencing", "ai meeting assistants", "transcription"}),
    ("People", {"hr talent & recruitment", "human resources"}),
    ("Learning", {"education", "online courses"}),
    ("Events", {"event management"}),
    ("Devices", {"internet of things"}),
    # Below here is the long tail. Still listed -- an operator who wants GitHub
    # should be able to have GitHub -- but after everything a business asks for
    # first, and reached mostly by typing a name into the search box rather
    # than by scrolling.
    ("Media", {"images & design", "video & audio", "news & lifestyle"}),
    (
        "AI",
        {
            "artificial intelligence",
            "ai models",
            "ai agents",
            "ai chatbots",
            "ai content generation",
            "ai web scraping",
            "ai document extraction",
            "ai safety compliance detection",
            "model context protocol",
        },
    ),
    (
        "Developer",
        {
            "developer tools",
            "it operations",
            "server monitoring",
            "security & identity tools",
            "app builder",
            "website builders",
            "website & app building",
            "databases & storage",
        },
    ),
]

#: The names an Indian business actually asks for, in the order they get asked
#: for. Editorial, not measured -- Composio publishes no popularity figure, and
#: inventing one from `tools_count` would rank by size of API surface, which is
#: not the same thing and would put Salesforce above WhatsApp.
#:
#: Everything not on this list sorts alphabetically inside its group, so the
#: list only has to be right about the top, and being short is a feature: it is
#: a claim about what a receptionist agent reaches for, and a hundred entries
#: would be a claim about nothing.
POPULAR: tuple[str, ...] = (
    "whatsapp",
    "gmail",
    "googlesheets",
    "googlecalendar",
    "googledrive",
    "googledocs",
    "slack",
    "razorpay",
    "stripe",
    "shopify",
    "zoho_books",
    "zoho_invoice",
    "zoho_bigin",
    "zoho_mail",
    "zoho_desk",
    "hubspot",
    "salesforce",
    "outlook",
    "calendly",
    "cal",
    "airtable",
    "notion",
    "typeform",
    "square",
    "quickbooks",
    "freshdesk",
    "zendesk",
    "intercom",
    "shippo",
    "mailchimp",
    "microsoft_teams",
    "zoom",
    "googlemeet",
    "excel",
    "trello",
    "asana",
    "clickup",
    "linear",
    "jira",
    "instagram",
    "facebook",
    "linkedin",
    "youtube",
)

#: Rank for the sort. Anything unlisted sorts after every listed one.
_POPULAR_RANK = {slug: index for index, slug in enumerate(POPULAR)}

#: Where an offerable toolkit goes when it matches no group above. Last in the
#: order, and deliberately not empty of meaning: "we can connect this and did
#: not have a shelf for it" is true and useful, where dropping it silently is
#: neither.
OTHER_GROUP = "Other"


@dataclass(frozen=True)
class Connector:
    slug: str
    name: str
    description: str
    logo: Optional[str]
    group: str
    setup: str
    tools_count: int


def _setup_kind(toolkit: dict[str, Any]) -> Optional[str]:
    """How this one gets turned on, or None if we cannot offer it.

    ``composio_managed_auth_schemes`` is the field that matters and the one
    easy to confuse with ``auth_schemes`` beside it. The second says what the
    provider supports; only the first says Composio will carry the OAuth
    application, which is the difference between a customer pressing a button
    and us registering an app with that provider first.
    """
    if toolkit.get("composio_managed_auth_schemes"):
        return SETUP_ONE_CLICK
    if toolkit.get("no_auth"):
        return SETUP_NO_AUTH
    schemes = {str(s).upper() for s in (toolkit.get("auth_schemes") or [])}
    if schemes & {"API_KEY", "BEARER_TOKEN", "BASIC"}:
        # Offerable, but the customer has to go and find a key. Listed, and
        # labelled as such, rather than hidden -- some businesses do have the
        # key already, and for them this is the fastest route. Shopify and
        # Razorpay both land here, which is why neither needed special-casing.
        return SETUP_API_KEY
    if schemes:
        # OAuth with no managed application behind it. We cannot connect it
        # today, and we list it anyway: see SETUP_NEEDS_APPROVAL.
        return SETUP_NEEDS_APPROVAL
    return None


def _group_for(categories: list[dict[str, Any]]) -> Optional[str]:
    """Which shelf this goes on. Never None: nothing is dropped for its
    category.

    Order of the checks is the whole logic, because a toolkit claiming both
    "ecommerce" and "developer tools" has to land on the shelf a business would
    look under rather than the one a programmer would. GROUPS is ordered so the
    business shelves are tried first and the long tail last, and anything
    matching none of them is Other.
    """
    names = {
        str(c.get("name") or "").strip().lower()
        for c in categories
        if isinstance(c, dict)
    }
    names.discard("")
    if not names:
        return OTHER_GROUP

    for group, claimed in GROUPS:
        if names & claimed:
            return group

    return OTHER_GROUP


def curate(toolkits: list[dict[str, Any]]) -> list[Connector]:
    """Turn Composio's raw catalogue into the list we are willing to show."""
    plain_slugs = {
        t.get("slug")
        for t in toolkits
        if isinstance(t, dict) and isinstance(t.get("slug"), str)
    }

    out: list[Connector] = []
    for toolkit in toolkits:
        if not isinstance(toolkit, dict) or toolkit.get("deprecated") is True:
            continue
        slug = toolkit.get("slug")
        name = toolkit.get("name")
        if not isinstance(slug, str) or not isinstance(name, str):
            continue

        # Composio publishes an `<app>_mcp` flavour of many toolkits. Where the
        # plain toolkit is also published, the MCP one is a duplicate and two
        # Box rows differing only by a suffix is a worse screen, not a richer
        # one -- so it is dropped.
        #
        # Only then, though. Fifty-seven of them have no plain twin, and
        # Cashfree is one: a payment gateway a great many Indian businesses
        # use, published by Composio only as `cashfree_payments_mcp`. Dropping
        # every `_mcp` slug took it off the screen entirely, which is the same
        # silent-absence bug the category allowlist had, wearing a different
        # hat.
        if slug.endswith("_mcp") and slug[: -len("_mcp")] in plain_slugs:
            continue

        setup = _setup_kind(toolkit)
        if setup is None:
            continue

        meta = toolkit.get("meta") or {}
        group = _group_for(meta.get("categories") or [])
        if group is None:
            continue

        out.append(
            Connector(
                slug=slug.lower(),
                name=name,
                description=str(meta.get("description") or "").strip(),
                logo=meta.get("logo") if isinstance(meta.get("logo"), str) else None,
                group=group,
                setup=setup,
                tools_count=int(meta.get("tools_count") or 0),
            )
        )

    # Popular first, then alphabetical. Not by setup: "one click" is a useful
    # badge and a bad sort key, because it would bury Razorpay -- which every
    # Indian business uses and which needs a pasted key -- under a one-click
    # app nobody has heard of.
    order = {group: index for index, (group, _) in enumerate(GROUPS)}
    order[OTHER_GROUP] = len(GROUPS)
    unranked = len(POPULAR)
    out.sort(
        key=lambda c: (
            order[c.group],
            _POPULAR_RANK.get(c.slug, unranked),
            c.name.lower(),
        )
    )
    return out


async def _fetch_all(timeout_secs: float) -> list[dict[str, Any]]:
    """Every page of Composio's catalogue.

    Bounded rather than `while next_cursor`: this runs against somebody else's
    API, and a paging bug on their side should cost a truncated list, not an
    endless loop holding a request open.
    """
    headers = _headers()
    collected: list[dict[str, Any]] = []
    cursor: Optional[str] = None

    async with httpx.AsyncClient(timeout=timeout_secs) as client:
        for _ in range(10):
            params: dict[str, Any] = {"limit": 500}
            if cursor:
                params["cursor"] = cursor
            response = await client.get(
                f"{COMPOSIO_BASE_URL}/api/v3.1/toolkits",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            body = response.json()
            items = body.get("items") if isinstance(body, dict) else None
            if not isinstance(items, list):
                break
            collected.extend(items)
            cursor = body.get("next_cursor")
            if not cursor:
                break

    return collected


async def _cache() -> aioredis.Redis | None:
    try:
        return await aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - a cache is never load-bearing
        logger.debug("Composio catalogue cache unavailable: {}", exc)
        return None


async def connectors(
    *, timeout_secs: float = COMPOSIO_TIMEOUT_SECS, refresh: bool = False
) -> list[Connector]:
    """The curated connector list, from cache when we have it.

    Returns an empty list rather than raising when Composio is unreachable. The
    integrations screen showing nothing with an explanation is a bad minute;
    the screen failing to load is a support ticket.
    """
    cache = await _cache()
    if cache is not None and not refresh:
        try:
            cached = await cache.get(CACHE_KEY)
            if cached:
                return [Connector(**row) for row in json.loads(cached)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read the Composio catalogue cache: {}", exc)

    try:
        raw = await _fetch_all(timeout_secs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch the Composio catalogue: {}", exc)
        return []

    curated = curate(raw)

    if cache is not None and curated:
        try:
            await cache.set(
                CACHE_KEY,
                json.dumps([asdict(c) for c in curated]),
                ex=CACHE_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write the Composio catalogue cache: {}", exc)

    return curated


def search(rows: list[Connector], query: str) -> list[Connector]:
    """Filter the catalogue by what somebody typed.

    Three passes rather than one substring test, because the ranking is the
    useful part. Somebody typing "what" wants WhatsApp at the top, not
    Whatagraph; somebody typing "sheet" wants Google Sheets above an app whose
    description happens to mention a sheet. So: name-prefix matches first, then
    anything else in the name, then description matches, each keeping the
    catalogue's own popular-first order within it.

    An empty query returns the list untouched -- the caller should not have to
    branch on whether a search box has anything in it.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return rows

    starts: list[Connector] = []
    contains: list[Connector] = []
    described: list[Connector] = []

    for row in rows:
        name = row.name.lower()
        slug = row.slug.lower()
        if name.startswith(needle) or slug.startswith(needle):
            starts.append(row)
        elif needle in name or needle in slug:
            contains.append(row)
        elif needle in row.description.lower():
            described.append(row)

    return starts + contains + described

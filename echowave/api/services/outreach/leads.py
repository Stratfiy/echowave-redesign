"""The lead-data slot: who to write to, from a provider the business can key.

A business owner says what they sell and to whom; Decibyl turns that into
criteria (job titles, places, industries, company size, keywords) and asks a
lead-data provider for matching people. Which provider is a setting, not a
code path: each one is a :class:`LeadProvider` registered in ``PROVIDERS``
and keyed like every other vendor, in the provider-key vault under
component ``data``.

Where the key comes from, in order (the same order ``lookup_source`` uses
for contact lookups, so the receipt says the same thing):

1. the workspace's **own** key -- added from the card on the thread, or by
   an admin under Provider keys -- charged as a tool call on their account;
2. the **platform's** key, added by an operator under Super admin, Provider
   keys -- charged the fee plus the vendor's price per verified address;
3. **none**: a ``needs_setup`` answer naming the provider and how to add the
   key. Never an empty list: "no leads" and "no key" must not look alike.

Apollo is the provider implemented (People API Search, then Bulk People
Enrichment for the addresses). Its API is documented at
https://docs.apollo.io/reference; the field names here follow it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx
from loguru import logger

from api import constants
from api.enums import CostComponent

#: Seconds before a provider call is given up on. A turn is waiting.
TIMEOUT_SECONDS = 20.0
#: Most leads one search hands back. A page a person can read and pick from.
MAX_LEADS = 25

OWN = "own"
PLATFORM = "platform"
NONE = "none"


class LeadError(Exception):
    """The provider answered, and not with leads. The message is for the
    person: what went wrong and what to do about it."""

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Criteria:
    """Who to look for, in the provider's own terms."""

    titles: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    keywords: str = ""
    company_sizes: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    limit: int = 10

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> Criteria:
        def words(key: str, most: int = 10) -> tuple[str, ...]:
            raw = arguments.get(key) or []
            if isinstance(raw, str):
                raw = [raw]
            out = [str(v).strip()[:80] for v in raw if str(v or "").strip()]
            return tuple(dict.fromkeys(out))[:most]

        try:
            limit = int(arguments.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        return cls(
            titles=words("titles"),
            locations=words("locations"),
            industries=words("industries"),
            keywords=str(arguments.get("keywords") or "").strip()[:200],
            company_sizes=tuple(
                s for s in words("company_sizes") if s in COMPANY_SIZES
            ),
            domains=words("domains", 25),
            limit=max(1, min(MAX_LEADS, limit)),
        )

    def empty(self) -> bool:
        return not (
            self.titles
            or self.locations
            or self.industries
            or self.keywords
            or self.company_sizes
            or self.domains
        )


#: Company sizes by headcount, as the tool offers them. Apollo takes the
#: same ranges written "low,high".
COMPANY_SIZES = (
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1000",
    "1001-5000",
    "5001+",
)


@dataclass(frozen=True)
class Lead:
    name: str
    title: str = ""
    company: str = ""
    website: str = ""
    email: str = ""
    #: The provider's word for the address: ``verified`` is the only one a
    #: draft is offered for; anything else is a guess and is said to be.
    email_status: str = ""
    city: str = ""
    country: str = ""
    industry: str = ""
    employees: int | None = None
    provider_id: str = ""
    source: str = ""

    @property
    def verified(self) -> bool:
        return bool(self.email) and self.email_status == "verified"

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in ("", None)}
        out["email_verified"] = self.verified
        return out


@dataclass
class SearchResult:
    leads: list[Lead] = field(default_factory=list)
    #: How many the provider says match in all, past the page returned.
    total: int | None = None
    #: Matches found without a verified address (not offered for a draft).
    without_email: int = 0


class LeadProvider(Protocol):
    """One lead-data vendor. ``key`` is the plaintext from the vault."""

    name: str
    label: str
    #: Where a person gets a key, for the card that asks for one.
    key_help: str

    async def search(self, key: str, criteria: Criteria) -> SearchResult: ...


# --- Apollo ------------------------------------------------------------------


class Apollo:
    """Apollo.io: People API Search for who matches, then Bulk People
    Enrichment for their work addresses (search returns none).

    Search is free on Apollo's side; enrichment spends the account's
    credits, so only the page asked for is enriched, ten at a time (the
    endpoint's limit)."""

    name = "apollo"
    label = "Apollo"
    key_help = (
        "In Apollo: Settings, Integrations, API, Create new key. A master key "
        "is needed for people search."
    )
    base_url = "https://api.apollo.io/api/v1"
    #: Bulk enrichment takes at most this many people per call.
    enrich_batch = 10

    def _headers(self, key: str) -> dict[str, str]:
        return {
            "x-api-key": key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "accept": "application/json",
        }

    def _search_body(self, criteria: Criteria, per_page: int) -> dict[str, Any]:
        body: dict[str, Any] = {"page": 1, "per_page": per_page}
        if criteria.titles:
            body["person_titles"] = list(criteria.titles)
            body["include_similar_titles"] = True
        if criteria.locations:
            body["person_locations"] = list(criteria.locations)
        keywords = " ".join(
            part for part in (criteria.keywords, *criteria.industries) if part
        ).strip()
        if keywords:
            body["q_keywords"] = keywords
        if criteria.company_sizes:
            body["organization_num_employees_ranges"] = [
                size.replace("-", ",").replace("+", ",1000000")
                for size in criteria.company_sizes
            ]
        if criteria.domains:
            body["q_organization_domains_list"] = list(criteria.domains)
        return body

    async def _post(
        self, http: httpx.AsyncClient, path: str, key: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        response = await http.post(
            f"{self.base_url}/{path}", json=body, headers=self._headers(key)
        )
        if response.status_code in (401, 403):
            detail = _detail(response)
            if "master" in detail.lower():
                raise LeadError(
                    "Apollo says this key cannot search people: it needs a "
                    "master API key. Create one in Apollo (Settings, "
                    "Integrations, API) and add it again.",
                    code="key_rejected",
                )
            raise LeadError(
                "Apollo rejected the key. Check it in Apollo (Settings, "
                "Integrations, API) and add it again.",
                code="key_rejected",
            )
        if response.status_code == 422 and "credit" in _detail(response).lower():
            raise LeadError(
                "The Apollo account is out of credits, so no addresses could be "
                "looked up. Top it up in Apollo and ask again.",
                code="out_of_credits",
            )
        if response.status_code == 429:
            raise LeadError(
                "Apollo is limiting requests on this key just now. Try again in "
                "a minute.",
                code="rate_limited",
            )
        if response.status_code >= 400:
            raise LeadError(
                f"Apollo answered {response.status_code}: {_detail(response)[:200]}",
                code="provider_error",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LeadError(
                "Apollo sent back something that was not a list of people.",
                code="provider_error",
            ) from exc
        return data if isinstance(data, dict) else {}

    async def search(self, key: str, criteria: Criteria) -> SearchResult:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            # Asked for twice the page: some matches have no work address.
            found = await self._post(
                http,
                "mixed_people/api_search",
                key,
                self._search_body(criteria, min(100, criteria.limit * 2)),
            )
            people = [p for p in (found.get("people") or []) if isinstance(p, dict)]
            total = _int(
                found.get("total_entries")
                or (found.get("pagination") or {}).get("total_entries")
            )
            ids = [str(p.get("id")) for p in people if p.get("id")]
            enriched: list[dict[str, Any]] = []
            for start in range(0, len(ids), self.enrich_batch):
                if len([e for e in enriched if _verified(e)]) >= criteria.limit:
                    break
                batch = ids[start : start + self.enrich_batch]
                matched = await self._post(
                    http,
                    "people/bulk_match",
                    key,
                    {
                        "details": [{"id": i} for i in batch],
                        "reveal_personal_emails": False,
                        "reveal_phone_number": False,
                    },
                )
                enriched.extend(
                    m for m in (matched.get("matches") or []) if isinstance(m, dict)
                )
        by_id = {str(m.get("id")): m for m in enriched if m.get("id")}
        leads: list[Lead] = []
        without_email = 0
        for person in people:
            merged = {**person, **by_id.get(str(person.get("id")), {})}
            lead = _apollo_lead(merged)
            if not lead.name:
                continue
            if not lead.verified:
                without_email += 1
                continue
            leads.append(lead)
            if len(leads) >= criteria.limit:
                break
        return SearchResult(leads=leads, total=total, without_email=without_email)


def _apollo_lead(person: dict[str, Any]) -> Lead:
    org = person.get("organization") or {}
    if not isinstance(org, dict):
        org = {}
    name = str(
        person.get("name")
        or " ".join(p for p in (person.get("first_name"), person.get("last_name")) if p)
    ).strip()
    email = str(person.get("email") or "").strip()
    if email.lower().startswith("email_not_unlocked"):
        email = ""
    return Lead(
        name=name,
        title=str(person.get("title") or "").strip(),
        company=str(org.get("name") or "").strip(),
        website=str(org.get("website_url") or org.get("primary_domain") or "").strip(),
        email=email,
        email_status=str(person.get("email_status") or "").strip().lower(),
        city=str(person.get("city") or "").strip(),
        country=str(person.get("country") or "").strip(),
        industry=str(org.get("industry") or "").strip(),
        employees=_int(org.get("estimated_num_employees")),
        provider_id=str(person.get("id") or ""),
        source="apollo",
    )


def _verified(person: dict[str, Any]) -> bool:
    return bool(person.get("email")) and (
        str(person.get("email_status") or "").lower() == "verified"
    )


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or ""
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)
    return str(data)


# --- the slot -----------------------------------------------------------------

#: Every provider this codebase can search. The name is the vault's provider
#: name under component ``data`` and the registry's (``DATA_PROVIDERS``).
PROVIDERS: dict[str, LeadProvider] = {"apollo": Apollo()}


def provider() -> LeadProvider:
    """The provider this deployment searches with: ``LEAD_DATA_PROVIDER``,
    else Apollo. An unknown name falls back to Apollo *and says so*, rather
    than leaving the slot empty."""
    wanted = (constants.LEAD_DATA_PROVIDER or "apollo").strip().lower()
    chosen = PROVIDERS.get(wanted)
    if chosen is None:
        logger.warning(
            "LEAD_DATA_PROVIDER={!r} is not a lead provider here ({}); using Apollo",
            wanted,
            ", ".join(sorted(PROVIDERS)),
        )
        return PROVIDERS["apollo"]
    return chosen


@dataclass(frozen=True)
class Key:
    kind: str  # OWN, PLATFORM or NONE
    provider: str
    value: str | None = None

    @property
    def usable(self) -> bool:
        return self.kind in (OWN, PLATFORM) and bool(self.value)


async def key_for(organization_id: int, name: str) -> Key:
    """The workspace's own key first, the platform's second. Never raises:
    a vault that cannot be read is no key, which is said, not guessed."""
    from api.db import db_client
    from api.services.configuration import (
        organization_credentials,
        platform_credentials,
    )

    try:
        async with db_client.async_session() as session:
            own = await organization_credentials.resolve_api_key(
                session,
                organization_id=organization_id,
                component=CostComponent.DATA,
                provider=name,
            )
            if own:
                return Key(OWN, name, own)
            platform = await platform_credentials.resolve_api_key(
                session, component=CostComponent.DATA, provider=name
            )
            if platform:
                return Key(PLATFORM, name, platform)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read the {} lead key for org {}: {}", name, organization_id, exc
        )
    return Key(NONE, name)


__all__ = [
    "COMPANY_SIZES",
    "MAX_LEADS",
    "NONE",
    "OWN",
    "PLATFORM",
    "PROVIDERS",
    "Apollo",
    "Criteria",
    "Key",
    "Lead",
    "LeadError",
    "LeadProvider",
    "SearchResult",
    "key_for",
    "provider",
]

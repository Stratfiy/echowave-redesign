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

Two providers are implemented:

* **Treg** (https://treg.to), the default: one token for a gateway over
  many lead-data vendors (Apollo, Hunter, Lusha, People Data Labs...). Its
  routed ``treg.people.search`` finds the people and ``treg.people.email.
  verify`` checks each address before it counts. Every call carries an
  ``Idempotency-Key`` (a retry never bills twice) and an
  ``X-Treg-Route-Max-Cost`` cap drawn from the run's :class:`Budget`;
  what was actually charged comes back in ``X-Treg-Cost-Micro`` and is
  recorded in vendor metering. Docs: https://treg.to/llms.txt.
* **Apollo** directly (People API Search, then Bulk People Enrichment),
  for a business that holds its own Apollo key. Docs:
  https://docs.apollo.io/reference.

A search is priced before it runs (:meth:`LeadProvider.estimate`) and never
spends past the run's cap (``LEAD_SEARCH_MAX_USD``).
"""

from __future__ import annotations

import hashlib
import json
import uuid
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

    def __init__(
        self, message: str, *, code: str = "error", detail: dict | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        #: What the person can act on: a top-up link, when to retry.
        self.detail = dict(detail or {})


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

    def fingerprint(self) -> str:
        """Which search this is, for matching an estimate to its run."""
        canonical = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

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
    #: Why the search stopped short with some leads in hand (the cap, a rate
    #: limit, the balance), so the person is told the list is partial.
    stopped: LeadError | None = None


@dataclass
class Charge:
    """One billed call, as the provider reported it."""

    endpoint: str
    cost_micro: int
    call_id: str = ""


@dataclass
class Budget:
    """What one search may spend, in micro-USD, and what it has spent.

    ``run_id`` keys every call's ``Idempotency-Key``, so a retried turn of
    the same run replays rather than pays twice."""

    cap_micro: int
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    charges: list[Charge] = field(default_factory=list)

    @property
    def spent_micro(self) -> int:
        return sum(c.cost_micro for c in self.charges)

    @property
    def left_micro(self) -> int:
        return max(self.cap_micro - self.spent_micro, 0)

    def key(self, step: str) -> str:
        return f"decibyl-{self.run_id}-{step}"[:120]


@dataclass(frozen=True)
class Estimate:
    """What a search is expected to cost, before it runs."""

    typical_usd: float
    cap_usd: float
    unit: str = "USD"
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "typical": round(self.typical_usd, 4),
            "at_most": round(self.cap_usd, 4),
            "unit": self.unit,
            "note": self.note,
        }


def run_cap_usd() -> float:
    """The most one lead search may spend (``LEAD_SEARCH_MAX_USD``)."""
    try:
        return max(float(constants.LEAD_SEARCH_MAX_USD), 0.01)
    except (TypeError, ValueError):
        return 0.5


class LeadProvider(Protocol):
    """One lead-data vendor. ``key`` is the plaintext from the vault."""

    name: str
    label: str
    #: Where a person gets a key, for the card that asks for one.
    key_help: str

    def estimate(self, criteria: Criteria) -> Estimate: ...

    async def search(
        self, key: str, criteria: Criteria, budget: Budget
    ) -> SearchResult: ...


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

    def estimate(self, criteria: Criteria) -> Estimate:
        """Apollo bills its own credits, not dollars here: search is free and
        each enriched person costs one credit."""
        return Estimate(
            typical_usd=0.0,
            cap_usd=0.0,
            unit="Apollo credits",
            note=(
                f"Uses up to {criteria.limit * 2} Apollo credits on your Apollo "
                "account (one per person looked up)."
            ),
        )

    async def search(
        self, key: str, criteria: Criteria, budget: Budget | None = None
    ) -> SearchResult:
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


# --- Treg -----------------------------------------------------------------------


class Treg:
    """Treg (https://treg.to): one token, many lead-data vendors behind it.

    A search is three kinds of call, each through ``/call/<endpoint>``:

    1. ``treg.people.search`` -- who matches (Treg routes to the best of its
       people-search providers, own keys first, cheapest first);
    2. ``treg.people.email.find`` -- an address for a match that came back
       without one, from its name and company domain;
    3. ``treg.people.email.verify`` -- every address is checked before it
       counts. Treg's own advice: a search row's email is a directory guess
       until verified, and unverified rows were most of one team's bounces.

    Every call carries ``Idempotency-Key`` (from the run, so a retried turn
    replays instead of paying again), ``X-Treg-Route-Max-Cost`` (what is
    left of the run's cap, so it can never overspend: past it Treg answers
    402 ``route_max_cost`` and charges nothing) and excludes the LinkedIn
    scraper among the routed providers -- Decibyl never reads LinkedIn.
    The charge is ``X-Treg-Cost-Micro`` on the response, never the body.
    """

    name = "treg"
    label = "Treg"
    key_help = (
        "In Treg (treg.to): your team's settings, Tokens, create a per-team "
        "token. One token covers Apollo, Hunter, Lusha and the other "
        "lead-data providers Treg routes to."
    )
    base_url = "https://treg.to"
    SEARCH = "treg.people.search"
    FIND = "treg.people.email.find"
    VERIFY = "treg.people.email.verify"
    #: Routed providers never used: LinkedIn is not read, by rule.
    EXCLUDE = "harvestapi"
    #: Share of the run's cap the searches may take, leaving room to verify.
    SEARCH_SHARE = 0.6
    #: Ceiling on one find or one verify call, in micro-USD.
    FIND_MAX_MICRO = 50_000
    VERIFY_MAX_MICRO = 20_000
    #: Catalog prices the estimate is drawn from (USD): a cheap search row,
    #: a verification, a find. The cap, not these, is what bounds the spend.
    TYPICAL_SEARCH_USD = 0.003
    TYPICAL_VERIFY_USD = 0.006
    TYPICAL_FIND_USD = 0.01
    #: Searches one run makes at most (one per job title or company).
    MAX_SEARCHES = 3

    def estimate(self, criteria: Criteria) -> Estimate:
        searches = max(
            1,
            min(self.MAX_SEARCHES, len(criteria.titles) or len(criteria.domains) or 1),
        )
        typical = (
            searches * self.TYPICAL_SEARCH_USD
            + criteria.limit * self.TYPICAL_VERIFY_USD
            + criteria.limit * 0.5 * self.TYPICAL_FIND_USD
        )
        cap = run_cap_usd()
        return Estimate(
            typical_usd=min(typical, cap),
            cap_usd=cap,
            note=(
                f"{searches} search(es) and a check of each address, at Treg's "
                "catalog prices. You pay what Treg charges and never more than "
                "the cap: past it Treg refuses and charges nothing."
            ),
        )

    def _headers(
        self, key: str, budget: Budget, step: str, cap_micro: int
    ) -> dict[str, str]:
        return {
            "X-Treg-Token": key,
            "Content-Type": "application/json",
            "Idempotency-Key": budget.key(step),
            "X-Treg-Route-Max-Cost": f"{cap_micro / 1_000_000:.6f}",
            "X-Treg-Route-Exclude": self.EXCLUDE,
        }

    async def _call(
        self,
        http: httpx.AsyncClient,
        key: str,
        endpoint: str,
        body: dict[str, Any],
        budget: Budget,
        step: str,
        cap_micro: int,
    ) -> dict[str, Any]:
        cap_micro = min(cap_micro, budget.left_micro)
        if cap_micro <= 0:
            raise LeadError("The cap for this search is spent.", code="cap_reached")
        response = await http.post(
            f"{self.base_url}/call/{endpoint}",
            json=body,
            headers=self._headers(key, budget, step, cap_micro),
        )
        cost = _int(response.headers.get("X-Treg-Cost-Micro")) or 0
        if cost:
            budget.charges.append(
                Charge(
                    endpoint=endpoint,
                    cost_micro=cost,
                    call_id=response.headers.get("X-Treg-Call-Id", ""),
                )
            )
        if response.status_code < 400:
            try:
                data = response.json()
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
        raise _treg_error(response)

    async def search(
        self, key: str, criteria: Criteria, budget: Budget
    ) -> SearchResult:
        rows: list[dict[str, Any]] = []
        searches = _treg_searches(criteria)[: self.MAX_SEARCHES]
        search_cap = int(budget.cap_micro * self.SEARCH_SHARE)
        stopped: LeadError | None = None
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            for i, body in enumerate(searches):
                left_for_search = search_cap - budget.spent_micro
                if left_for_search <= 0:
                    break
                try:
                    found = await self._call(
                        http,
                        key,
                        self.SEARCH,
                        body,
                        budget,
                        f"search-{i}",
                        left_for_search,
                    )
                except LeadError as exc:
                    if exc.code == "cap_reached" or (
                        rows and exc.code in ("rate_limited", "capacity")
                    ):
                        stopped = exc
                        break
                    raise
                output = (
                    found.get("output")
                    if isinstance(found.get("output"), dict)
                    else found
                )
                rows.extend(
                    p for p in (output.get("people") or []) if isinstance(p, dict)
                )

            leads: list[Lead] = []
            seen: set[str] = set()
            without_email = 0
            for i, row in enumerate(rows):
                if len(leads) >= criteria.limit or stopped is not None:
                    break
                lead = _treg_lead(row)
                ident = (lead.email or f"{lead.name}|{lead.company}").lower()
                if not lead.name or ident in seen:
                    continue
                seen.add(ident)
                try:
                    if not lead.email:
                        domain = _domain(lead.website)
                        if not domain:
                            without_email += 1
                            continue
                        got = await self._call(
                            http,
                            key,
                            self.FIND,
                            {"full_name": lead.name, "domain": domain},
                            budget,
                            f"find-{i}",
                            self.FIND_MAX_MICRO,
                        )
                        out = got.get("output") or {}
                        email = str(out.get("email") or "").strip()
                        if not email:
                            without_email += 1
                            continue
                        lead = _with(
                            lead,
                            email=email,
                            email_status="verified"
                            if out.get("verified") is True
                            else "",
                        )
                    if not lead.verified:
                        checked = await self._call(
                            http,
                            key,
                            self.VERIFY,
                            {"email": lead.email},
                            budget,
                            f"verify-{i}",
                            self.VERIFY_MAX_MICRO,
                        )
                        lead = _with(
                            lead, email_status=_verdict(checked.get("output") or {})
                        )
                except LeadError as exc:
                    if exc.code in (
                        "cap_reached",
                        "rate_limited",
                        "capacity",
                        "out_of_balance",
                    ):
                        stopped = exc
                        break
                    raise
                if lead.verified:
                    leads.append(lead)
                else:
                    without_email += 1
        if stopped is not None and not leads:
            raise stopped
        result = SearchResult(leads=leads, total=len(rows), without_email=without_email)
        if stopped is not None:
            result.stopped = stopped
        return result


def _treg_searches(criteria: Criteria) -> list[dict[str, Any]]:
    """The people-search bodies for these criteria: one per company domain
    when companies were named, else one per job title, else one free-text
    question. Treg sends each provider only the fields it accepts."""
    common: dict[str, Any] = {"limit": min(25, criteria.limit * 2)}
    if criteria.locations:
        common["location"] = criteria.locations[0]
    keywords = [
        *criteria.industries,
        *([criteria.keywords] if criteria.keywords else []),
    ]
    if keywords:
        common["keywords"] = keywords
    if criteria.domains:
        return [
            {
                **common,
                "company_domain": d,
                **({"title": criteria.titles[0]} if criteria.titles else {}),
            }
            for d in criteria.domains
        ]
    if criteria.titles:
        return [{**common, "title": t} for t in criteria.titles]
    words = " ".join([*keywords, *criteria.locations]).strip()
    return [{**common, "q": words or "business owners"}]


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("value")
        if isinstance(value, list):
            value = next((v for v in value if v), "")
            if isinstance(value, dict):
                value = value.get("email") or value.get("value") or value.get("name")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _treg_lead(row: dict[str, Any]) -> Lead:
    """One provider-native people row as a lead. Shapes differ per provider
    (Treg passes them through), so each field is read from the names the
    providers use; a profile URL is never kept."""
    name = _first(row, "full_name", "name")
    if not name:
        name = " ".join(
            p for p in (_first(row, "first_name"), _first(row, "last_name")) if p
        )
    status = _first(
        row, "email_status", "email_verification", "verification_status"
    ).lower()
    if row.get("email_verified") is True or row.get("verified") is True:
        status = "verified"
    return Lead(
        name=name,
        title=_first(row, "title", "job_title", "position", "headline"),
        company=_first(
            row,
            "company",
            "company_name",
            "organization",
            "organization_name",
            "employer",
        ),
        website=_first(
            row, "company_domain", "domain", "website", "company_website", "website_url"
        ),
        email=_first(row, "email", "work_email", "email_address", "emails"),
        email_status="verified"
        if status in ("verified", "valid", "deliverable")
        else status,
        city=_first(row, "city", "location", "locality"),
        country=_first(row, "country", "country_code"),
        industry=_first(row, "industry"),
        provider_id=_first(row, "id"),
        source="treg",
    )


def _verdict(output: dict[str, Any]) -> str:
    """Treg's verify contract: ``valid``, ``status`` and ``score``. Only a
    deliverable address counts; ``accept_all`` is risky and is not one."""
    status = str(output.get("status") or "").strip().lower()
    if status in ("valid", "deliverable", "ok") or (
        output.get("valid") is True
        and status not in ("accept_all", "catch_all", "risky", "unknown")
    ):
        return "verified"
    return status or "unverified"


def _with(lead: Lead, **changes: Any) -> Lead:
    return Lead(**{**asdict(lead), **changes})


def _domain(website: str) -> str:
    text = (website or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        text = text.removeprefix(prefix)
    return text.split("/")[0]


def _treg_error(response: httpx.Response) -> LeadError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    info = (
        detail if isinstance(detail, dict) else (body if isinstance(body, dict) else {})
    )
    error = str(info.get("error") or "").lower()
    code = response.status_code
    if code in (401, 403):
        return LeadError(
            "Treg rejected the token. Check it in Treg (team settings, Tokens) "
            "and add it again.",
            code="key_rejected",
        )
    if code == 402 and error == "route_max_cost":
        return LeadError(
            "This search reached its spending cap, so Treg stopped it and "
            "charged nothing for the call that would have gone over.",
            code="cap_reached",
        )
    if code == 402:
        topup = str(info.get("topup_url") or "")
        return LeadError(
            "The Treg balance is too low for this search, so nothing was looked "
            "up. Top it up in Treg"
            + (f" ({topup})" if topup else " (team settings, Billing)")
            + " and ask again.",
            code="out_of_balance",
            detail={
                "topup_url": topup,
                "balance_micro": info.get("balance_micro"),
                "estimated_cost_micro": info.get("estimated_cost_micro"),
            },
        )
    if code == 429:
        return LeadError(
            "The lead provider is limiting requests just now. Try again in a "
            "minute; nothing was charged for the refused call.",
            code="rate_limited",
            detail={"retry_after": response.headers.get("Retry-After")},
        )
    if code == 503:
        if error == "provider_capacity_unavailable":
            return LeadError(
                "Treg's account with that lead provider is out for now (not your "
                "balance; nothing was charged). Try again in a few minutes.",
                code="capacity",
                detail={"resets_at": info.get("resets_at")},
            )
        return LeadError(
            "Treg is busy just now; nothing was charged. Try again shortly.",
            code="rate_limited",
            detail={"retry_after": response.headers.get("Retry-After")},
        )
    if code == 422:
        return LeadError(
            "Treg refused the search as malformed; nothing was charged. This is "
            "ours to fix, not yours.",
            code="malformed",
        )
    return LeadError(
        f"Treg answered {code}; nothing was charged for it. Try again shortly.",
        code="provider_error",
    )


# --- the slot -----------------------------------------------------------------

#: Every provider this codebase can search. The name is the vault's provider
#: name under component ``data`` and the registry's (``DATA_PROVIDERS``).
PROVIDERS: dict[str, LeadProvider] = {"treg": Treg(), "apollo": Apollo()}
DEFAULT_PROVIDER = "treg"


def provider() -> LeadProvider:
    """The provider this deployment searches with: ``LEAD_DATA_PROVIDER``,
    else Treg. An unknown name falls back to Treg *and says so*, rather
    than leaving the slot empty."""
    wanted = (constants.LEAD_DATA_PROVIDER or DEFAULT_PROVIDER).strip().lower()
    chosen = PROVIDERS.get(wanted)
    if chosen is None:
        logger.warning(
            "LEAD_DATA_PROVIDER={!r} is not a lead provider here ({}); using {}",
            wanted,
            ", ".join(sorted(PROVIDERS)),
            DEFAULT_PROVIDER,
        )
        return PROVIDERS[DEFAULT_PROVIDER]
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
    "Budget",
    "Charge",
    "Criteria",
    "Estimate",
    "Key",
    "Lead",
    "LeadError",
    "LeadProvider",
    "SearchResult",
    "Treg",
    "key_for",
    "provider",
    "run_cap_usd",
]

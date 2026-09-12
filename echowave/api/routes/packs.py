"""The shelf: roles a business can hire, and what hiring one involves.

Reads like a job board because that is what it is. A card carries the role,
what the job is, what it speaks, what it needs access to, and what it costs a
month -- and the price and the capability chips are computed from the role's own
declaration rather than typed by whoever published it.

Open to any signed-in user. The shelf is the same for everybody: nothing here
is scoped to an organisation, because a role is not owned by the business that
hires it. What *is* org-scoped -- which apps you have already connected, what
your agents have done -- lives on other endpoints and gets joined on the
screen.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.packs import card, get_pack, hire_steps, jobs, listed_packs
from api.services.packs.search import filter_packs, search_packs

router = APIRouter(prefix="/packs", tags=["packs"])


class PackPricing(BaseModel):
    #: True when hiring this puts an agent on the phone, which is priced as a
    #: hire rather than as usage.
    is_hire: bool
    seat_price_paise: int
    platform_price_paise: int
    creator_price_paise: int
    monthly_price_paise: int
    included_minutes: int
    included_executions: int
    overage_unit: str


class PackPublisher(BaseModel):
    slug: str
    name: str
    first_party: bool


class PackCard(BaseModel):
    slug: str
    name: str
    summary: str
    job: str
    version: str
    publisher: PackPublisher
    #: "Answers calls", "Makes calls", "WhatsApp" -- derived from the declared
    #: channels, never written by the publisher.
    badges: list[str]
    industries: list[str]
    languages: list[str]
    demo_number: Optional[str]
    pricing: PackPricing
    listed: bool


class ShelfResponse(BaseModel):
    #: Job groups in the order we want people to hire in, not alphabetical.
    jobs: list[str]
    packs: list[PackCard]


class HireStep(BaseModel):
    key: str
    title: str
    detail: str
    #: True when going live is blocked until this step is done.
    blocking: bool
    demo_number: Optional[str] = None
    facts: list[dict[str, Any]] = []
    connectors: list[dict[str, Any]] = []


class PackDetail(BaseModel):
    card: PackCard
    steps: list[HireStep]
    #: What the role will do on a call, from the wrapped template. The "job"
    #: half of a job description.
    guardrails: list[str]
    compliance_notes: list[str]


@router.get("", response_model=ShelfResponse)
async def shelf(
    q: Optional[str] = Query(
        default=None, description="Free text, in the user's words"
    ),
    job: Optional[str] = None,
    industry: Optional[str] = None,
    language: Optional[str] = None,
    calling: Optional[bool] = None,
    user: UserModel = Depends(get_user),
) -> ShelfResponse:
    """Every role on the shelf, narrowed by whatever was asked for.

    Search runs first and the filters narrow its result, so "phone" plus
    industry=Clinics behaves the way somebody typing both expects rather than
    intersecting two independent lists.
    """
    found = search_packs(q or "")
    narrowed = filter_packs(
        job=job,
        industry=industry,
        language=language,
        calling=calling,
        packs=found,
    )
    return ShelfResponse(
        jobs=list(jobs()),
        packs=[PackCard(**card(pack)) for pack in narrowed],
    )


@router.get("/{slug}", response_model=PackDetail)
async def pack_detail(
    slug: str,
    user: UserModel = Depends(get_user),
) -> PackDetail:
    """One role in full, with the steps hiring it will walk through."""
    pack = get_pack(slug)
    # An unlisted pack is one still being written or awaiting review. It is not
    # a 403: from outside, a role that is not on the shelf does not exist.
    if pack is None or not pack.listed:
        raise HTTPException(status_code=404, detail="Role not found")

    template = pack.template
    return PackDetail(
        card=PackCard(**card(pack)),
        steps=[HireStep(**step) for step in hire_steps(pack)],
        guardrails=list(template.guardrails) if template else [],
        compliance_notes=list(template.compliance_notes) if template else [],
    )


@router.get("/_all/unlisted", response_model=ShelfResponse, include_in_schema=False)
async def unlisted(user: UserModel = Depends(get_user)) -> ShelfResponse:
    """Roles that exist but are not on the shelf.

    Ours only, for now, and the reason it exists is operational: with no demo
    number configured every calling role is unlisted, and a screen showing an
    empty shelf should be able to say *why* rather than looking broken.
    """
    from api.services.packs import all_packs

    listed = {pack.slug for pack in listed_packs()}
    hidden = [pack for pack in all_packs() if pack.slug not in listed]
    return ShelfResponse(jobs=[], packs=[PackCard(**card(pack)) for pack in hidden])

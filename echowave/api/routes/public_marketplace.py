"""The public marketplace: every listed role, with no account.

The shelf a visitor browses before signing up. Same roles, same search and
filters as the in-app shelf; nothing here reads or depends on who is asking,
and nothing here can hire. What a page may and may not show is decided in
``services/packs/public.py``.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.routes.packs import HireStep, PackCard, ShelfResponse
from api.services.packs import jobs, resolve_listed_packs, resolve_pack
from api.services.packs import public as public_packs
from api.services.packs.derive import card
from api.services.packs.search import filter_packs, search_packs

router = APIRouter(prefix="/public/marketplace", tags=["public-marketplace"])


class OutlineStep(BaseModel):
    name: str
    #: "start" | "step" | "finish". The page words it for the channel.
    kind: str


class PublicPackDetail(BaseModel):
    card: PackCard
    template_id: str
    flow: str
    steps: list[HireStep]
    guardrails: list[str]
    compliance_notes: list[str]
    outline: list[OutlineStep]
    #: Whether the role is on a phone call, so the page never calls a
    #: back-office role's work "a call".
    speaks: bool
    #: How often a scheduled role runs, in words; None for one that responds.
    runs: Optional[str] = None


@router.get("", response_model=ShelfResponse)
async def public_shelf(
    q: Optional[str] = Query(default=None, description="Free text"),
    job: Optional[str] = None,
    industry: Optional[str] = None,
    language: Optional[str] = None,
    calling: Optional[bool] = None,
) -> ShelfResponse:
    """Every listed role, narrowed by whatever was asked for."""
    shelf = await resolve_listed_packs()
    found = search_packs(q or "", packs=shelf)
    narrowed = filter_packs(
        job=job, industry=industry, language=language, calling=calling, packs=found
    )
    return ShelfResponse(
        jobs=list(jobs(shelf)),
        packs=[PackCard(**card(pack)) for pack in narrowed],
    )


@router.get("/{slug}", response_model=PublicPackDetail)
async def public_pack(slug: str) -> dict[str, Any]:
    """One listed role in full. An unlisted role does not exist from outside."""
    pack = await resolve_pack(slug)
    if pack is None or not pack.listed:
        raise HTTPException(status_code=404, detail="Role not found")
    return public_packs.detail(pack)

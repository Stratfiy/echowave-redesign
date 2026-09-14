"""The super-admin KPI board (pricing spec §9).

Read-only, superuser-only, and internal: the ``admin-`` tag keeps every
operation here out of the public OpenAPI document, and the router-level
``get_superuser`` dependency keeps the data behind the staff gate no matter
what a handler forgets. Nothing on this surface is ever described to a
customer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import db_client
from api.services.auth.depends import get_superuser
from api.services.billing import kpi_board
from api.services.billing.rollup import IST

router = APIRouter(
    prefix="/admin/kpis",
    tags=["admin-kpis"],
    dependencies=[Depends(get_superuser)],
)


def _as_of(value: str | None) -> date:
    if not value:
        return datetime.now(IST).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="as_of must be YYYY-MM-DD") from exc


@router.get("")
async def get_kpi_board(
    as_of: str | None = Query(
        None, description="The IST day the windows end on; defaults to today"
    ),
) -> dict[str, Any]:
    """Every KPI in spec §9 for today, the last 7 and the last 30 days, each
    with the previous period beside it. KPIs that cannot be computed yet are
    listed with the reason rather than left blank."""
    async with db_client.async_session() as session:
        return await kpi_board.board(session, as_of=_as_of(as_of))


@router.get("/catalogue")
async def get_kpi_catalogue() -> dict[str, Any]:
    """The board's rows and definitions without values: what is measured,
    how, and which rows are still waiting on an input."""
    return {"sections": kpi_board.catalogue()}

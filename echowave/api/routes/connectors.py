"""The integrations screen's data: what can be connected, and what is.

One endpoint rather than two. The screen has to answer "what is available" and
"what have we already got" in the same breath -- a list that does not know
which rows are already connected renders a Connect button beside a working
integration, which is how a customer ends up with two authorizations and no
idea which one their agent uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.integrations.composio import catalogue
from api.services.integrations.composio.client import (
    connected_toolkits,
    is_configured,
)

router = APIRouter(prefix="/connectors")


class ConnectorResponse(BaseModel):
    slug: str
    name: str
    description: str
    logo: str | None
    setup: str
    tools_count: int
    connected: bool


class ConnectorGroupResponse(BaseModel):
    group: str
    connectors: list[ConnectorResponse]


class ConnectorCatalogueResponse(BaseModel):
    available: bool
    groups: list[ConnectorGroupResponse]
    connected_count: int


@router.get("", response_model=ConnectorCatalogueResponse)
async def list_connectors(
    refresh: bool = Query(
        default=False,
        description="Bypass the cached catalogue and re-read it from Composio.",
    ),
    user: UserModel = Depends(get_user),
) -> ConnectorCatalogueResponse:
    """Every connector this deployment can offer, with this account's status.

    ``available: false`` rather than an error when the deployment has no
    Composio key. The screen then says the feature is not switched on, which is
    a sentence an operator can act on; a 500 is not.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    if not is_configured():
        return ConnectorCatalogueResponse(available=False, groups=[], connected_count=0)

    rows = await catalogue.connectors(refresh=refresh)
    # Best-effort: a catalogue we can show with every row marked unconnected is
    # far better than no screen, and the Connect flow re-checks anyway.
    connected = set(await connected_toolkits(organization_id))

    grouped: dict[str, list[ConnectorResponse]] = {}
    for row in rows:
        grouped.setdefault(row.group, []).append(
            ConnectorResponse(
                slug=row.slug,
                name=row.name,
                description=row.description,
                logo=row.logo,
                setup=row.setup,
                tools_count=row.tools_count,
                connected=row.slug.upper() in connected,
            )
        )

    # Group order comes from the catalogue, which orders them by how often a
    # business actually asks -- not alphabetically, and not by how many
    # connectors happen to be in each.
    ordered = [
        ConnectorGroupResponse(group=name, connectors=grouped[name])
        for name, _ in catalogue.GROUPS
        if name in grouped
    ]

    return ConnectorCatalogueResponse(
        available=True,
        groups=ordered,
        connected_count=len(connected),
    )

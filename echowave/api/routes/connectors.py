"""The integrations screen's data: what can be connected, and what is.

One endpoint rather than two. The screen has to answer "what is available" and
"what have we already got" in the same breath -- a list that does not know
which rows are already connected renders a Connect button beside a working
integration, which is how a customer ends up with two authorizations and no
idea which one their agent uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.integrations.composio import catalogue
from api.services.integrations.composio.client import (
    connect_link,
    connected_accounts,
    connected_toolkits,
    is_configured,
    toolkit_name,
)

router = APIRouter(prefix="/connectors")

#: How many favourites the collection row carries.
#:
#: Twelve because it fills two tidy rows of six on a desktop and three of four
#: on a laptop, and because a collection long enough to need its own scroll has
#: stopped being a shortcut past the categories and become a thirteenth one.
POPULAR_SHOWN = 12


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
    #: The apps an Indian business asks for by name, across every category.
    #:
    #: A collection rather than a category, because the thing somebody wants
    #: is almost never the thing they would go looking for a *heading* for:
    #: nobody opens "Messaging" to find WhatsApp, they scan for WhatsApp.
    #: Empty while a search is running -- a collection of favourites inside a
    #: filtered result is a second ranking fighting the one the user asked
    #: for.
    popular: list[ConnectorResponse] = []
    groups: list[ConnectorGroupResponse] = []
    #: The bucket for apps that matched no category, kept apart so the main
    #: screen can send it to its own page rather than end on nine hundred
    #: unsorted rows.
    #:
    #: It used to be dropped entirely: ``OTHER_GROUP`` is not a member of
    #: ``GROUPS``, and the response was built by walking ``GROUPS``, so every
    #: app the categoriser could not place vanished from the screen with
    #: nothing saying so. The catalogue assigned them and the route lost them.
    other: list[ConnectorResponse] = []
    connected_count: int
    total: int = 0


@router.get("", response_model=ConnectorCatalogueResponse)
async def list_connectors(
    q: str = Query(
        default="",
        max_length=80,
        description="Filter by name. Matches the app's name, slug or description.",
    ),
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
    rows = catalogue.search(rows, q)
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

    # The favourites, in the catalogue's own popularity order, deduplicated
    # against nothing: an app appears here *and* in its category, because
    # somebody who scrolls to "Email" should still find Gmail there.
    #
    # Suppressed while searching. A row of favourites above a filtered result
    # is a second ranking arguing with the one the user typed.
    popular: list[ConnectorResponse] = []
    if not q:
        by_slug = {
            connector.slug: connector
            for bucket in grouped.values()
            for connector in bucket
        }
        popular = [
            by_slug[slug]
            for slug in catalogue.POPULAR[:POPULAR_SHOWN]
            if slug in by_slug
        ]

    return ConnectorCatalogueResponse(
        available=True,
        popular=popular,
        groups=ordered,
        other=grouped.get(catalogue.OTHER_GROUP, []),
        connected_count=len(connected),
        total=len(rows),
    )


class ConnectLinkResponse(BaseModel):
    app: str
    app_name: str
    connect_url: str
    expires_at: str | None = None


@router.post("/{slug}/connect", response_model=ConnectLinkResponse)
async def start_connecting(
    slug: str = Path(description="The connector's slug, e.g. gmail."),
    user: UserModel = Depends(get_user),
) -> ConnectLinkResponse:
    """A link the operator opens to authorize one app for this account.

    Minted per request and short-lived, so it is fetched when the button is
    pressed rather than with the page. A link issued alongside a catalogue of
    1,500 rows would be expired by the time anybody scrolled to the one they
    wanted, and 1,500 of them would be absurd.

    Refuses an app this account has already connected. Two live authorizations
    for one app leave an operator unable to say which one their agent is using,
    and the fix afterwards is worse than the refusal now.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Connecting outside apps is not switched on for this platform.",
        )

    wanted = slug.strip().lower()
    if wanted.upper() in set(await connected_toolkits(organization_id)):
        raise HTTPException(
            status_code=409,
            detail=f"{wanted} is already connected to this account.",
        )

    # Checked against Composio's own catalogue rather than ours: the cached
    # list is a day old and a slug typed into a URL is not from the list at all.
    display_name = await toolkit_name(wanted)
    if not display_name:
        raise HTTPException(status_code=404, detail=f"No app called '{wanted}'.")

    link = await connect_link(toolkit=wanted, organization_id=organization_id)
    if "error" in link:
        raise HTTPException(status_code=502, detail=link["error"])

    return ConnectLinkResponse(
        app=wanted,
        app_name=display_name,
        connect_url=link["url"],
        expires_at=link.get("expires_at"),
    )


class ConnectorActivity(BaseModel):
    kind: str
    app: str | None
    calls: int
    errors: int
    avg_ms: int | None


class ConnectorActivityResponse(BaseModel):
    apps: list[ConnectorActivity]


@router.get("/activity", response_model=ConnectorActivityResponse)
async def connector_activity(
    days: int = Query(default=30, ge=1, le=365),
    user: UserModel = Depends(get_user),
) -> ConnectorActivityResponse:
    """How each connected app has actually behaved for this account.

    Calls, failures and typical latency, which are the three things an operator
    asks when an agent "stopped working" and the three nobody could answer
    before the interactions table existed.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    rows = await db_client.app_interaction_summary(
        organization_id=organization_id, days=days
    )
    return ConnectorActivityResponse(apps=[ConnectorActivity(**row) for row in rows])


class ConnectedAccount(BaseModel):
    connected_account_id: str
    app: str | None
    #: Composio's generated word-id until somebody renames it. No email comes
    #: back from them, and "Dr Ramesh's calendar" is a better label than a
    #: Google address in a clinic anyway.
    label: str
    connected_at: str | None = None


class ConnectedAccountsResponse(BaseModel):
    accounts: list[ConnectedAccount]


@router.get("/accounts", response_model=ConnectedAccountsResponse)
async def list_connected_accounts(
    user: UserModel = Depends(get_user),
) -> ConnectedAccountsResponse:
    """Each app account this organization has authorized, individually.

    The catalogue endpoint answers "is Gmail connected". This answers "which
    Gmail", which is what a clinic with three doctors needs when it points one
    tool at one calendar.
    """
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if not is_configured():
        return ConnectedAccountsResponse(accounts=[])

    rows = await connected_accounts(organization_id)
    return ConnectedAccountsResponse(accounts=[ConnectedAccount(**row) for row in rows])

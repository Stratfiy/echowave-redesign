"""Outside tools and ordering over HTTP (launch stream `reach`).

Thin: each route resolves the signed-in person and their workspace and
hands over to ``services/reach``. Nothing here takes a user id: every route
is about the caller's own connections and orders, and another person's id
or uuid is answered as not found, the way a wrong tenant is.

Flags: ``/reach/connections`` with kind ``tool`` needs ``outside_tools``;
kind ``ordering``, ``/reach/providers`` and ``/reach/orders`` need
``ordering``. Each is a 404 while its flag is off for the workspace. The
OAuth callback is public (the server's sign-in screen redirects to it) and
trusts nothing but the hash of its ``state``.
"""

from __future__ import annotations

from html import escape
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.reach import ORDERING, OUTSIDE_TOOLS, connections, enabled
from api.services.reach.ordering import providers
from api.services.reach.ordering import service as ordering

router = APIRouter(prefix="/reach", tags=["reach"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


def _require(flag: str, user: UserModel) -> int:
    organization_id = _organization_id(user)
    if not enabled(flag, organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return organization_id


def _require_any(user: UserModel) -> int:
    organization_id = _organization_id(user)
    if not (
        enabled(OUTSIDE_TOOLS, organization_id) or enabled(ORDERING, organization_id)
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    return organization_id


def _flag_for(kind: str) -> str:
    return OUTSIDE_TOOLS if kind == connections.TOOL else ORDERING


# --- connections ---------------------------------------------------------------


class ReachToolSummary(BaseModel):
    name: str | None = None
    read: bool


class ReachConnection(BaseModel):
    id: str
    kind: str
    provider: str
    name: str
    server_url: str
    auth: str
    status: str
    tools: list[ReachToolSummary]
    last_error: str | None = None
    connected_at: str | None = None


class ReachConnectionList(BaseModel):
    connections: list[ReachConnection]


class ReachConnectRequest(BaseModel):
    kind: Literal["tool", "ordering"]
    #: For ``ordering``: the app's key (``zomato``, ``swiggy``).
    provider: str | None = Field(default=None, max_length=64)
    #: For ``tool``: what the person calls it, and its server address.
    name: str | None = Field(default=None, max_length=120)
    server_url: str | None = Field(default=None, max_length=2048)
    #: For ``tool``: a token from the server, if it uses one. Stored
    #: encrypted; never returned.
    token: str | None = Field(default=None, max_length=4096)


class ReachConnectResponse(BaseModel):
    connection: ReachConnection
    #: Set when the person must sign in on the server's own screen: the chip
    #: opens it in a new tab.
    authorize_url: str | None = None


@router.get("/connections", response_model=ReachConnectionList)
async def my_connections(
    user: Annotated[UserModel, Depends(get_user)],
) -> ReachConnectionList:
    """The caller's own outside tools and ordering apps. Never anyone else's."""
    organization_id = _require_any(user)
    rows = [
        row
        for row in await connections.mine(organization_id, user.id)
        if enabled(_flag_for(row.kind), organization_id)
    ]
    return ReachConnectionList(
        connections=[ReachConnection(**connections.public(r)) for r in rows]
    )


@router.post("/connections", response_model=ReachConnectResponse)
async def connect(
    body: ReachConnectRequest, user: Annotated[UserModel, Depends(get_user)]
) -> ReachConnectResponse:
    """Connect the caller (and only the caller) to an outside server."""
    organization_id = _require(_flag_for(body.kind), user)
    if body.kind == connections.ORDERING:
        provider = providers.get(body.provider or "")
        if provider is None:
            raise HTTPException(
                status_code=404, detail="There is no such ordering app."
            )
        state, reason = provider.state()
        if state != providers.AVAILABLE:
            raise HTTPException(status_code=409, detail=reason)
        kwargs: dict[str, Any] = {
            "provider": provider.key,
            "name": provider.name,
            "server_url": provider.url(),
            "client_id": provider.client_id(),
        }
    else:
        name = (body.name or "").strip()
        if not name or not (body.server_url or "").strip():
            raise HTTPException(
                status_code=422, detail="Give the tool a name and its server address."
            )
        kwargs = {
            "provider": connections.slug(name),
            "name": name,
            "server_url": body.server_url,
            "pasted_token": body.token,
        }
    try:
        started = await connections.connect(
            organization_id=organization_id, user_id=user.id, kind=body.kind, **kwargs
        )
    except connections.ConnectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReachConnectResponse(
        connection=ReachConnection(**connections.public(started.connection)),
        authorize_url=started.authorize_url,
    )


async def _mine(organization_id: int, user: UserModel, connection_id: str):
    row = await connections.get(organization_id, user.id, connection_id)
    if row is None or not enabled(_flag_for(row.kind), organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    return row


@router.post("/connections/{connection_id}/refresh", response_model=ReachConnection)
async def refresh_connection(
    connection_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> ReachConnection:
    """Read the server's tools again (after signing in, or when it changed)."""
    organization_id = _require_any(user)
    row = await _mine(organization_id, user, connection_id)
    if row.status == connections.PENDING:
        raise HTTPException(
            status_code=409, detail="Finish signing in on the tool's own screen first."
        )
    return ReachConnection(**connections.public(await connections.read_tools(row)))


@router.delete("/connections/{connection_id}", status_code=204)
async def disconnect(
    connection_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> None:
    """Disconnect; the stored token is deleted with it."""
    organization_id = _require_any(user)
    await _mine(organization_id, user, connection_id)
    await connections.revoke(organization_id, user.id, connection_id)


_DONE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:32rem;
margin:3rem auto;padding:0 1rem;line-height:1.5}}</style></head>
<body><h1 style="font-size:1.25rem">{title}</h1><p>{body}</p></body></html>"""


@router.get("/oauth/callback", response_class=HTMLResponse, include_in_schema=False)
async def oauth_callback(
    state: Annotated[str, Query(max_length=200)] = "",
    code: Annotated[str, Query(max_length=4096)] = "",
    error: Annotated[str, Query(max_length=200)] = "",
) -> HTMLResponse:
    """Where the outside server sends the person back after signing in."""
    if not (features.on_anywhere(OUTSIDE_TOOLS) or features.on_anywhere(ORDERING)):
        raise HTTPException(status_code=404, detail="Not Found")
    if error:
        page = _DONE_PAGE.format(
            title="Not connected",
            body="The sign-in was cancelled. You can close this tab and try again from the chat.",
        )
        return HTMLResponse(page, status_code=400)
    try:
        row = await connections.finish_sign_in(state, code)
    except connections.ConnectError as exc:
        page = _DONE_PAGE.format(title="Not connected", body=escape(str(exc)))
        return HTMLResponse(page, status_code=400)
    if row.status != connections.CONNECTED:
        page = _DONE_PAGE.format(
            title="Signed in, but not ready",
            body=escape(row.last_error or "Its tools could not be read yet.")
            + " Go back to the chat and press Check on the chip.",
        )
        return HTMLResponse(page, status_code=200)
    page = _DONE_PAGE.format(
        title=f"{escape(row.name)} is connected",
        body="You can close this tab and go back to the chat.",
    )
    return HTMLResponse(page)


# --- ordering ---------------------------------------------------------------------


class ReachProviderState(BaseModel):
    provider: str
    name: str
    kinds: list[str]
    #: ``connected``, ``available``, ``needs_setup`` or ``unavailable``.
    state: str
    reason: str | None = None
    connection_id: str | None = None


class ReachProviderList(BaseModel):
    providers: list[ReachProviderState]


@router.get("/providers", response_model=ReachProviderList)
async def ordering_providers(
    user: Annotated[UserModel, Depends(get_user)],
) -> ReachProviderList:
    """Each ordering app and where the caller stands with it."""
    organization_id = _require(ORDERING, user)
    out = []
    for provider in providers.PROVIDERS.values():
        row = await connections.live(
            organization_id, user.id, connections.ORDERING, provider.key
        )
        out.append(ReachProviderState(**providers.describe(provider, row)))
    return ReachProviderList(providers=out)


class ReachOrderItem(BaseModel):
    item_id: str
    name: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int


class ReachOrderCharge(BaseModel):
    label: str
    amount_paise: int


class ReachOrderOffer(BaseModel):
    code: str
    description: str = ""
    saving_paise: int | None = None


class ReachOrderDetail(BaseModel):
    id: str
    provider: str
    provider_name: str
    store: dict[str, Any]
    items: list[ReachOrderItem]
    charges: list[ReachOrderCharge]
    discount_paise: int
    subtotal_paise: int | None = None
    total_paise: int | None = None
    currency: str
    coupon: str | None = None
    address: dict[str, Any]
    payment: dict[str, Any]
    offers: list[ReachOrderOffer]
    status: str
    digest: str
    card_event_id: int | None = None
    quoted_at: str | None = None
    provider_order_id: str | None = None
    payment_link: str | None = None
    error: str | None = None


async def _order(organization_id: int, user: UserModel, order_id: str):
    row = await ordering.get_draft(organization_id, user.id, order_id)
    if row is None:
        # Another person's order reads exactly like no order at all.
        raise HTTPException(status_code=404, detail="Not Found")
    return row


@router.get("/orders/{order_id}", response_model=ReachOrderDetail)
async def order_detail(
    order_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> ReachOrderDetail:
    """The order card's detail: items, charges, total, address, payment.
    Its owner's only."""
    organization_id = _require(ORDERING, user)
    return ReachOrderDetail(
        **ordering.detail(await _order(organization_id, user, order_id))
    )


class ReachOrderChange(BaseModel):
    item_id: str
    quantity: int = Field(ge=0, le=50)


class ReachReviseOrderRequest(BaseModel):
    items: list[ReachOrderChange] | None = None
    address_id: str | None = Field(default=None, max_length=80)
    payment_method: str | None = Field(default=None, max_length=40)
    coupon: str | None = Field(default=None, max_length=40)


class ReachReviseOrderResponse(BaseModel):
    status: str
    note: str | None = None
    event_id: int | None = None


@router.post("/orders/{order_id}/revise", response_model=ReachReviseOrderResponse)
async def revise_order(
    order_id: str,
    body: ReachReviseOrderRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> ReachReviseOrderResponse:
    """Change a waiting order: it is priced again and a new card replaces
    the old one, so an approval never carries over to new figures."""
    organization_id = _require(ORDERING, user)
    await _order(organization_id, user, order_id)
    changes = body.model_dump(exclude_unset=True)
    try:
        answer = await ordering.revise(
            organization_id=organization_id,
            user_id=user.id,
            uuid=order_id,
            changes=changes,
        )
    except ordering.OrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReachReviseOrderResponse(
        status=str(answer.get("status")),
        note=answer.get("note"),
        event_id=answer.get("event_id"),
    )


@router.post("/orders/{order_id}/check", response_model=ReachOrderDetail)
async def check_order(
    order_id: str, user: Annotated[UserModel, Depends(get_user)]
) -> ReachOrderDetail:
    """Ask the app what happened to an order whose outcome is unknown."""
    organization_id = _require(ORDERING, user)
    await _order(organization_id, user, order_id)
    try:
        detail = await ordering.reconcile(
            organization_id=organization_id, user_id=user.id, uuid=order_id
        )
    except ordering.OrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReachOrderDetail(**detail)

"""A person's connections to outside servers, and only theirs.

Every function takes the organisation *and* the person, and both are in the
``WHERE`` of every query. There is no function that lists a workspace's
connections: a colleague, an admin of the same workspace, and Decibyl
answering a colleague all see nothing of this person's tools or apps.

Connecting is one call from the chip in the thread:

* a server that needs nothing, or a pasted token, is connected at once and
  its tools are read;
* a server that answers 401 starts a sign-in (``oauth.begin``): the chip
  opens the server's own screen in a new tab, and the callback finishes it.

Connecting again replaces the old connection (one live row per person,
kind and provider -- a unique index, not a convention).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select, update

from api.db import db_client
from api.db.reach_models import ReachConnectionModel
from api.services.reach import oauth, safety, vault, wire
from api.services.workflow import mcp_read_only

TOOL = "tool"
ORDERING = "ordering"
KINDS = (TOOL, ORDERING)

PENDING = "pending"
CONNECTED = "connected"
ERROR = "error"
REVOKED = "revoked"

#: How many tools of one server are kept; the rest are named as left out.
MAX_TOOLS = 40


class ConnectError(Exception):
    """A connection that could not be made, in words a person reads."""


@dataclass
class Started:
    """What the chip shows next."""

    connection: ReachConnectionModel
    #: Set when the person must sign in on the server's own screen.
    authorize_url: str | None = None


def slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return value[:40] or "tool"


def _now() -> datetime:
    return datetime.now(UTC)


def public(row: ReachConnectionModel) -> dict[str, Any]:
    """The row as a route returns it: never the secret."""
    tools = list(row.tools or [])
    return {
        "id": row.uuid,
        "kind": row.kind,
        "provider": row.provider,
        "name": row.name,
        "server_url": row.server_url,
        "auth": row.auth,
        "status": row.status,
        "tools": [{"name": t.get("name"), "read": bool(t.get("read"))} for t in tools],
        "last_error": row.last_error,
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
    }


async def mine(
    organization_id: int, user_id: int, kind: str | None = None
) -> list[ReachConnectionModel]:
    query = select(ReachConnectionModel).where(
        ReachConnectionModel.organization_id == organization_id,
        ReachConnectionModel.user_id == user_id,
        ReachConnectionModel.revoked_at.is_(None),
    )
    if kind:
        query = query.where(ReachConnectionModel.kind == kind)
    async with db_client.async_session() as session:
        rows = (await session.scalars(query.order_by(ReachConnectionModel.id))).all()
    return list(rows)


async def get(
    organization_id: int, user_id: int, uuid: str
) -> ReachConnectionModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(ReachConnectionModel).where(
                ReachConnectionModel.organization_id == organization_id,
                ReachConnectionModel.user_id == user_id,
                ReachConnectionModel.uuid == str(uuid),
                ReachConnectionModel.revoked_at.is_(None),
            )
        )


async def live(
    organization_id: int, user_id: int, kind: str, provider: str
) -> ReachConnectionModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(ReachConnectionModel).where(
                ReachConnectionModel.organization_id == organization_id,
                ReachConnectionModel.user_id == user_id,
                ReachConnectionModel.kind == kind,
                ReachConnectionModel.provider == provider,
                ReachConnectionModel.revoked_at.is_(None),
            )
        )


async def _save(row: ReachConnectionModel, **values: Any) -> ReachConnectionModel:
    values["updated_at"] = _now()
    async with db_client.async_session() as session:
        await session.execute(
            update(ReachConnectionModel)
            .where(
                ReachConnectionModel.id == row.id,
                ReachConnectionModel.organization_id == row.organization_id,
                ReachConnectionModel.user_id == row.user_id,
            )
            .values(**values)
        )
        await session.commit()
    for key, value in values.items():
        setattr(row, key, value)
    return row


async def _replace(
    *,
    organization_id: int,
    user_id: int,
    kind: str,
    provider: str,
    name: str,
    server_url: str,
    auth: str,
) -> ReachConnectionModel:
    """Revoke the person's live row for this provider and add a new one."""
    async with db_client.async_session() as session:
        await session.execute(
            update(ReachConnectionModel)
            .where(
                ReachConnectionModel.organization_id == organization_id,
                ReachConnectionModel.user_id == user_id,
                ReachConnectionModel.kind == kind,
                ReachConnectionModel.provider == provider,
                ReachConnectionModel.revoked_at.is_(None),
            )
            .values(status=REVOKED, revoked_at=_now(), secret_encrypted=None)
        )
        row = ReachConnectionModel(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            provider=provider,
            name=name[:120],
            server_url=server_url,
            auth=auth,
            status=PENDING,
            tools=[],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _describe(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What is kept of a server's tool list: names checked, descriptions
    bounded, and each classified read or write -- conservatively: a tool
    is a read only if its name starts with a reading verb *and* neither it
    nor its description names a write (``mcp_read_only.is_read``)."""
    out = []
    seen: set[str] = set()
    for tool in tools[:MAX_TOOLS]:
        name = str(tool.get("name") or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name) or name in seen:
            # A name the model could not call cleanly is kept out, and said:
            # logged with the server, not dropped silently.
            logger.warning(
                "Outside tool with an unusable name skipped: {!r}", name[:80]
            )
            continue
        seen.add(name)
        description = safety.clean_text(tool.get("description"), 600)
        schema = tool.get("input_schema")
        out.append(
            {
                "name": name,
                "description": description,
                "input_schema": schema
                if isinstance(schema, dict)
                else {"type": "object"},
                "read": mcp_read_only.is_read(name, description),
            }
        )
    return out


async def token(row: ReachConnectionModel) -> str | None:
    """The bearer token for this connection, refreshed if it is due."""
    secret = vault.open_(row.secret_encrypted)
    if row.auth == "token":
        return secret.get("token")
    if row.auth == "oauth":
        if oauth.needs_refresh(secret):
            try:
                secret = await oauth.refresh(secret)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Refreshing a sign-in failed: {}", type(exc).__name__)
                await _save(
                    row,
                    status=ERROR,
                    last_error="The sign-in has expired. Connect it again.",
                )
                return None
            await _save(row, secret_encrypted=vault.seal(secret))
        return (secret.get("oauth") or {}).get("access_token")
    return None


async def read_tools(row: ReachConnectionModel) -> ReachConnectionModel:
    """Read the server's tools and mark the connection usable, or not."""
    try:
        listed = await wire.list_tools(row.server_url, await token(row))
    except wire.NeedsSignIn:
        return await _save(
            row,
            status=ERROR,
            last_error="The server did not accept the sign-in. Connect it again.",
        )
    except (wire.WireError, wire.ToolRefused) as exc:
        return await _save(row, status=ERROR, last_error=str(exc)[:300])
    return await _save(
        row,
        status=CONNECTED,
        tools=_describe(listed),
        last_error=None,
        connected_at=_now(),
    )


async def connect(
    *,
    organization_id: int,
    user_id: int,
    kind: str,
    provider: str,
    name: str,
    server_url: str,
    pasted_token: str | None = None,
    client_id: str | None = None,
) -> Started:
    """Connect this person to a server. Raises :class:`ConnectError`."""
    if kind not in KINDS:
        raise ConnectError("Not a kind of connection.")
    try:
        url = await safety.check_address(server_url)
    except safety.UnsafeAddress as exc:
        raise ConnectError(str(exc)) from exc
    pasted = (pasted_token or "").strip() or None
    try:
        await wire.probe(url, pasted)
        needs = None
    except wire.NeedsSignIn as exc:
        if pasted:
            raise ConnectError("That token was not accepted by the server.") from exc
        needs = exc
    except wire.WireError as exc:
        raise ConnectError(str(exc)) from exc

    row = await _replace(
        organization_id=organization_id,
        user_id=user_id,
        kind=kind,
        provider=provider,
        name=name or provider,
        server_url=url,
        auth="oauth" if needs is not None else ("token" if pasted else "none"),
    )
    try:
        if needs is None:
            if pasted:
                await _save(row, secret_encrypted=vault.seal({"token": pasted}))
            await read_tools(row)
            return Started(connection=row)
        authorize_url, state, secret = await oauth.begin(
            server_url=url,
            resource_metadata=needs.resource_metadata,
            client_id=client_id,
        )
        await _save(
            row,
            secret_encrypted=vault.seal(secret),
            oauth_state_hash=oauth.state_hash(state),
        )
        return Started(connection=row, authorize_url=authorize_url)
    except vault.VaultUnavailable as exc:
        await _save(row, status=ERROR, last_error=str(exc))
        raise ConnectError(str(exc)) from exc
    except oauth.SignInUnavailable as exc:
        await _save(row, status=ERROR, last_error=str(exc)[:300])
        raise ConnectError(str(exc)) from exc


async def finish_sign_in(state: str, code: str) -> ReachConnectionModel:
    """The sign-in came back. Finds the pending connection by the hash of
    its state -- nothing else in the request is trusted -- and finishes it."""
    if not state or not code:
        raise ConnectError("The sign-in did not come back complete.")
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(ReachConnectionModel).where(
                ReachConnectionModel.oauth_state_hash == oauth.state_hash(state),
                ReachConnectionModel.status == PENDING,
                ReachConnectionModel.revoked_at.is_(None),
            )
        )
    if row is None:
        raise ConnectError("This sign-in link has already been used or has expired.")
    secret = vault.open_(row.secret_encrypted)
    if oauth.state_expired(secret):
        await _save(
            row,
            status=ERROR,
            oauth_state_hash=None,
            last_error="The sign-in took too long. Try again.",
        )
        raise ConnectError("The sign-in took too long. Try again from the chat.")
    try:
        secret = await oauth.finish(secret, code)
    except (oauth.SignInUnavailable, safety.UnsafeAddress) as exc:
        await _save(row, status=ERROR, oauth_state_hash=None, last_error=str(exc)[:300])
        raise ConnectError(str(exc)) from exc
    await _save(row, secret_encrypted=vault.seal(secret), oauth_state_hash=None)
    return await read_tools(row)


async def revoke(organization_id: int, user_id: int, uuid: str) -> bool:
    async with db_client.async_session() as session:
        result = await session.execute(
            update(ReachConnectionModel)
            .where(
                ReachConnectionModel.organization_id == organization_id,
                ReachConnectionModel.user_id == user_id,
                ReachConnectionModel.uuid == str(uuid),
                ReachConnectionModel.revoked_at.is_(None),
            )
            .values(
                status=REVOKED,
                revoked_at=_now(),
                secret_encrypted=None,
                oauth_state_hash=None,
                updated_at=_now(),
            )
        )
        await session.commit()
    return bool(result.rowcount)


async def call(
    row: ReachConnectionModel, tool_name: str, arguments: dict[str, Any]
) -> Any:
    """Run one of the connection's tools. Raises the ``wire`` errors."""
    if row.status != CONNECTED:
        raise wire.WireError("This connection is not ready.")
    known = {t.get("name") for t in row.tools or []}
    if tool_name not in known:
        raise wire.ToolRefused(f"{row.name} has no tool called {tool_name}.")
    access = await token(row)
    if row.auth != "none" and not access:
        raise wire.NeedsSignIn()
    return await wire.call_tool(row.server_url, access, tool_name, arguments or {})


__all__ = [
    "CONNECTED",
    "ConnectError",
    "ERROR",
    "ORDERING",
    "PENDING",
    "REVOKED",
    "Started",
    "TOOL",
    "call",
    "connect",
    "finish_sign_in",
    "get",
    "live",
    "mine",
    "public",
    "read_tools",
    "revoke",
    "slug",
    "token",
]

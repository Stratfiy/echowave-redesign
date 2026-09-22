"""Connecting a business's own dialer, so its team's calls can be coached (CR-1).

Thin by design: the vault rules and the import live in
``services/dialer_import``. Every route is an admin's -- these are recordings
of the business's own staff -- resolves the organization from the session,
never the body, and is a 404 while DIALER_IMPORT_ENABLED is off, so nothing
about the feature is visible before its consent wording is approved.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import require_organization_role
from api.services.dialer_import import connections, importer
from api.services.dialer_import._base import DialerAuthError, DialerError

router = APIRouter(prefix="/dialer-connections", tags=["dialer-connections"])


def _enabled() -> None:
    if not constants.DIALER_IMPORT_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


_admin = require_organization_role(OrganizationRole.ADMIN)


class ConnectRequest(BaseModel):
    vendor: str = Field(..., description="exotel | smartflo")
    credentials: dict[str, str] = Field(
        ...,
        description=(
            "Exotel: api_key, api_token, account_sid, and subdomain if not "
            "api.exotel.com. Smartflo: api_token from the Smartflo portal."
        ),
    )
    label: str | None = Field(None, max_length=128)


def _view(connection: connections.DialerConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "vendor": connection.vendor,
        "label": connection.label,
        "key_last_four": connection.key_last_four,
        "status": connection.status,
        "last_error": connection.last_error,
        "last_synced_at": connection.last_synced_at,
    }


@router.get("", dependencies=[Depends(_enabled)])
async def list_dialer_connections(user: UserModel = Depends(_admin)) -> dict:
    async with db_client.async_session() as session:
        found = await connections.list_for(
            session, organization_id=user.selected_organization_id
        )
    return {"connections": [_view(c) for c in found]}


@router.post("", dependencies=[Depends(_enabled)])
async def connect_dialer(
    request: ConnectRequest, user: UserModel = Depends(_admin)
) -> dict:
    """Store the dialer's credentials, after one cheap listing proves them.

    A refused credential is not stored: a connection that can never import
    is a screen that says "connected" and a coach that stays silent. A
    dialer we cannot reach is stored and says so; the night will retry.
    """
    try:
        cleaned = connections.clean_credentials(request.vendor, request.credentials)
        adapter = connections.adapter_for(request.vendor)
    except connections.ConnectionError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    warning = None
    until = datetime.now(timezone.utc)
    try:
        await adapter.list_calls(cleaned, until - timedelta(hours=1), until)
    except DialerAuthError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except DialerError as exc:
        warning = exc.message

    async with db_client.async_session() as session:
        try:
            connection = await connections.create(
                session,
                organization_id=user.selected_organization_id,
                actor_user_id=user.id,
                vendor=request.vendor,
                credentials=cleaned,
                label=request.label,
            )
        except connections.ConnectionError_ as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
    return {"connection": _view(connection), "unverified": warning}


@router.delete("/{connection_id}", dependencies=[Depends(_enabled)])
async def disconnect_dialer(connection_id: int, user: UserModel = Depends(_admin)):
    async with db_client.async_session() as session:
        removed = await connections.remove(
            session,
            organization_id=user.selected_organization_id,
            connection_id=connection_id,
        )
        await session.commit()
    if not removed:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"removed": True}


@router.get("/calls", dependencies=[Depends(_enabled)])
async def list_imported_calls(days: int = 7, user: UserModel = Depends(_admin)):
    """What was imported, newest last, for checking the import worked."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 30)))
    async with db_client.async_session() as session:
        calls = await importer.calls_for(
            session, organization_id=user.selected_organization_id, since=since
        )
    return {
        "calls": [
            {
                "id": call.id,
                "vendor": call.vendor,
                "started_at": call.started_at,
                "duration_seconds": call.duration_seconds,
                "direction": call.direction,
                "agent_name": call.agent_name,
                "agent_number": call.agent_number,
                "customer_last_four": call.customer_last_four,
                "transcript": call.transcript,
            }
            for call in calls
        ]
    }

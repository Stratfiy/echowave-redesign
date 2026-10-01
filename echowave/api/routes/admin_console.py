"""Staff console reads that are not billing (ADMIN-2): the audit log and the
system strip.

**Every route here is staff-only**, declared once at router level so a new
endpoint added to this file is gated by default. Mounted under ``/admin`` and
tagged ``admin-…``, so it stays out of the public OpenAPI document
(``services/openapi_surface.py``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import admin_audit_client, db_client
from api.services import system_status
from api.services.auth.depends import get_superuser

router = APIRouter(
    prefix="/admin",
    tags=["admin-console"],
    dependencies=[Depends(get_superuser)],
)


@router.get("/audit")
async def read_audit_log(
    organization_id: int | None = Query(None, description="Only this account"),
    actor_user_id: int | None = Query(None, description="Only this staff member"),
    action: str | None = Query(None, max_length=48),
    before: str | None = Query(
        None,
        description=(
            "The `next_before` cursor from the previous page, or an ISO "
            "timestamp to start below"
        ),
    ),
    limit: int = Query(
        admin_audit_client.DEFAULT_LIMIT, ge=1, le=admin_audit_client.MAX_LIMIT
    ),
) -> dict[str, Any]:
    """Staff actions and billing changes, newest first, as one stream."""
    try:
        cursor = admin_audit_client.parse_cursor(before)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed `before`") from exc
    async with db_client.async_session() as session:
        page = await admin_audit_client.audit_page(
            session,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action or None,
            before=cursor,
            limit=limit,
        )
        page["actions"] = await admin_audit_client.known_actions(session)
    return page


@router.get("/system")
async def read_system_status() -> dict[str, Any]:
    """Build, database, Redis, worker heartbeat, ARQ queue and provider
    balances -- each probe time-boxed, none able to blank the page."""
    return await system_status.snapshot()

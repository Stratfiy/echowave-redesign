"""Ending an impersonation, audited (KAN-82, ADMIN-2 A4).

Not under ``/superuser``: by the time "Stop impersonating" is pressed the
browser holds only the *customer's* borrowed session, so this cannot be
``get_superuser``. ``services/auth/impersonation_audit.py`` explains what the
borrowed session may and may not record. Kept out of the public OpenAPI by
``INTERNAL_PATH_PREFIXES``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.db import db_client
from api.db.models import UserModel
from api.services.auth import impersonation_audit
from api.services.auth.depends import get_user

router = APIRouter(prefix="/impersonation", tags=["impersonation"])


@router.post("/stop")
async def stop_impersonation(
    request: Request, user: UserModel = Depends(get_user)
) -> dict:
    """Record ``impersonation_stopped`` for the open impersonation of the
    signed-in (borrowed) user. A no-op when there is none."""
    async with db_client.async_session() as session:
        return await impersonation_audit.record_stop(
            session,
            user_id=user.id,
            provider_id=user.provider_id,
            actor_ip=request.client.host if request.client else None,
        )

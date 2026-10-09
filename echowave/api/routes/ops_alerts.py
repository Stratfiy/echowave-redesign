"""The staff console's view of the operational alerts (services/ops_alerts).

A 404 while ``ops_alerts`` is off; staff only, and out of the public
OpenAPI (``/admin/`` path, ``admin-`` tag). Open alerts are for any staff
role; the daily cost summaries carry spend by workspace and are for
superadmins, like the rest of the billing screens.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_staff, get_superuser
from api.services.ops_alerts import views

router = APIRouter(
    prefix="/admin/ops/alerts",
    tags=["admin-ops"],
    dependencies=[Depends(features.require("ops_alerts")), Depends(get_staff)],
)


@router.get("")
async def open_alerts() -> dict[str, Any]:
    """Alerts open now, the last twenty resolved, and each watched tick."""
    return await views.alerts()


@router.get("/daily-summaries")
async def daily_summaries(
    _user: UserModel = Depends(get_superuser),
) -> dict[str, Any]:
    """The last seven daily cost summaries, newest first."""
    return await views.daily_summaries()

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import AdminActionLogModel, UserModel
from api.services.auth.depends import get_superuser
from api.services.auth.stack_auth import (
    StackAuthSessionError,
    StackAuthUserSearchError,
    stackauth,
)

router = APIRouter(prefix="/superuser", tags=["superuser"])


class ImpersonateRequest(BaseModel):
    """Request payload for superadmin impersonation.

    ``provider_user_id``, ``user_id``, or ``email`` may be supplied. If more
    than one is provided, ``provider_user_id`` takes precedence, followed by
    ``user_id`` and then ``email``.
    """

    provider_user_id: str | None = None
    user_id: int | None = None
    email: str | None = None


class ImpersonateResponse(BaseModel):
    refresh_token: str
    access_token: str


class SuperuserWorkflowRunResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: str | None
    user_id: int | None
    organization_id: int | None
    organization_name: str | None
    mode: str
    is_completed: bool
    recording_url: str | None
    transcript_url: str | None
    usage_info: dict | None
    cost_info: dict | None
    initial_context: dict | None
    gathered_context: dict | None
    created_at: datetime


class SuperuserWorkflowRunsListResponse(BaseModel):
    workflow_runs: list[SuperuserWorkflowRunResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


@router.post("/impersonate")
async def impersonate(
    request: ImpersonateRequest,
    http_request: Request,
    user: UserModel = Depends(get_superuser),
) -> ImpersonateResponse:
    """Impersonate a user as a super-admin.
    Internally, Stack Auth requires the **provider user ID** (a UUID-ish string)
    to create an impersonation session.
    """

    provider_user_id = (
        request.provider_user_id.strip() if request.provider_user_id else None
    ) or None
    email = request.email.strip().lower() if request.email else None

    # ------------------------------------------------------------------
    # Fallback: resolve provider_user_id from internal ``user_id`` or email.
    # ------------------------------------------------------------------
    if provider_user_id is None:
        if request.user_id is not None:
            db_user = await db_client.get_user_by_id(request.user_id)

            if db_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with ID {request.user_id} not found.",
                )

            provider_user_id = db_user.provider_id
        elif email:
            db_user = await db_client.get_user_by_email(email)

            if db_user is not None:
                provider_user_id = db_user.provider_id
            else:
                try:
                    stack_users = await stackauth.find_users_by_email(email)
                except StackAuthUserSearchError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to search Stack Auth users.",
                    ) from exc

                if len(stack_users) == 1 and isinstance(stack_users[0].get("id"), str):
                    provider_user_id = stack_users[0]["id"]
                elif len(stack_users) > 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Multiple Stack Auth users matched that email.",
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"User with email {email} not found.",
                    )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "One of 'provider_user_id', 'user_id', or 'email' must be provided."
                ),
            )

    # ------------------------------------------------------------------
    # KAN-82: never impersonate another staff account, and audit every start.
    # A superadmin borrowing a customer's session is the most powerful action
    # in the product; borrowing another *staff* member's session would let one
    # admin act as another with no separation, and every start must leave a
    # durable trace an account can be shown.
    # ------------------------------------------------------------------
    target_user = await db_client.get_user_by_provider_id(provider_user_id)
    if target_user is not None and getattr(target_user, "staff_role", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A staff account cannot be impersonated.",
        )
    try:
        async with db_client.async_session() as audit_session:
            audit_session.add(
                AdminActionLogModel(
                    actor_user_id=user.id,
                    action="impersonation_started",
                    target_user_id=getattr(target_user, "id", None),
                    target_provider_id=provider_user_id,
                    target_organization_id=getattr(
                        target_user, "selected_organization_id", None
                    ),
                    actor_ip=(
                        http_request.client.host if http_request.client else None
                    ),
                    note=(email or (target_user.email if target_user else None) or "")[
                        :500
                    ],
                )
            )
            await audit_session.commit()
    except Exception as exc:
        # A failed audit write must not let an UNAUDITED impersonation proceed:
        # the whole point is that none happens without a trace.
        logger.error("Could not audit impersonation start: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not record this impersonation; not proceeding.",
        ) from exc

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    try:
        session = await stackauth.impersonate(provider_user_id)
    except StackAuthSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        ) from exc

    if (
        not isinstance(session, dict)
        or "refresh_token" not in session
        or "access_token" not in session
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        )

    return ImpersonateResponse(
        refresh_token=session["refresh_token"],
        access_token=session["access_token"],
    )


@router.get("/workflow-runs")
async def get_workflow_runs(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page"),
    filters: str | None = Query(None, description="JSON-encoded filter criteria"),
    sort_by: str | None = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: str | None = Query("desc", description="Sort order ('asc' or 'desc')"),
    user: UserModel = Depends(get_superuser),
) -> SuperuserWorkflowRunsListResponse:
    """
    Get paginated list of all workflow runs with organization information.
    Requires superuser privileges.

    Filters should be provided as a JSON-encoded array of filter criteria.
    Example: [{"field": "id", "type": "number", "value": {"value": 680}}]
    """
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = None
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

    # Validate sort_order
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    workflow_runs, total_count = await db_client.get_workflow_runs_for_superadmin(
        limit=limit,
        offset=offset,
        filters=filter_criteria,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    total_pages = (total_count + limit - 1) // limit  # Ceiling division

    return SuperuserWorkflowRunsListResponse(
        workflow_runs=[SuperuserWorkflowRunResponse(**run) for run in workflow_runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )

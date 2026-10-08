import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import AUTH_PROVIDER
from api.db import db_client
from api.db.models import AdminActionLogModel, UserModel
from api.services.auth import impersonation_tokens
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
    #: Why staff is looking (phase 3): recorded on the audit row. Required --
    #: a borrowed session with no stated reason cannot be reviewed.
    reason: str = Field(min_length=3, max_length=300)
    #: ``read_only`` (view as: every write is refused on the server) or
    #: ``full`` (act as the person). Read-only is the default.
    mode: str = Field(default="read_only", pattern="^(read_only|full)$")


class ImpersonateResponse(BaseModel):
    #: Stack Auth only; a local borrowed session is a single access token.
    refresh_token: str | None = None
    access_token: str
    mode: str = "full"
    #: ``stack`` or ``local``: which cookie the UI writes.
    auth_provider: str = "stack"
    expires_in_seconds: int = 3600


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
    #: ``granted`` when the workspace allows staff to read this call;
    #: otherwise ``consent_required`` and both URLs above are withheld.
    content_access: str = "consent_required"
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


async def _close_unstarted(actor_id, start_id, target_user, provider_user_id) -> None:
    """The start row is written before the session exists (so none exists
    unaudited). When the session then could not be made, close the row, or
    the console would show an impersonation open that never happened."""
    from api.services.auth.impersonation_audit import STOPPED

    try:
        async with db_client.async_session() as s:
            s.add(
                AdminActionLogModel(
                    actor_user_id=actor_id,
                    action=STOPPED,
                    target_user_id=getattr(target_user, "id", None),
                    target_provider_id=provider_user_id,
                    note=f"never started: the session could not be made (start #{start_id})",
                )
            )
            await s.commit()
    except Exception as exc:  # noqa: BLE001 -- the 502 is the answer either way
        logger.error("Could not close an unstarted impersonation: {}", exc)


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
    local = AUTH_PROVIDER == "local"

    if not local and request.mode == "read_only":
        # A Stack session carries none of our claims, so read-only could not
        # be enforced on it; refuse rather than hand over a full session.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Read-only view needs local sign-in on this deployment.",
        )

    if local and provider_user_id is None:
        # Local accounts are found here, never in Stack (which is not
        # configured): by id, else by address.
        found = (
            await db_client.get_user_by_id(request.user_id)
            if request.user_id is not None
            else await db_client.get_user_by_email(email)
            if email
            else None
        )
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND
                if (request.user_id is not None or email)
                else status.HTTP_400_BAD_REQUEST,
                detail="User not found."
                if (request.user_id is not None or email)
                else "One of 'provider_user_id', 'user_id', or 'email' must be provided.",
            )
        provider_user_id = found.provider_id

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
    if local and target_user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    try:
        async with db_client.async_session() as audit_session:
            start = AdminActionLogModel(
                actor_user_id=user.id,
                action="impersonation_started",
                target_user_id=getattr(target_user, "id", None),
                target_provider_id=provider_user_id,
                target_organization_id=getattr(
                    target_user, "selected_organization_id", None
                ),
                actor_ip=(http_request.client.host if http_request.client else None),
                note=impersonation_tokens.note_for(
                    request.mode,
                    request.reason,
                    email or (target_user.email if target_user else None),
                ),
            )
            audit_session.add(start)
            await audit_session.flush()
            start_id = start.id
            await audit_session.commit()
    except Exception as exc:
        # A failed audit write must not let an UNAUDITED impersonation proceed:
        # the whole point is that none happens without a trace.
        logger.error("Could not audit impersonation start: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not record this impersonation; not proceeding.",
        ) from exc

    if local:
        # A local borrowed session: the person's own token plus who is really
        # there, the mode and the audit row (impersonation_tokens.py).
        return ImpersonateResponse(
            access_token=impersonation_tokens.mint(
                user_id=target_user.id,
                email=target_user.email,
                actor_user_id=user.id,
                mode=request.mode,
                start_id=start_id,
            ),
            mode=request.mode,
            auth_provider="local",
        )

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    try:
        session = await stackauth.impersonate(provider_user_id)
    except StackAuthSessionError as exc:
        await _close_unstarted(user.id, start_id, target_user, provider_user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        ) from exc

    if (
        not isinstance(session, dict)
        or "refresh_token" not in session
        or "access_token" not in session
    ):
        await _close_unstarted(user.id, start_id, target_user, provider_user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        )

    return ImpersonateResponse(
        refresh_token=session["refresh_token"],
        access_token=session["access_token"],
        mode=request.mode,
        auth_provider="stack",
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

    # A call's recording and transcript are the customer's conversation:
    # handed out only under the workspace's consent (phase 3).
    from api.services.staff import call_content

    async with db_client.async_session() as session:
        granted = await call_content.granted_runs(
            session, [(run["id"], run.get("organization_id")) for run in workflow_runs]
        )
    for run in workflow_runs:
        if run["id"] in granted:
            run["content_access"] = "granted"
        else:
            run["content_access"] = "consent_required"
            run["recording_url"] = None
            run["transcript_url"] = None

    return SuperuserWorkflowRunsListResponse(
        workflow_runs=[SuperuserWorkflowRunResponse(**run) for run in workflow_runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# Invite codes (INVITE-1, KAN-273)
# ---------------------------------------------------------------------------


class MintInvitesRequest(BaseModel):
    #: How many single-address codes to mint (1-200).
    count: int = 1
    #: Pin the code to one address; only with count == 1.
    email: str | None = None
    #: Accounts one code admits; 1 unless it is a code for a group.
    max_uses: int = 1
    #: Who it is for or where it went ("Anna Nagar dental, via Ravi").
    note: str | None = None
    expires_at: datetime | None = None


@router.post("/invites")
async def mint_invites(
    body: MintInvitesRequest,
    user: UserModel = Depends(get_superuser),
) -> dict:
    """Mint invite codes. Each is shown once here and again in the list."""
    from api.services.auth import signup_invites

    try:
        rows = await signup_invites.mint(
            count=body.count,
            created_by_user_id=user.id,
            email=body.email,
            max_uses=body.max_uses,
            note=body.note,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        async with db_client.async_session() as audit_session:
            audit_session.add(
                AdminActionLogModel(
                    actor_user_id=user.id,
                    action="invites_minted",
                    note=f"{len(rows)} code(s); max_uses={body.max_uses}; "
                    f"{(body.note or '')[:300]}",
                )
            )
            await audit_session.commit()
    except Exception:
        logger.exception("Could not write the audit row for minted invites")

    return {
        "codes": [signup_invites.display(r.code) for r in rows],
        "invite_only": signup_invites.required(),
    }


@router.get("/invites")
async def list_invites(user: UserModel = Depends(get_superuser)) -> dict:
    from api.services.auth import signup_invites

    return {
        "invites": await signup_invites.list_invites(),
        "invite_only": signup_invites.required(),
    }


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(
    invite_id: int, user: UserModel = Depends(get_superuser)
) -> dict:
    from api.services.auth import signup_invites

    if not await signup_invites.revoke(invite_id):
        raise HTTPException(
            status_code=404, detail="No such invite, or already revoked"
        )
    try:
        async with db_client.async_session() as audit_session:
            audit_session.add(
                AdminActionLogModel(
                    actor_user_id=user.id,
                    action="invite_revoked",
                    note=f"invite {invite_id}",
                )
            )
            await audit_session.commit()
    except Exception:
        logger.exception("Could not write the audit row for a revoked invite")
    return {"revoked": invite_id}


# ---------------------------------------------------------------------------
# Trial window (PLAN-1, KAN-255)
# ---------------------------------------------------------------------------


class TrialOverrideRequest(BaseModel):
    #: The new end, or null to go back to the computed window.
    ends_at: datetime | None = None
    #: Or: extend by this many days from now (wins over ends_at).
    extend_days: int | None = None
    note: str | None = None


@router.post("/organizations/{organization_id}/trial")
async def set_trial_end(
    organization_id: int,
    body: TrialOverrideRequest,
    user: UserModel = Depends(get_superuser),
) -> dict:
    """Extend or reset an account's trial. Writes an audit row."""
    from datetime import UTC, timedelta

    from api.db.models import OrganizationModel
    from api.services.billing import trial as trial_service

    if body.extend_days is not None and not (1 <= body.extend_days <= 365):
        raise HTTPException(status_code=400, detail="extend_days must be 1-365")
    ends_at = (
        datetime.now(UTC) + timedelta(days=body.extend_days)
        if body.extend_days is not None
        else body.ends_at
    )
    async with db_client.async_session() as session:
        org = await session.get(OrganizationModel, organization_id)
        if org is None:
            raise HTTPException(status_code=404, detail="No such organization")
        org.trial_ends_at = ends_at
        session.add(
            AdminActionLogModel(
                actor_user_id=user.id,
                action="trial_end_set",
                target_organization_id=organization_id,
                note=f"ends_at={ends_at.isoformat() if ends_at else 'computed'}; "
                f"{(body.note or '')[:300]}",
            )
        )
        await session.commit()
        status_ = await trial_service.status(session, organization_id=organization_id)
    return {"organization_id": organization_id, "trial": status_.as_dict()}


@router.get("/aws-gateway")
async def aws_gateway_status(user: UserModel = Depends(get_superuser)):
    """Claude and other models through AWS: each part's state and, when it is
    not ready, the step that makes it ready (stream aws-gateway).

    Configuration and Bedrock model access only; nothing here calls AWS.
    """
    from api.services.aws_gateway import config as aws_config

    return aws_config.overview()

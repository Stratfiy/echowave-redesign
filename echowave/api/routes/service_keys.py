from typing import List

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db.models import UserModel
from api.schemas.service_key import (
    CreateServiceKeyRequest,
    CreateServiceKeyResponse,
    ServiceKeyResponse,
)
from api.services.auth.depends import get_user
from api.services.mps_service_key_client import mps_service_key_client

router = APIRouter()


#: What a service key actually is, said once so the three handlers below can
#: agree. It is a credential *for the Model Proxy Service* — the Decibyl
#: provider's ``api_key`` is one of these, and ``validate_service_key`` checks
#: it against MPS's own usage endpoint. So on a deployment that cannot reach
#: MPS these are not broken; they are meaningless, and the difference is the
#: whole of what this screen should say. Reporting "Failed to retrieve service
#: keys" sends somebody to debug a feature that has nothing to do for them.
MPS_UNREACHABLE_DETAIL = (
    "Service keys are issued by the Decibyl model service, which this "
    "deployment cannot reach. They are only needed for Decibyl-managed models "
    "— if you bring your own provider keys, you do not need one. Set "
    "MPS_API_URL to a reachable service to use this screen."
)


def _is_unreachable(exc: Exception) -> bool:
    """Whether this failure is "no MPS here" rather than "MPS said no"."""
    import httpx

    return isinstance(
        exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)
    )


@router.get("/user/service-keys", response_model=List[ServiceKeyResponse])
async def get_service_keys(
    include_archived: bool = False,
    user: UserModel = Depends(get_user),
):
    """Get all service keys for the user's organization."""
    try:
        # For OSS mode, use provider_id as created_by
        # For authenticated mode, use organization_id
        if DEPLOYMENT_MODE == "oss":
            service_keys = await mps_service_key_client.get_service_keys(
                created_by=str(user.provider_id),
                include_archived=include_archived,
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")

            service_keys = await mps_service_key_client.get_service_keys(
                organization_id=user.selected_organization_id,
                include_archived=include_archived,
            )

        return [ServiceKeyResponse.model_validate(key) for key in service_keys]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get service keys: {e}")
        if _is_unreachable(e):
            raise HTTPException(status_code=503, detail=MPS_UNREACHABLE_DETAIL) from e
        raise HTTPException(
            status_code=502, detail="Failed to retrieve service keys"
        ) from e


@router.post("/user/service-keys", response_model=CreateServiceKeyResponse)
async def create_service_key(
    request: CreateServiceKeyRequest,
    user: UserModel = Depends(get_user),
):
    """Create a new service key for the user's organization."""
    try:
        # For OSS mode, don't pass organization_id
        # For authenticated mode, pass organization_id
        if DEPLOYMENT_MODE == "oss":
            result = await mps_service_key_client.create_service_key(
                name=request.name,
                created_by=str(user.provider_id),
                expires_in_days=request.expires_in_days or 90,
                description=f"Service key: {request.name}",
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")

            result = await mps_service_key_client.create_service_key(
                name=request.name,
                organization_id=user.selected_organization_id,
                created_by=str(user.provider_id),
                expires_in_days=request.expires_in_days or 90,
                description=f"Service key for organization {user.selected_organization_id}",
            )

        return CreateServiceKeyResponse.model_validate(result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create service key: {e}")
        if _is_unreachable(e):
            raise HTTPException(status_code=503, detail=MPS_UNREACHABLE_DETAIL) from e
        raise HTTPException(
            status_code=502, detail="Failed to create service key"
        ) from e


@router.delete("/user/service-keys/{service_key_id}")
async def archive_service_key(
    service_key_id: str,  # Changed from int to str since MPS uses string IDs
    user: UserModel = Depends(get_user),
):
    """Archive a service key."""
    try:
        # For OSS mode, use provider_id as created_by for validation
        # For authenticated mode, use organization_id for validation
        if DEPLOYMENT_MODE == "oss":
            success = await mps_service_key_client.archive_service_key(
                key_id=service_key_id,
                created_by=str(user.provider_id),
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")

            success = await mps_service_key_client.archive_service_key(
                key_id=service_key_id,
                organization_id=user.selected_organization_id,
            )

        if not success:
            raise HTTPException(
                status_code=404,
                detail="Service key not found, already archived, or access denied",
            )

        return {"message": "Service key archived successfully"}

    except HTTPException:
        # A 404 from the block above is the answer, not an error to relabel.
        raise
    except Exception as e:
        logger.error(f"Failed to archive service key: {e}")
        if _is_unreachable(e):
            raise HTTPException(status_code=503, detail=MPS_UNREACHABLE_DETAIL) from e
        # str(e) on an httpx failure names our internal host. It went to the
        # log a line ago; it does not go to the customer.
        raise HTTPException(
            status_code=502, detail="Failed to archive service key"
        ) from e


@router.put("/user/service-keys/{service_key_id}/reactivate")
async def reactivate_service_key(
    service_key_id: str,  # Changed from int to str since MPS uses string IDs
    user: UserModel = Depends(get_user),  # Kept for consistency but not used
):
    """
    Reactivate an archived service key.

    Note: This endpoint is provided for API compatibility but service key
    reactivation is not supported by MPS. Once archived, a service key
    cannot be reactivated and a new key must be created instead.
    """
    # MPS does not support reactivation of archived service keys
    raise HTTPException(
        status_code=501,  # Not Implemented
        detail="Service key reactivation is not supported. Once a service key is archived, it cannot be reactivated. Please create a new service key instead.",
    )

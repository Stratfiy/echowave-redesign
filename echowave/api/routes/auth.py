from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.constants import ENABLE_SIGNUP
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MfaDisableRequest,
    MfaEnrollResponse,
    MfaVerifyRequest,
    SignupRequest,
    UserResponse,
)
from api.services.auth import mfa
from api.services.auth.depends import (
    create_user_configuration_with_mps_key,
    get_user,
    require_local_auth,
)
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
)
from api.services.posthog_client import capture_event
from api.utils.auth import create_jwt_token, hash_password, verify_password

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


@router.post(
    "/signup",
    response_model=AuthResponse,
    dependencies=[Depends(require_local_auth)],
)
async def signup(request: SignupRequest):
    if not ENABLE_SIGNUP:
        raise HTTPException(status_code=403, detail="Signup is disabled")

    # Check if email is already taken
    existing_user = await db_client.get_user_by_email(request.email)
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Hash password and create user
    hashed = hash_password(request.password)
    user = await db_client.create_user_with_email(
        email=request.email,
        password_hash=hashed,
        name=request.name,
    )

    # Create organization for the user
    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    # Link user to organization
    await db_client.add_user_to_organization(user.id, organization.id)
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Create default service configuration
    try:
        mps_config = await create_user_configuration_with_mps_key(
            user.id, organization.id, user.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(user.id, mps_config)
            model_config_v2 = convert_legacy_ai_model_configuration_to_v2(mps_config)
            await db_client.upsert_configuration(
                organization.id,
                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                model_config_v2.model_dump(mode="json", exclude_none=True),
            )
    except Exception:
        logger.warning(
            "Failed to create default configuration for OSS user", exc_info=True
        )

    # Create JWT token
    token = create_jwt_token(user.id, request.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_UP,
        properties={
            "organization_id": organization.id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=request.name,
            organization_id=organization.id,
            provider_id=user.provider_id,
        ),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(require_local_auth)],
)
async def login(request: LoginRequest):
    # Look up user by email
    user = await db_client.get_user_by_email(request.email)
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Second factor, if the account has one. Checked after the password so a
    # wrong password and a wrong code are indistinguishable to an attacker
    # probing which accounts have MFA enabled.
    if user.mfa_enabled and user.mfa_secret_encrypted:
        if not request.mfa_code:
            # 401 with a machine-readable reason rather than an error: the
            # client needs to know to prompt, and the credentials were correct.
            raise HTTPException(
                status_code=401,
                detail="mfa_required",
                headers={"X-MFA-Required": "totp"},
            )
        if not await _accept_second_factor(user, request.mfa_code):
            raise HTTPException(status_code=401, detail="Invalid authentication code")

    # Create JWT token
    token = create_jwt_token(user.id, user.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_IN,
        properties={
            "organization_id": user.selected_organization_id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            organization_id=user.selected_organization_id,
            provider_id=user.provider_id,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(user: UserModel = Depends(get_user)):
    return UserResponse(
        id=user.id,
        email=user.email,
        organization_id=user.selected_organization_id,
        provider_id=user.provider_id,
    )


async def _accept_second_factor(user: UserModel, code: str) -> bool:
    """A TOTP code, or one of the account's recovery codes.

    Both are single-use. The TOTP counter is recorded so a code cannot be
    replayed inside its own 30-second step, and a spent recovery code is removed
    — one that survived its use would be a permanent bypass.
    """
    secret = mfa.decrypt_secret(user.mfa_secret_encrypted)
    counter = mfa.verify_code(
        secret=secret, code=code, last_counter=user.mfa_last_counter
    )
    if counter is not None:
        await db_client.update_user_mfa(user.id, mfa_last_counter=counter)
        return True

    remaining = list(user.mfa_recovery_hashes or [])
    spent = mfa.verify_recovery_code(code, remaining)
    if spent is None:
        return False

    remaining.remove(spent)
    await db_client.update_user_mfa(user.id, mfa_recovery_hashes=remaining)
    logger.warning(
        "User {} signed in with a recovery code; {} remaining", user.id, len(remaining)
    )
    return True


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def enroll_mfa(user: UserModel = Depends(get_user)):
    """Start enrolment. Nothing is enabled until a code is verified.

    Enabling on the strength of the QR alone locks out anyone whose scan
    silently failed, and the recovery codes would be the only way back — which
    is precisely the situation they exist to avoid, not to create.
    """
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled")

    enrollment = mfa.begin_enrollment(email=user.email or str(user.id))
    await db_client.update_user_mfa(
        user.id,
        mfa_secret_encrypted=mfa.encrypt_secret(enrollment.secret),
        mfa_enabled=False,
        mfa_recovery_hashes=[
            mfa.hash_recovery_code(code) for code in enrollment.recovery_codes
        ],
    )
    return MfaEnrollResponse(
        secret=enrollment.secret,
        uri=enrollment.uri,
        recovery_codes=list(enrollment.recovery_codes),
    )


@router.post("/mfa/verify")
async def verify_mfa(
    request: MfaVerifyRequest, user: UserModel = Depends(get_user)
) -> dict:
    """Finish enrolment by proving the authenticator works."""
    if not user.mfa_secret_encrypted:
        raise HTTPException(status_code=409, detail="Start enrolment first")

    secret = mfa.decrypt_secret(user.mfa_secret_encrypted)
    counter = mfa.verify_code(
        secret=secret, code=request.code, last_counter=user.mfa_last_counter
    )
    if counter is None:
        raise HTTPException(status_code=400, detail="Invalid authentication code")

    await db_client.update_user_mfa(user.id, mfa_enabled=True, mfa_last_counter=counter)
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
async def disable_mfa(
    request: MfaDisableRequest, user: UserModel = Depends(get_user)
) -> dict:
    """Turn MFA off. Needs the password *and* a current code.

    A valid session alone must not be enough: if it were, a stolen token could
    strip the second factor and the protection would end at the first XSS.
    """
    if not user.password_hash or not verify_password(
        request.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid password")
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise HTTPException(status_code=409, detail="MFA is not enabled")
    if not await _accept_second_factor(user, request.code):
        raise HTTPException(status_code=401, detail="Invalid authentication code")

    await db_client.update_user_mfa(
        user.id,
        mfa_enabled=False,
        mfa_secret_encrypted=None,
        mfa_last_counter=None,
        mfa_recovery_hashes=None,
    )
    logger.info("MFA disabled for user {}", user.id)
    return {"mfa_enabled": False}

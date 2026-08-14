from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import BACKEND_API_ENDPOINT, ENABLE_SIGNUP, UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MfaDisableRequest,
    MfaEnrollResponse,
    MfaVerifyRequest,
    SignupRequest,
    UserResponse,
)
from api.services.auth import (
    email_verification,
    email_verification_flow,
    google_oauth,
    invitations,
    mfa,
)
from api.services.auth.depends import (
    get_user,
    require_local_auth,
)
from api.services.auth.provisioning import provision_new_account
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

    # Organization, membership and default configuration. Shared with the
    # Google callback so both doors produce an identically-provisioned account.
    organization = await provision_new_account(user)

    # Send the verification code, best effort.
    #
    # Signup completes either way. A mail server having a bad minute must not
    # cost somebody their account — they are already provisioned, already
    # signed in, and can ask for another code from inside the app. Failing the
    # signup here would turn a transient SMTP fault into a lost customer.
    await email_verification_flow.issue_code(user.id, request.email)

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


class AcceptInvitationRequest(BaseModel):
    """Claim an account somebody else created, by setting its password."""

    email: str = Field(..., max_length=320)
    code: str = Field(..., min_length=4, max_length=12)
    password: str = Field(..., min_length=8, max_length=256)
    name: str | None = Field(None, max_length=200)


@router.post(
    "/accept-invitation",
    response_model=AuthResponse,
    dependencies=[Depends(require_local_auth)],
)
async def accept_invitation(request: AcceptInvitationRequest):
    """Set a password on an invited account and sign in.

    Deliberately *not* gated on ``ENABLE_SIGNUP``. That switch closes the
    self-serve door — anyone off the internet making themselves an account —
    and this door is the opposite: it can only be walked through by someone an
    owner or staff member already invited, holding a code they were sent.
    Gating it would make a deployment with signup closed unable to add anyone
    at all, which is the configuration most likely to need this.

    The email is marked verified on success. Receiving the code *is* proof of
    the address, and asking for a second code to prove the same fact is asking
    the user to do the same thing twice.
    """
    async with db_client.async_session() as session:
        try:
            accepted = await invitations.accept(
                session, email=request.email, code=request.code
            )
        except invitations.InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()

    hashed = hash_password(request.password)
    user = await db_client.get_user_by_email(accepted.email)
    if user is None:
        user = await db_client.create_user_with_email(
            email=accepted.email, password_hash=hashed, name=request.name
        )
    else:
        # A row already existed with no password — an earlier invitation, or a
        # Google sign-in on the same address. Setting the password adds a way
        # in; it does not take the other one away.
        await db_client.set_user_password(user.id, hashed)

    if accepted.organization_id is None:
        # Staff created the account rather than an owner adding a colleague, so
        # there is no organization yet. Provisioned here rather than at
        # invitation time so an unaccepted invitation leaves nothing behind.
        organization = await provision_new_account(user)
    else:
        # `role` may be None when the invitation predates roles or came from
        # a caller that did not name one; the client's own default is the
        # right answer then, rather than passing None into a NOT NULL column.
        if accepted.role:
            await db_client.add_user_to_organization(
                user.id, accepted.organization_id, role=accepted.role
            )
        else:
            await db_client.add_user_to_organization(user.id, accepted.organization_id)
        await db_client.update_user_selected_organization(
            user.id, accepted.organization_id
        )
        organization = await db_client.get_organization_by_id(accepted.organization_id)

    await db_client.mark_email_verified(user.id, verified_at=datetime.now(UTC))

    token = create_jwt_token(user.id, user.email)
    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_UP,
        properties={
            "organization_id": organization.id if organization else None,
            "auth_provider": "invitation",
        },
    )
    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=request.name,
            organization_id=organization.id if organization else None,
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


# ── Sign in with Google ──────────────────────────────────────────────────
#
# Two routes, both gated on local auth: this is an additional door into a
# local-auth account, not a second identity system. A deployment running Stack
# Auth already has social login and does not go through here.


def _redirect_uri() -> str:
    """Where Google sends the browser back.

    Derived rather than configured so it cannot disagree with the URL the app
    is actually served from — a mismatch here fails at Google with an error the
    user cannot act on, and it is the single most common way this integration
    is misconfigured.
    """
    return f"{BACKEND_API_ENDPOINT}/api/v1/auth/google/callback"


@router.get("/google/start", dependencies=[Depends(require_local_auth)])
async def google_start(next: str | None = None) -> dict:
    """Begin sign-in. Returns the URL to send the browser to."""
    try:
        return {
            "authorization_url": google_oauth.build_authorization_url(
                redirect_uri=_redirect_uri(), next_path=next
            )
        }
    except google_oauth.GoogleAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/google/callback", dependencies=[Depends(require_local_auth)])
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Complete sign-in and hand the browser back to the app with a token.

    Always a redirect, never JSON: the browser arrives here from Google, so a
    JSON body would leave the user staring at raw text. Failures go back to the
    login page carrying a message it can render.
    """
    if error or not code or not state:
        # `error` is Google's own — most often the user pressing Cancel, which
        # is not a failure worth alarming them about.
        return _google_failure(
            "Sign-in was cancelled."
            if error == "access_denied"
            else "Sign-in could not be completed."
        )

    try:
        identity, next_path = await google_oauth.complete_sign_in(
            code=code, state=state, redirect_uri=_redirect_uri()
        )
    except google_oauth.GoogleAuthError as exc:
        return _google_failure(str(exc))

    user = await db_client.get_user_by_email(identity.email)
    if user is None:
        if not ENABLE_SIGNUP:
            return _google_failure("Signup is disabled on this deployment.")
        user = await db_client.create_user_with_email(
            email=identity.email, password_hash=None, name=identity.name
        )
        organization = await provision_new_account(user)
        event = PostHogEvent.SIGNED_UP

    # Google already proved this address — complete_sign_in refuses any token
    # whose email Google has not verified, which is the check that stands
    # between us and somebody claiming an address on a domain Google does not
    # control. Mailing a code to confirm what we just confirmed would be
    # theatre, and would leave every Google account permanently unverified.
    if getattr(user, "email_verified_at", None) is None:
        await db_client.mark_email_verified(user.id, verified_at=datetime.now(UTC))
    else:
        # Linking to an existing account, which is only safe because
        # complete_sign_in has already refused any unverified email — see the
        # module docstring in services/auth/google_oauth.py. An account with a
        # password keeps it; this adds a way in rather than replacing one.
        # A returning account already has its organization selected. Only an
        # account that never finished provisioning — a signup that failed
        # part-way — needs it built now, and doing it here is what stops that
        # user being permanently stuck at a login that succeeds into nothing.
        organization = (
            await db_client.get_organization_by_id(user.selected_organization_id)
            if user.selected_organization_id
            else None
        )
        if organization is None:
            organization = await provision_new_account(user)
        event = PostHogEvent.SIGNED_IN

    token = create_jwt_token(user.id, identity.email)
    capture_event(
        distinct_id=str(user.provider_id),
        event=event,
        properties={"organization_id": organization.id, "auth_provider": "google"},
    )
    logger.info("Google sign-in for user {} ({})", user.id, identity.email)

    return RedirectResponse(
        url=f"{UI_APP_URL}/auth/google?token={quote(token)}"
        + (f"&next={quote(next_path)}" if next_path else ""),
        status_code=303,
    )


def _google_failure(message: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{UI_APP_URL}/auth/login?error={quote(message)}", status_code=303
    )


# ---------------------------------------------------------------------------
# Email verification
#
# Nothing refuses an unverified account yet, deliberately. Every account that
# predates this has email_verified_at NULL, and locking those people out to
# enforce a rule introduced after they signed up is an outage rather than a
# security improvement. What exists here is the proof and the record; what to
# withhold from an unverified account is a separate, reversible decision.
# ---------------------------------------------------------------------------


class VerifyEmailRequest(BaseModel):
    code: str = Field(..., max_length=12)


@router.post("/email/verify", dependencies=[Depends(require_local_auth)])
async def verify_email(
    request: VerifyEmailRequest, user: UserModel = Depends(get_user)
) -> dict:
    try:
        await email_verification.confirm_verification(user.id, request.code)
    except email_verification.TooManyAttempts as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except email_verification.EmailVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"email_verified": True}


@router.post("/email/resend", dependencies=[Depends(require_local_auth)])
async def resend_email_verification(user: UserModel = Depends(get_user)) -> dict:
    """Send another code.

    Reports whether it went out rather than raising on a refusal: the rate
    limits live in the service, and "you asked a moment ago" is an answer, not
    a failure. The response deliberately does not distinguish a cooldown from a
    send ceiling — both mean wait, and separating them only tells someone
    probing the endpoint how close they are to the wall.
    """
    sent = await email_verification_flow.issue_code(user.id, user.email or "")
    return {"sent": sent}

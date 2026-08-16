from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    #: A partner's referral code, carried from `?ref=` on the signup link.
    #: Never validated here: an unknown code provisions an unattributed account
    #: rather than refusing the signup, because the person signing up did not
    #: choose the code and cannot fix it.
    referral_code: str | None = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # Supplied on the second step when the account has MFA enabled. Optional
    # rather than a separate endpoint so an existing client keeps working: it
    # simply receives the mfa_required response and prompts.
    mfa_code: str | None = None


class MfaEnrollResponse(BaseModel):
    """Shown exactly once. The secret is never retrievable afterwards — a
    support flow that can read it back is an account takeover waiting to be
    socially engineered."""

    secret: str
    uri: str
    recovery_codes: list[str]


class MfaVerifyRequest(BaseModel):
    code: str


class MfaDisableRequest(BaseModel):
    """Disabling needs the password as well as a current code. Otherwise a
    stolen session alone is enough to strip the second factor off."""

    password: str
    code: str


class UserResponse(BaseModel):
    id: int
    email: str | None
    name: str | None = None
    organization_id: int | None = None
    provider_id: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: UserResponse

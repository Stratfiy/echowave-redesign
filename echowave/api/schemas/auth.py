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
    def password_is_hashable(cls, v: str) -> str:
        """Both ends of what bcrypt will accept.

        The upper bound is not cosmetic. bcrypt takes at most 72 **bytes**, and
        the installed version (5.x) *raises* rather than truncating, so a
        password over the limit reached ``hash_password`` and came back to the
        user as "Internal Server Error" with nothing on the form mentioning a
        length — they retry the same password forever.

        Measured in bytes rather than characters because that is what bcrypt
        counts, and the difference is not academic here: 40 characters of
        Telugu, Hindi or Devanagari is over 80 bytes, so a character-length
        check would still 500 on exactly the customers this product is for.
        A password-manager passphrase trips it in plain ASCII too.

        Refused rather than silently truncated: truncating would mean the
        password and its 72-byte prefix both open the account, which is a
        weaker guarantee than the user believes they chose.
        """
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v.encode("utf-8")) > 72:
            raise ValueError(
                "Password is too long. It must be at most 72 bytes — "
                "roughly 72 characters, or fewer if it uses non-Latin script."
            )
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
    # Whether MFA is fully enrolled (a verified authenticator, not merely a
    # pending enrolment). Needed so a settings screen can render "enable" vs
    # "disable" without a second round trip.
    mfa_enabled: bool = False


class AuthResponse(BaseModel):
    token: str
    user: UserResponse

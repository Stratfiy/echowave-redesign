"""Sign in with Google, as an additional way into a local-auth account.

Identity stays here. This is not a migration to a hosted identity provider —
the user row, the organization, the session token and the password (if there
is one) are all still ours. Google is asked one question, "is this person who
they say they are", and the answer is turned into the same JWT the password
flow issues. That keeps two properties worth having: an air-gapped deployment
switches the feature off and loses nothing else, and no third party sits in the
path of every login for a platform with DPDP obligations.

Four decisions carry the security of this file:

* **The ID token is verified, not decoded.** Google's response is a signed JWT;
  reading its claims without checking the signature would let anyone who can
  reach the callback mint an account for any email. The signature is checked
  against Google's published keys, and the audience, issuer and expiry are
  checked with it.

* **``state`` is signed and short-lived.** It is the only thing standing
  between a user and a login CSRF, where an attacker completes a flow in the
  victim's browser and lands them in the attacker's account. Signing it with
  the same secret as our sessions means a forged one cannot survive the
  callback.

* **``nonce`` is round-tripped.** State proves the request came from us; the
  nonce proves *this* ID token was minted for *this* request, which is what
  stops a token captured elsewhere being replayed into our callback.

* **An account is linked only on a Google-verified email.** This is the one
  that would quietly lose an account. If we matched on the email claim alone,
  anyone who could get Google to issue a token carrying somebody else's
  address — which is possible on domains Google does not verify — could sign in
  as that person and inherit their organization, their credits and their call
  recordings. ``email_verified`` is Google asserting it owns that mailbox, and
  without it we refuse rather than guess.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from loguru import logger

from api.constants import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    OSS_JWT_SECRET,
)

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

#: Google signs its ID tokens under either spelling of the issuer, and which
#: one you get is not something to depend on.
VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

#: Only what is needed to identify a person. Asking for more would show up on
#: the consent screen as a reason to hesitate, and we have no use for it.
SCOPES = ("openid", "email", "profile")

#: How long a started sign-in stays completable. Long enough to read a consent
#: screen and pick an account, short enough that a leaked state is useless.
STATE_TTL_SECONDS = 600

_STATE_AUDIENCE = "decibyl:google-oauth-state"


class GoogleAuthError(Exception):
    """Sign-in could not be completed. The message is safe to show a user."""


def is_enabled() -> bool:
    """Whether Google sign-in is configured on this deployment.

    Both halves are required: a client id alone cannot complete the exchange,
    and half-configured is the state most likely to produce a confusing button
    that fails after the user has already left the site.
    """
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)


def _require_enabled() -> None:
    if not is_enabled():
        raise GoogleAuthError("Google sign-in is not configured on this deployment.")


@dataclass(frozen=True)
class GoogleIdentity:
    """The parts of a verified Google account we act on."""

    subject: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None


def _issue_state(*, nonce: str, next_path: str | None) -> str:
    """A signed, expiring state parameter.

    Carries the nonce so the callback can check the ID token was minted for
    this request without needing server-side storage — which would otherwise
    mean a Redis round trip on a path that must work while Redis is degraded.
    """
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "aud": _STATE_AUDIENCE,
            "nonce": nonce,
            "next": next_path or None,
            "iat": now,
            "exp": now + timedelta(seconds=STATE_TTL_SECONDS),
        },
        OSS_JWT_SECRET,
        algorithm="HS256",
    )


def _read_state(state: str) -> dict:
    try:
        return jwt.decode(
            state,
            OSS_JWT_SECRET,
            algorithms=["HS256"],
            audience=_STATE_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise GoogleAuthError(
            "This sign-in link has expired. Start again."
        ) from exc
    except jwt.InvalidTokenError as exc:
        # Deliberately vague to the user: the detail only helps someone
        # probing the callback.
        raise GoogleAuthError("This sign-in could not be verified. Start again.") from exc


def build_authorization_url(*, redirect_uri: str, next_path: str | None = None) -> str:
    """Where to send the browser to begin sign-in."""
    _require_enabled()
    nonce = secrets.token_urlsafe(24)
    query = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": _issue_state(nonce=nonce, next_path=next_path),
        "nonce": nonce,
        # Ask for an account choice rather than silently reusing whichever
        # Google session the browser happens to hold. People have more than one
        # account and land in the wrong one otherwise.
        "prompt": "select_account",
        # No refresh token is requested: we mint our own session on the way out
        # and never call Google again on this user's behalf.
        "access_type": "online",
    }
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(query)}"


async def _exchange_code(*, code: str, redirect_uri: str) -> str:
    """Trade the one-time code for an ID token. Returns the raw JWT."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.RequestError as exc:
            # An air-gapped or network-restricted box reaches exactly here, and
            # "could not reach Google" is the honest thing to say.
            raise GoogleAuthError("Could not reach Google to complete sign-in.") from exc

    if response.status_code != 200:
        logger.warning(
            "Google token exchange failed: {} {}",
            response.status_code,
            response.text[:300],
        )
        raise GoogleAuthError("Google rejected this sign-in. Start again.")

    id_token = response.json().get("id_token")
    if not id_token:
        raise GoogleAuthError("Google did not return an identity for this account.")
    return id_token


def _verify_id_token(id_token: str, *, expected_nonce: str) -> GoogleIdentity:
    """Check the signature and claims, then return the identity it asserts."""
    try:
        signing_key = PyJWKClient(JWKS_URI).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=GOOGLE_OAUTH_CLIENT_ID,
            issuer=VALID_ISSUERS,
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Google ID token failed verification: {}", exc)
        raise GoogleAuthError("Google's response could not be verified.") from exc
    except Exception as exc:  # JWKS fetch failures surface as transport errors
        logger.warning("Could not verify Google ID token: {}", exc)
        raise GoogleAuthError("Could not verify sign-in with Google.") from exc

    # Compared in constant time: the nonce is a secret for the length of one
    # sign-in, and an early-exit comparison leaks it a character at a time.
    if not secrets.compare_digest(str(claims.get("nonce") or ""), expected_nonce):
        raise GoogleAuthError("This sign-in could not be verified. Start again.")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("This Google account has no email address on it.")

    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=email,
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )


async def complete_sign_in(*, code: str, state: str, redirect_uri: str) -> tuple[GoogleIdentity, str | None]:
    """Verify a callback end to end.

    Returns the identity Google vouched for and the path the user was heading
    to when they started. Raises :class:`GoogleAuthError` for anything that
    should send them back to the login page with a message.
    """
    _require_enabled()
    state_claims = _read_state(state)
    id_token = await _exchange_code(code=code, redirect_uri=redirect_uri)
    identity = _verify_id_token(id_token, expected_nonce=state_claims["nonce"])

    if not identity.email_verified:
        # See the module docstring: this is the check that stops an account
        # being taken over by someone who can have Google issue a token
        # carrying an address it has not confirmed.
        raise GoogleAuthError(
            "Google has not verified the email address on this account, "
            "so it cannot be used to sign in."
        )

    return identity, state_claims.get("next")

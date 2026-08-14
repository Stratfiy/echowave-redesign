"""Accounts somebody else creates for you.

Until now the only doors into the system were self-serve signup and Google
first sign-in. Both are the new user acting for themselves, which leaves two
things nobody could do: an owner could not add a colleague to their own
organization, and staff could not stand up an account for a customer they had
just sold to. Both are ordinary, and both were impossible.

The awkward part is not creating the row — it is that the person on the other
end has no password. ``login`` deliberately refuses any account whose
``password_hash`` is falsy, which is what stops a Google-only account being
reachable by guessing at a placeholder we invented. So an invited user needs a
way to set one, and this module is that way.

**No password is ever invented for them.** The user row is created with no
password at all, exactly like a Google account, and stays unreachable by
password until the invitation is accepted. Generating a temporary password and
emailing it would put a working credential in an inbox, in the clear, forever.

**The code is stored salted-hashed, never in the clear**, with a TTL and an
attempt ceiling — the same shape as email verification and the markup change,
because these get the security decisions right or wrong together.

**Accepting is idempotent in the direction that matters.** A second acceptance
with a spent code fails; it does not silently reset the password of an account
that is now in use.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AccountInvitationModel, UserModel
from api.services.auth import otp

#: Long enough to survive a weekend, short enough that a forgotten invitation
#: in a mailbox is not a standing way in.
INVITATION_TTL_HOURS = 72

#: A six-digit code is a million guesses. The ceiling is what makes it a
#: credential rather than a formality.
MAX_ATTEMPTS = 5


class InvitationError(ValueError):
    """An invitation could not be created or accepted."""


@dataclass(frozen=True)
class IssuedInvitation:
    """A pending invitation and the code that accepts it.

    Carries the code because the caller has to send it. It is never stored and
    never returned by any read path.
    """

    email: str
    code: str
    expires_at: datetime
    organization_id: int | None
    is_new_user: bool


async def issue(
    session: AsyncSession,
    *,
    email: str,
    organization_id: int | None,
    invited_by: int | None,
    role: str | None = None,
    now: datetime | None = None,
) -> IssuedInvitation:
    """Stage an invitation and return its code. Sends nothing.

    Sending is the caller's job, so the rules here can be tested without a mail
    server and a delivery failure never has to be untangled from a validation
    one — the same split the markup change uses.
    """
    moment = now or datetime.now(UTC)
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise InvitationError("A valid email address is required.")

    existing_user = (
        await session.execute(select(UserModel).where(UserModel.email == email))
    ).scalar_one_or_none()

    # An account that can already sign in does not need a way to set a
    # password, and issuing one would be a way to take over any address we can
    # spell. Adding them to an organization is a membership change, which the
    # caller does directly.
    if existing_user is not None and existing_user.password_hash:
        raise InvitationError(
            f"{email} already has an account. Add them to the organization "
            "directly rather than inviting them."
        )

    # One live invitation per address. Replacing rather than adding is what
    # stops a stale code from an earlier attempt still working, and keeps
    # "resend" from meaning "now two codes are valid".
    for stale in (
        await session.execute(
            select(AccountInvitationModel).where(AccountInvitationModel.email == email)
        )
    ).scalars():
        await session.delete(stale)
    await session.flush()

    code = otp.generate_code()
    salt = otp.generate_salt()
    expires_at = moment + timedelta(hours=INVITATION_TTL_HOURS)

    session.add(
        AccountInvitationModel(
            email=email,
            organization_id=organization_id,
            role=role,
            code_hash=otp.hash_code(code, salt),
            code_salt=salt,
            expires_at=expires_at,
            invited_by=invited_by,
            created_at=moment,
        )
    )
    await session.flush()

    # The address, never the code. A log line carrying the code is a way for
    # anyone who can read logs to take the account.
    logger.info("Invitation issued for {} to organization {}", email, organization_id)
    return IssuedInvitation(
        email=email,
        code=code,
        expires_at=expires_at,
        organization_id=organization_id,
        is_new_user=existing_user is None,
    )


@dataclass(frozen=True)
class AcceptedInvitation:
    email: str
    organization_id: int | None
    role: str | None


async def accept(
    session: AsyncSession,
    *,
    email: str,
    code: str,
    now: datetime | None = None,
) -> AcceptedInvitation:
    """Check the code and consume the invitation.

    Deliberately does *not* set the password or create the membership. Those
    need the db_client's own session handling, and keeping this function to
    "was this code right, and what was it for" is what lets the rules be tested
    without a user table full of fixtures.
    """
    moment = now or datetime.now(UTC)
    email = (email or "").strip().lower()

    invitation = (
        await session.execute(
            select(AccountInvitationModel).where(AccountInvitationModel.email == email)
        )
    ).scalar_one_or_none()

    if invitation is None:
        raise InvitationError("No invitation is waiting for that address.")

    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if moment >= expires_at:
        await session.delete(invitation)
        await session.flush()
        raise InvitationError("That invitation has expired. Ask for a new one.")

    if invitation.attempts >= MAX_ATTEMPTS:
        await session.delete(invitation)
        await session.flush()
        raise InvitationError(
            "Too many incorrect codes. The invitation has been cancelled — "
            "ask for a new one."
        )

    expected = otp.hash_code((code or "").strip(), invitation.code_salt)
    # Constant time: a plain == leaks how much of a guess was right through how
    # long the comparison took.
    if not hmac.compare_digest(expected, invitation.code_hash):
        invitation.attempts += 1
        await session.flush()
        raise InvitationError("That code is not right.")

    accepted = AcceptedInvitation(
        email=invitation.email,
        organization_id=invitation.organization_id,
        role=invitation.role,
    )
    # Consumed on success, so a replayed acceptance cannot reset the password
    # of an account that is now in use.
    await session.delete(invitation)
    await session.flush()

    logger.info("Invitation accepted for {}", email)
    return accepted


def notice_subject() -> str:
    return "You have been invited to Decibyl"


def notice_body(invitation: IssuedInvitation, *, invited_by_email: str | None) -> str:
    """Says who invited them and what to do, not merely that a code exists.

    An unexplained code from a product somebody has never heard of reads as
    phishing, and the correct response to phishing is to ignore it.
    """
    who = f" by {invited_by_email}" if invited_by_email else ""
    return (
        f"You have been invited{who} to Decibyl, a platform for building voice "
        "agents.\n\n"
        f"Your code: {invitation.code}\n\n"
        "Enter it on the sign-in page along with a password of your choosing to "
        f"finish setting up your account. It expires in {INVITATION_TTL_HOURS} "
        "hours.\n\n"
        "If you were not expecting this, do nothing. No account can be used "
        "without this code, and it will expire on its own."
    )

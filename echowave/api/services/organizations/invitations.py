"""Inviting somebody into an organization.

The piece that did not exist. Membership was mirrored from Stack Auth in SaaS
mode and created once at signup otherwise, so a self-hosted organization had
exactly one member and no way to gain a second. Every role below Owner was
therefore unreachable — there was never anybody to be a member.

Three rules do most of the work here, and each of them is the fix for a way
this goes wrong:

- the token is stored hashed, because a pending invitation is a bearer
  credential for a seat in somebody's account;
- accepting requires being signed in as the address invited, because a link in
  a mailbox gets forwarded;
- an invitation expires, because one that does not is a credential with no
  end.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    OrganizationInvitationModel,
    OrganizationMembershipModel,
    UserModel,
)
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole

#: Long enough that guessing is not a strategy, short enough to paste.
_TOKEN_BYTES = 32

#: A week. Long enough to survive a holiday, short enough that a forgotten
#: invitation in an old mailbox stops working.
INVITATION_TTL = timedelta(days=7)


class InvitationError(Exception):
    """Something the rules do not allow. Routes surface the message as-is."""


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def invite(
    session: AsyncSession,
    *,
    organization_id: int,
    email: str,
    role: str,
    invited_by: int | None,
    now: datetime | None = None,
) -> tuple[OrganizationInvitationModel, str]:
    """Offer a seat. Returns the row and the raw token, which is shown once.

    The raw token is deliberately not stored and not returned again. If the
    email does not arrive, the invitation is revoked and reissued rather than
    recovered — the same trade an API key makes.
    """
    address = normalize_email(email)
    if "@" not in address:
        raise InvitationError("That does not look like an email address.")
    if role not in ORGANIZATION_ROLE_RANK:
        raise InvitationError(f"Unknown role: {role}")

    now = now or datetime.now(UTC)

    existing_member = await session.scalar(
        select(OrganizationMembershipModel)
        .join(UserModel, UserModel.id == OrganizationMembershipModel.user_id)
        .where(
            OrganizationMembershipModel.organization_id == organization_id,
            func.lower(UserModel.email) == address,
        )
    )
    if existing_member is not None:
        # Said plainly rather than silently succeeding: an Owner who meant to
        # change somebody's role should be told to do that, not left waiting
        # for an invitation the person will never need to accept.
        raise InvitationError(
            f"{address} is already in this organization. "
            "Change their role from the members list instead."
        )

    if await pending_for_email(session, organization_id, address) is not None:
        raise InvitationError(
            f"{address} already has an invitation waiting. "
            "Revoke it first if you want to send a new one."
        )

    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    row = OrganizationInvitationModel(
        organization_id=organization_id,
        email=address,
        role=role,
        token_hash=hash_token(raw),
        invited_by=invited_by,
        created_at=now,
        expires_at=now + INVITATION_TTL,
    )
    session.add(row)
    await session.flush()
    return row, raw


async def pending_for_email(
    session: AsyncSession, organization_id: int, email: str
) -> OrganizationInvitationModel | None:
    return await session.scalar(
        select(OrganizationInvitationModel).where(
            OrganizationInvitationModel.organization_id == organization_id,
            OrganizationInvitationModel.email == normalize_email(email),
            OrganizationInvitationModel.accepted_at.is_(None),
            OrganizationInvitationModel.revoked_at.is_(None),
        )
    )


async def pending_for_organization(
    session: AsyncSession, organization_id: int
) -> list[OrganizationInvitationModel]:
    """Outstanding offers, so the members list can show who is on the way.

    An invited person who has not accepted is invisible otherwise, which is how
    an Owner ends up inviting them a second time.
    """
    return list(
        (
            await session.scalars(
                select(OrganizationInvitationModel)
                .where(
                    OrganizationInvitationModel.organization_id == organization_id,
                    OrganizationInvitationModel.accepted_at.is_(None),
                    OrganizationInvitationModel.revoked_at.is_(None),
                )
                .order_by(OrganizationInvitationModel.created_at)
            )
        ).all()
    )


async def revoke(
    session: AsyncSession,
    *,
    organization_id: int,
    invitation_id: int,
    now: datetime | None = None,
) -> OrganizationInvitationModel:
    """Withdraw an offer.

    Scoped by ``organization_id`` rather than looked up by id alone: an id from
    a request never implies ownership, and revoking is a write against another
    tenant's row if it does.
    """
    row = await session.scalar(
        select(OrganizationInvitationModel).where(
            OrganizationInvitationModel.id == invitation_id,
            OrganizationInvitationModel.organization_id == organization_id,
        )
    )
    if row is None:
        raise InvitationError("Invitation not found.")
    if row.accepted_at is not None:
        raise InvitationError(
            "That invitation was already accepted. Remove the member instead."
        )
    row.revoked_at = now or datetime.now(UTC)
    await session.flush()
    return row


async def lookup(
    session: AsyncSession, raw_token: str, *, now: datetime | None = None
) -> OrganizationInvitationModel:
    """Resolve a token to a live invitation, or say why not.

    Every refusal is deliberately the same shape and none of them says whether
    some *other* invitation exists — the token is the only secret, and a
    response that distinguishes "expired" from "never existed" is a probe.
    Expiry is the one exception, because a person holding a genuinely expired
    link needs to know to ask for another.
    """
    now = now or datetime.now(UTC)
    row = await session.scalar(
        select(OrganizationInvitationModel).where(
            OrganizationInvitationModel.token_hash == hash_token(raw_token)
        )
    )
    if row is None or row.revoked_at is not None:
        raise InvitationError("This invitation link is not valid.")
    if row.accepted_at is not None:
        raise InvitationError("This invitation has already been used.")
    if row.expires_at <= now:
        raise InvitationError("This invitation has expired. Ask for a new one.")
    return row


async def accept(
    session: AsyncSession,
    *,
    raw_token: str,
    user: UserModel,
    now: datetime | None = None,
) -> OrganizationInvitationModel:
    """Take the seat.

    The accepting user's address must match the invitation. An invitation
    lands in a mailbox, and a mailbox forwards — without this, the seat goes
    to whoever opens the link first, which is not who the Owner invited.
    """
    now = now or datetime.now(UTC)
    invitation = await lookup(session, raw_token, now=now)

    if normalize_email(user.email or "") != invitation.email:
        raise InvitationError(
            f"This invitation was sent to {invitation.email}. "
            "Sign in as that address to accept it."
        )

    already = await session.scalar(
        select(OrganizationMembershipModel).where(
            OrganizationMembershipModel.user_id == user.id,
            OrganizationMembershipModel.organization_id == invitation.organization_id,
        )
    )
    if already is None:
        session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=invitation.organization_id,
                role=invitation.role,
                created_at=now,
            )
        )
    elif ORGANIZATION_ROLE_RANK.get(invitation.role, -1) > ORGANIZATION_ROLE_RANK.get(
        already.role, -1
    ):
        # They were already in, at less. An invitation is an offer from
        # somebody who could grant it, so honour the higher of the two — but
        # never move anybody *down*, which would turn a stale invitation into
        # a demotion.
        already.role = invitation.role

    invitation.accepted_at = now
    invitation.accepted_by = user.id

    # Land them in the organization they were just invited to, rather than
    # leaving them looking at whichever one they had selected — accepting an
    # invitation and then appearing to be somewhere else reads as a failure.
    user.selected_organization_id = invitation.organization_id

    await session.flush()
    return invitation


def role_choices() -> list[str]:
    """Roles an invitation may carry, least privileged first."""
    return sorted(
        (r.value for r in OrganizationRole), key=lambda v: ORGANIZATION_ROLE_RANK[v]
    )

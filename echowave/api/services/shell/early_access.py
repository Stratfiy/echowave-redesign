"""Early access: the waitlist and what an invitation link says (screen 01).

Two public answers, both behind the ``early_access`` flag:

- ``join`` takes one request per address. A second submission (a double
  click, a second tab, a reload) returns the first row's state and creates
  nothing, because the unique index on ``lower(email)`` decides, not the
  button.
- ``invite_status`` reads a code without spending it, so the invitation page
  can say "valid until 14 October, for n***@example.com" or "expired" before
  anyone signs up. Spending stays in ``signup_invites.claim``, at signup,
  where the bound email and expiry are enforced again.

Nothing here says whether an address has an account beyond what the screen
needs: "already registered" points at sign-in, and the invited state never
returns a code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.shell_models import WaitlistRequestModel
from api.db.signup_invite_models import SignupInviteModel
from api.services.auth import signup_invites
from api.services.shell import languages

FLAG = "early_access"

#: What ``join`` can say. The screen has a sentence for each.
WAITLISTED = "waitlisted"
ALREADY_REGISTERED = "already_registered"
INVITED = "invited"

#: What ``invite_status`` can say.
VALID = "valid"
EXPIRED = "expired"
REVOKED = "revoked"
USED = "used"
INVALID = "invalid"

_PHONE = re.compile(r"^\+?[0-9 ()-]{6,20}$")


class Invalid(ValueError):
    """A field the person can fix; ``str(exc)`` is safe to show."""


@dataclass(frozen=True)
class JoinResult:
    state: str
    created: bool


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def mask_email(email: str | None) -> str | None:
    """``nithya@example.com`` -> ``n*****@example.com``. Enough for the
    holder of a link to recognise the address it is bound to, not enough
    for a forwarded link to hand the address to somebody else."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"


async def _live_invite_for(email: str) -> bool:
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(SignupInviteModel.id).where(
                    and_(
                        SignupInviteModel.email == email,
                        SignupInviteModel.revoked_at.is_(None),
                        SignupInviteModel.uses < SignupInviteModel.max_uses,
                        or_(
                            SignupInviteModel.expires_at.is_(None),
                            SignupInviteModel.expires_at > now,
                        ),
                    )
                )
            )
        ).first()
    return row is not None


async def join(
    *,
    email: str,
    language: str,
    first_task: str | None = None,
    phone: str | None = None,
    occupation: str | None = None,
    renewal: bool = False,
) -> JoinResult:
    """Put ``email`` on the list once. Raises ``Invalid`` for a field the
    person can correct; never raises for a repeat."""
    address = normalise_email(email)
    if not languages.is_supported(language):
        raise Invalid("Choose a language from the list.")
    phone = (phone or "").strip() or None
    if phone and not _PHONE.match(phone):
        raise Invalid("Enter the phone number with digits only, or leave it empty.")
    occupation = (occupation or "").strip()[:120] or None
    first_task = (first_task or "").strip()[:2000] or None

    if await db_client.get_user_by_email(address):
        return JoinResult(state=ALREADY_REGISTERED, created=False)
    if await _live_invite_for(address):
        return JoinResult(state=INVITED, created=False)

    async with db_client.async_session() as session:
        result = await session.execute(
            insert(WaitlistRequestModel)
            .values(
                email=address,
                language=language,
                first_task=first_task,
                phone=phone,
                occupation=occupation,
                source="renewal" if renewal else "waitlist",
                status=WAITLISTED,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=[func.lower(WaitlistRequestModel.email)]
            )
            .returning(WaitlistRequestModel.id)
        )
        created = result.scalar_one_or_none() is not None
        await session.commit()
    return JoinResult(state=WAITLISTED, created=created)


async def invite_status(code: str) -> dict:
    """What an invitation link says, without spending it."""
    normalised = signup_invites.normalise(code)
    if not normalised:
        return {"state": INVALID}
    async with db_client.async_session() as session:
        invite = (
            await session.execute(
                select(SignupInviteModel).where(SignupInviteModel.code == normalised)
            )
        ).scalar_one_or_none()
    if invite is None:
        return {"state": INVALID}
    expires_at = invite.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    base = {
        "email_hint": mask_email(invite.email),
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    if invite.revoked_at is not None:
        return {"state": REVOKED, **base}
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return {"state": EXPIRED, **base}
    if invite.uses >= invite.max_uses:
        return {"state": USED, **base}
    return {"state": VALID, **base, "code": signup_invites.display(normalised)}


async def count() -> int:
    async with db_client.async_session() as session:
        return int(
            (
                await session.execute(select(func.count(WaitlistRequestModel.id)))
            ).scalar_one()
        )

"""Invite-only signup (INVITE-1, KAN-273).

While ``invite_only_signup`` is on, a new account needs a code minted by
staff. Existing accounts never need one. Both doors in -- password signup
and Google -- call ``claim`` before the user row exists, so a refused code
creates nothing, and ``record_redemption`` after provisioning.

``claim`` is one conditional UPDATE, so two people racing for the last use
of a code cannot both get it.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import and_, or_, select, update

from api.db import db_client
from api.db.signup_invite_models import (
    SignupInviteModel,
    SignupInviteRedemptionModel,
)
from api.services import features

FLAG = "invite_only_signup"

#: No 0/O or 1/I, so a code read aloud or off a phone is typed right.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

MISSING = "Decibyl is invite-only for now. Enter your invite code to create an account."
UNKNOWN = "That invite code is not valid. Check it and try again."
USED = "That invite code has already been used."
EXPIRED = "That invite code has expired."
WRONG_EMAIL = "That invite code was issued for a different email address."


class InviteRefused(Exception):
    """A signup that must not go ahead; ``str(exc)`` is safe to show."""


@dataclass(frozen=True)
class Claim:
    invite_id: int
    code: str


def required() -> bool:
    return features.is_on(FLAG)


def normalise(code: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def display(code: str) -> str:
    """``ABCD2345`` -> ``ABCD-2345`` for an email or a screen."""
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code


async def claim(code: str | None, email: str) -> Claim | None:
    """Take one use of ``code`` for ``email``.

    Returns None when invites are not required (the flag is off) and no code
    was given. A code given while the flag is off is still honoured, so a
    link sent before the switch keeps working. Raises ``InviteRefused`` with
    a message for the person signing up.
    """
    normalised = normalise(code)
    if not normalised:
        if required():
            raise InviteRefused(MISSING)
        return None

    email_lower = (email or "").strip().lower()
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        result = await session.execute(
            update(SignupInviteModel)
            .where(
                and_(
                    SignupInviteModel.code == normalised,
                    SignupInviteModel.uses < SignupInviteModel.max_uses,
                    SignupInviteModel.revoked_at.is_(None),
                    or_(
                        SignupInviteModel.expires_at.is_(None),
                        SignupInviteModel.expires_at > now,
                    ),
                    or_(
                        SignupInviteModel.email.is_(None),
                        SignupInviteModel.email == email_lower,
                    ),
                )
            )
            .values(uses=SignupInviteModel.uses + 1)
            .returning(SignupInviteModel.id)
        )
        invite_id = result.scalar_one_or_none()
        if invite_id is not None:
            await session.commit()
            return Claim(invite_id=invite_id, code=normalised)

        # Say why, so the person knows whether to ask for a new code.
        invite = (
            await session.execute(
                select(SignupInviteModel).where(SignupInviteModel.code == normalised)
            )
        ).scalar_one_or_none()

    if invite is None:
        if not required():
            # Flag off and a stale or mistyped code: let them in; a code is
            # not needed. Refusing would be stricter than having no gate.
            return None
        raise InviteRefused(UNKNOWN)
    if not required():
        return None
    if invite.revoked_at is not None:
        raise InviteRefused(UNKNOWN)
    if invite.expires_at is not None and invite.expires_at <= now:
        raise InviteRefused(EXPIRED)
    if invite.email and invite.email != email_lower:
        raise InviteRefused(WRONG_EMAIL)
    raise InviteRefused(USED)


async def record_redemption(
    claim_: Claim | None,
    *,
    email: str,
    user_id: int | None,
    organization_id: int | None,
    door: str,
) -> None:
    """Best effort: the account exists either way, and the use was already
    counted by ``claim``. A failure here is logged, never raised."""
    if claim_ is None:
        return
    try:
        async with db_client.async_session() as session:
            session.add(
                SignupInviteRedemptionModel(
                    invite_id=claim_.invite_id,
                    email=(email or "").strip().lower(),
                    user_id=user_id,
                    organization_id=organization_id,
                    door=door,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "Could not record invite redemption for invite {}", claim_.invite_id
        )


async def mint(
    *,
    count: int,
    created_by_user_id: int,
    email: str | None = None,
    max_uses: int = 1,
    note: str | None = None,
    expires_at: datetime | None = None,
) -> list[SignupInviteModel]:
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")
    if max_uses < 1 or max_uses > 1000:
        raise ValueError("max_uses must be between 1 and 1000")
    if email and count != 1:
        raise ValueError("an email-pinned invite is minted one at a time")
    rows: list[SignupInviteModel] = []
    async with db_client.async_session() as session:
        existing = set(
            (await session.execute(select(SignupInviteModel.code))).scalars().all()
        )
        for _ in range(count):
            code = generate_code()
            while code in existing:
                code = generate_code()
            existing.add(code)
            row = SignupInviteModel(
                code=code,
                email=(email or "").strip().lower() or None,
                max_uses=max_uses,
                uses=0,
                note=note,
                created_by_user_id=created_by_user_id,
                expires_at=expires_at,
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return rows


async def list_invites(limit: int = 500) -> list[dict]:
    async with db_client.async_session() as session:
        invites = (
            (
                await session.execute(
                    select(SignupInviteModel)
                    .order_by(SignupInviteModel.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        ids = [i.id for i in invites]
        redemptions = (
            (
                await session.execute(
                    select(SignupInviteRedemptionModel).where(
                        SignupInviteRedemptionModel.invite_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
            if ids
            else []
        )
    by_invite: dict[int, list[dict]] = {}
    for r in redemptions:
        by_invite.setdefault(r.invite_id, []).append(
            {
                "email": r.email,
                "organization_id": r.organization_id,
                "door": r.door,
                "redeemed_at": r.redeemed_at.isoformat() if r.redeemed_at else None,
            }
        )
    return [
        {
            "id": i.id,
            "code": display(i.code),
            "email": i.email,
            "max_uses": i.max_uses,
            "uses": i.uses,
            "note": i.note,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            "revoked": i.revoked_at is not None,
            "redemptions": by_invite.get(i.id, []),
        }
        for i in invites
    ]


async def revoke(invite_id: int) -> bool:
    async with db_client.async_session() as session:
        result = await session.execute(
            update(SignupInviteModel)
            .where(
                and_(
                    SignupInviteModel.id == invite_id,
                    SignupInviteModel.revoked_at.is_(None),
                )
            )
            .values(revoked_at=datetime.now(UTC))
            .returning(SignupInviteModel.id)
        )
        done = result.scalar_one_or_none() is not None
        await session.commit()
    return done

"""Who is writing, and linking an app to a member with a one-time code."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, delete, select, update

from api.db import db_client
from api.db.channel_identity_models import ChannelIdentityModel, ChannelLinkCodeModel

from .base import CHANNELS

CODE_TTL = timedelta(minutes=10)
#: No 0/O/1/I, so a code read off a laptop and typed on a phone is right.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
#: What an owner types in the app: "LINK ABC234", "/start ABC234" or just
#: the code.
_CODE_IN_TEXT = re.compile(
    r"^\s*(?:/start|link)?\s*([A-HJ-NP-Z2-9]{6})\s*$", re.IGNORECASE
)


class LinkError(Exception):
    """A link code that cannot be used; ``str(exc)`` is safe to show."""


@dataclass(frozen=True)
class Identity:
    id: int
    organization_id: int
    user_id: int
    channel: str
    external_id: str
    display_name: str | None
    conversation_ref: dict[str, Any]


def _hash(code: str) -> str:
    return hashlib.sha256(code.upper().encode()).hexdigest()


def code_in(text: str | None) -> str | None:
    match = _CODE_IN_TEXT.match(text or "")
    return match.group(1).upper() if match else None


def _view(row: ChannelIdentityModel) -> Identity:
    return Identity(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        channel=row.channel,
        external_id=row.external_id,
        display_name=row.display_name,
        conversation_ref=dict(row.conversation_ref or {}),
    )


async def start_link(*, organization_id: int, user_id: int, channel: str) -> str:
    """A fresh code for this member and app. Earlier unused codes for the
    same member and app stop working, so only the newest one on screen does."""
    if channel not in CHANNELS:
        raise LinkError(f"Unknown app {channel!r}.")
    code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        await session.execute(
            delete(ChannelLinkCodeModel).where(
                and_(
                    ChannelLinkCodeModel.organization_id == organization_id,
                    ChannelLinkCodeModel.user_id == user_id,
                    ChannelLinkCodeModel.channel == channel,
                    ChannelLinkCodeModel.used_at.is_(None),
                )
            )
        )
        session.add(
            ChannelLinkCodeModel(
                organization_id=organization_id,
                user_id=user_id,
                channel=channel,
                code_hash=_hash(code),
                expires_at=now + CODE_TTL,
            )
        )
        await session.commit()
    return code


async def redeem(
    *,
    code: str,
    channel: str,
    external_id: str,
    display_name: str = "",
    conversation_ref: dict[str, Any] | None = None,
) -> Identity:
    """Use a code sent from an app: this app identity becomes that member.

    One conditional UPDATE takes the code, so it links once. An identity
    already linked elsewhere is moved to the new member (the phone changed
    hands, or the owner re-linked), never duplicated."""
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        taken = (
            await session.execute(
                update(ChannelLinkCodeModel)
                .where(
                    and_(
                        ChannelLinkCodeModel.code_hash == _hash(code),
                        ChannelLinkCodeModel.channel == channel,
                        ChannelLinkCodeModel.used_at.is_(None),
                        ChannelLinkCodeModel.expires_at > now,
                    )
                )
                .values(used_at=now)
                .returning(
                    ChannelLinkCodeModel.organization_id, ChannelLinkCodeModel.user_id
                )
            )
        ).first()
        if taken is None:
            raise LinkError(
                "That code is not valid any more. Make a new one in Decibyl → "
                "Settings → Decibyl in your apps."
            )
        organization_id, user_id = int(taken[0]), int(taken[1])
        row = (
            await session.execute(
                select(ChannelIdentityModel).where(
                    and_(
                        ChannelIdentityModel.channel == channel,
                        ChannelIdentityModel.external_id == external_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = ChannelIdentityModel(channel=channel, external_id=external_id)
            session.add(row)
        row.organization_id = organization_id
        row.user_id = user_id
        row.display_name = (display_name or "")[:200] or None
        row.conversation_ref = conversation_ref or {}
        row.verified_at = now
        await session.commit()
        await session.refresh(row)
        return _view(row)


async def find(channel: str, external_id: str) -> Identity | None:
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(ChannelIdentityModel).where(
                    and_(
                        ChannelIdentityModel.channel == channel,
                        ChannelIdentityModel.external_id == external_id,
                    )
                )
            )
        ).scalar_one_or_none()
        return _view(row) if row is not None else None


async def remember_ref(identity: Identity, ref: dict[str, Any]) -> None:
    """Keep the newest way back (Teams' serviceUrl can change). Best effort."""
    if not ref or ref == identity.conversation_ref:
        return
    try:
        async with db_client.async_session() as session:
            await session.execute(
                update(ChannelIdentityModel)
                .where(ChannelIdentityModel.id == identity.id)
                .values(conversation_ref=ref)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store the reply route for {}: {}", identity.id, exc)


async def for_member(*, organization_id: int, user_id: int) -> list[Identity]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(ChannelIdentityModel).where(
                    and_(
                        ChannelIdentityModel.organization_id == organization_id,
                        ChannelIdentityModel.user_id == user_id,
                    )
                )
            )
        ).scalars()
        return [_view(r) for r in rows]


async def unlink(*, organization_id: int, user_id: int, identity_id: int) -> bool:
    async with db_client.async_session() as session:
        result = await session.execute(
            delete(ChannelIdentityModel)
            .where(
                and_(
                    ChannelIdentityModel.id == identity_id,
                    ChannelIdentityModel.organization_id == organization_id,
                    ChannelIdentityModel.user_id == user_id,
                )
            )
            .returning(ChannelIdentityModel.id)
        )
        done = result.first() is not None
        await session.commit()
        return done

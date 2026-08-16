"""Referral codes, and the one moment an account is attributed to a partner.

A partner's code is the only mechanism by which a customer becomes *theirs*.
Approving an application sets a rate; without a code and a signup that carries
it, that rate has nothing to be a percentage of.

The code is deliberately boring: uppercase, unambiguous alphabet, eight
characters. It gets typed off a slide and read down a phone, so ``0``/``O`` and
``1``/``I`` are not in it — a code that is transcribed wrong does not fail
loudly, it silently attributes nothing, and the partner finds out a month later
when their statement is empty.

Attribution is **write-once, at provisioning**. See
``OrganizationModel.referred_by_organization_id`` for why that is not a
limitation to be worked around later.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OrganizationModel

#: No 0/O, no 1/I/L. Transcription errors are the failure mode, not guessing.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8

#: 31^8 is about 850 billion, so a collision needs a birthday-sized population
#: we will never have. The retry exists because "never" and "cannot" are
#: different words and the unique index is the one that has to hold.
_MINT_ATTEMPTS = 5


def normalize(code: str | None) -> str | None:
    """What a user typed, as the column stores it.

    Case and surrounding whitespace are how a code arrives from a URL bar, an
    email signature, or a form somebody pasted into. None of them are a
    different code.
    """
    if not code:
        return None
    cleaned = code.strip().upper().replace("-", "").replace(" ", "")
    return cleaned or None


def _candidate() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


async def code_is_taken(session: AsyncSession, code: str) -> bool:
    return (
        await session.scalar(
            select(OrganizationModel.id).where(OrganizationModel.referral_code == code)
        )
    ) is not None


async def ensure_code(session: AsyncSession, organization: OrganizationModel) -> str:
    """This partner's code, minting one if they have none.

    Returns the existing code unchanged when there is one. A code that changes
    is a code that stops attributing the links already out in the world, so
    there is deliberately no "regenerate".
    """
    if organization.referral_code:
        return organization.referral_code

    for _ in range(_MINT_ATTEMPTS):
        candidate = _candidate()
        if not await code_is_taken(session, candidate):
            organization.referral_code = candidate
            await session.flush()
            return candidate

    # Five collisions in a row against a space this size means the column is
    # not what we think it is. Louder than a sixth attempt.
    raise RuntimeError("Could not mint a unique referral code")


async def partner_for_code(
    session: AsyncSession, code: str | None
) -> OrganizationModel | None:
    """The partner a code belongs to, or None.

    None covers every way a code can fail — unknown, blank, mistyped — because
    the caller does the same thing in all of them: provision the account
    without attribution. A signup must never fail on a bad referral code; the
    person signing up did not choose it and cannot fix it.
    """
    normalized = normalize(code)
    if not normalized:
        return None
    return await session.scalar(
        select(OrganizationModel).where(OrganizationModel.referral_code == normalized)
    )


async def attribute(
    session: AsyncSession,
    *,
    organization: OrganizationModel,
    code: str | None,
) -> OrganizationModel | None:
    """Attribute a newly provisioned account to the partner whose code it used.

    Returns the partner, or None when there was no usable code. Refuses two
    cases outright, both silently, because neither is the new account's fault
    and neither should cost them a signup:

    * **A code that resolves to the account itself.** Only reachable by an
      existing partner signing up again through their own link, but it would
      pay somebody commission on their own spend.
    * **An account that is already attributed.** Write-once means write-once.
    """
    partner = await partner_for_code(session, code)
    if partner is None:
        return None
    if partner.id == organization.id:
        logger.warning(
            f"Ignoring self-referral: organization {organization.id} used its own code"
        )
        return None
    if organization.referred_by_organization_id is not None:
        logger.warning(
            f"Organization {organization.id} is already attributed to "
            f"{organization.referred_by_organization_id}; ignoring code"
        )
        return None

    organization.referred_by_organization_id = partner.id
    organization.referred_at = datetime.now(UTC)
    await session.flush()
    logger.info(f"Organization {organization.id} attributed to partner {partner.id}")
    return partner

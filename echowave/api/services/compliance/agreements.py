"""What a customer has accepted, and what they still owe.

A click-wrap is enforceable in India under IT Act s10A given reasonable notice,
an affirmative act, and terms accessible before acceptance — but all three are
worthless without the fourth thing, which is a record. In a dispute the question
is not whether the terms were good; it is whether this customer, on this date,
agreed to this version. Nothing could answer that before this module.

**Versions, not booleans.** "They accepted the DPA" is not a defence when the
DPA has been revised twice since. Every acceptance stores the published version
string, and bumping a version here means every customer is asked again — which
is the intended behaviour, not an inconvenience to design around.

Acceptance rows are never updated or deleted. Superseding is a new row.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AgreementAcceptanceModel


@dataclass(frozen=True)
class Agreement:
    key: str
    title: str
    #: Bump this when the published document changes in a way that alters what
    #: the customer is agreeing to. Typos do not count; obligations do.
    version: str
    url: str
    #: Whether a customer must accept it before running a campaign. The DPA is
    #: required because without it the consent obligation for the people being
    #: called has not been allocated to anyone.
    required: bool


AGREEMENTS: tuple[Agreement, ...] = (
    Agreement(
        key="dpa",
        title="Data Processing Agreement",
        version="2026-07",
        url="https://decibyl.ai/legal/dpa",
        required=True,
    ),
    Agreement(
        key="terms",
        title="Terms of Service",
        version="2026-07",
        url="https://decibyl.ai/legal/terms",
        required=True,
    ),
)

CURRENT_VERSIONS: dict[str, str] = {a.key: a.version for a in AGREEMENTS}


async def record_acceptance(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    agreement: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AgreementAcceptanceModel:
    """Write one acceptance at the currently published version.

    The version is read from ``AGREEMENTS`` rather than taken from the request:
    a client that could name its own version could claim acceptance of a
    document that was never published.
    """
    if agreement not in CURRENT_VERSIONS:
        raise ValueError(f"Unknown agreement {agreement!r}")

    row = AgreementAcceptanceModel(
        organization_id=organization_id,
        user_id=user_id,
        agreement=agreement,
        version=CURRENT_VERSIONS[agreement],
        ip_address=ip_address,
        user_agent=(user_agent or "")[:512] or None,
    )
    session.add(row)
    await session.flush()
    return row


async def outstanding_for(
    session: AsyncSession, *, organization_id: int
) -> tuple[Agreement, ...]:
    """Required agreements this account has not accepted at the current version.

    An account that accepted last year's DPA is outstanding on this year's, and
    reads exactly the same as one that never accepted at all — which is correct,
    because the obligations may have changed.
    """
    accepted = set(
        (
            await session.execute(
                select(
                    AgreementAcceptanceModel.agreement,
                    AgreementAcceptanceModel.version,
                ).where(AgreementAcceptanceModel.organization_id == organization_id)
            )
        ).all()
    )
    return tuple(
        agreement
        for agreement in AGREEMENTS
        if agreement.required and (agreement.key, agreement.version) not in accepted
    )

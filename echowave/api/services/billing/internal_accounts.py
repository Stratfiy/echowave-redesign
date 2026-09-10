"""Accounts that are ours, billed at what the providers actually charge.

Decibyl runs its own agents on its own platform -- the demos a prospect is
shown, the account an engineer tests on. Billed as a customer, those accounts
pay the platform fee and the managed markup, which does three unhelpful things
at once.

It bills us to ourselves. A morning of testing cost this deployment 135 credits
of platform fee alone, invoiced by the company to the company.

It stops the work. The balance floor is a good rule for a customer -- the last
call should be one they could afford -- and a bad one for the account used to
test whether calls work at all, which finds out it has run out of credit in the
middle of a demo.

And it poisons the numbers. Revenue and gross margin on the operator dashboard
count our own testing as sales, so the one figure that says whether the
business works is inflated by however much QA happened that week.

So an internal account pays provider cost: no platform fee, no markup, and no
floor. It may go negative, because the balance on an internal account is a
record of what the providers cost us, not a wallet.

**Not a discount.** A customer on a negotiated rate still has a rate, a floor
and a margin, and all of that keeps working through the ordinary overrides.
This is the narrower statement that an account is not a customer at all, which
is why it is one flag set by staff rather than a rate somebody could tune to
zero by accident.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OrganizationModel

#: The markup that means "charge exactly what it cost", in basis points.
COST_ONLY_MARKUP_BPS = 10_000


async def is_internal(session: AsyncSession, organization_id: int | None) -> bool:
    """Is this one of our own accounts rather than a customer's?

    ``None`` is not internal: it asks for list pricing, which is a question
    about what a customer pays and must never answer with our own cost.
    """
    if organization_id is None:
        return False
    return bool(
        await session.scalar(
            select(OrganizationModel.internal_billing).where(
                OrganizationModel.id == organization_id
            )
        )
    )

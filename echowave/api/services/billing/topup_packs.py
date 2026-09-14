"""Top-up packs: what a customer can buy outright, and what it grants.

KAN-55 introduced packs; KAN-47 (14 Sept) settled the ladder. ₹999 is the
floor for everyone: a customer on the ₹999 Everyday plan should not be
offered a pack that reads as small change, and the payment gateway's fee
share is better on ₹999 than on ₹500. Credits cost fifty paise in a pack as
in a plan, and the ratio rises with the pack: ₹4,999 buys 10,500 (5% extra)
and ₹19,999 buys 44,000 (10%). The extra is a *bonus* the ledger carries as
paise: a ₹4,999 pack credits ₹5,250 of balance against a ₹4,999 invoice, and
the difference is recorded on the payment so a reconciliation can explain it.

**The ₹500 pack is gated, not gone.** Students on the Campus plan and
accounts a staff member has marked early adopters (with an end date, so
"early" ends) still see it. Everyone else sees the ladder from ₹999. The
gate is enforced where the order is made, not only where the list is shown.

Top-up credits never expire and are spent after plan credits — see
``plans._consumed_since`` for the ordering, which is what makes a pack a pool
of its own without a second ledger.

**Dollar packs (KAN-135).** An account billed outside India — its billing
profile's country is not IN, the same test that makes its supply an export —
sees $12 → 2,000, $60 → 10,500 and $240 → 44,000 instead, and nothing else.
Six tenths of a cent a credit, a cushion over the rupee price for the card
fee and the exchange, with the same 5% and 10% rungs. The order goes to
Razorpay in cents; what lands on the balance is the pack's credits, in paise
at fifty each, because the ledger has one unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OrganizationModel
from api.services.billing.credits import PAISE_PER_CREDIT

#: The plan whose accounts may buy the gated pack without a staff flag.
STUDENT_PLAN_CODE = "campus"

INR = "INR"
USD = "USD"

#: A dollar pack prices a credit at $0.006: ten credits for six cents. Kept as
#: a ratio so the bonus arithmetic stays in integers.
USD_CENTS_PER_TEN_CREDITS = 6


@dataclass(frozen=True)
class Pack:
    code: str
    #: What the customer pays, net of tax, in minor units of ``currency``:
    #: paise for a rupee pack, cents for a dollar one. Named for paise because
    #: every rupee caller reads it that way; ``price_minor`` is the same number
    #: under the name that is true for both.
    price_paise: int
    #: What lands on the balance, in credits.
    credits: int
    #: Shown and sold only to eligible accounts (Campus, early adopters).
    restricted: bool = False
    currency: str = INR

    @property
    def price_minor(self) -> int:
        return self.price_paise

    @property
    def credit_paise(self) -> int:
        """What the pack's credits are worth on the ledger, whatever it cost."""
        return self.credits * PAISE_PER_CREDIT

    @property
    def bonus_paise(self) -> int:
        """Balance granted beyond the rupee price paid. Zero on the small packs
        and on every dollar pack, whose grant is its credits and not a sum."""
        if self.currency != INR:
            return 0
        return self.credit_paise - self.price_paise

    @property
    def bonus_credits(self) -> int:
        """Credits beyond what the price buys at the pack's own per-credit
        rate: fifty paise, or six tenths of a cent."""
        if self.currency == USD:
            return self.credits - self.price_minor * 10 // USD_CENTS_PER_TEN_CREDITS
        return self.bonus_paise // PAISE_PER_CREDIT

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "currency": self.currency,
            "price_minor": self.price_minor,
            "price_paise": self.price_paise,
            "credits": self.credits,
            "bonus_credits": self.bonus_credits,
            "restricted": self.restricted,
        }


PACKS: tuple[Pack, ...] = (
    Pack("p500", 50_000, 1_000, restricted=True),
    Pack("p999", 99_900, 2_000),
    Pack("p4999", 499_900, 10_500),
    Pack("p19999", 1_999_900, 44_000),
)

USD_PACKS: tuple[Pack, ...] = (
    Pack("u12", 1_200, 2_000, currency=USD),
    Pack("u60", 6_000, 10_500, currency=USD),
    Pack("u240", 24_000, 44_000, currency=USD),
)

PACKS_BY_CODE: dict[str, Pack] = {pack.code: pack for pack in PACKS + USD_PACKS}


def pack_for(code: str) -> Pack | None:
    return PACKS_BY_CODE.get((code or "").strip().lower())


def packs_as_dicts(
    *, include_restricted: bool = False, currency: str = INR
) -> list[dict]:
    ladder = USD_PACKS if currency == USD else PACKS
    return [
        pack.as_dict() for pack in ladder if include_restricted or not pack.restricted
    ]


async def billing_currency(session: AsyncSession, *, organization_id: int) -> str:
    """The currency this account buys credit in.

    Dollars for an account whose billing profile puts it outside India — the
    same fact that zero-rates its supply as an export — and rupees for
    everyone else, including an account that has not filled its profile in.
    """
    from api.services.billing.billing_profile import get_profile

    profile = await get_profile(session, organization_id=organization_id)
    return USD if profile.is_export else INR


async def may_buy_restricted(session: AsyncSession, *, organization_id: int) -> bool:
    """Whether this account sees the gated ₹500 pack: on the Campus plan, or
    marked an early adopter by staff with the date still ahead."""
    from api.services.billing import subscription_plans

    plan = await subscription_plans.plan_for_organization(
        session, organization_id=organization_id
    )
    if plan.code == STUDENT_PLAN_CODE:
        return True
    until = await session.scalar(
        select(OrganizationModel.early_adopter_until).where(
            OrganizationModel.id == organization_id
        )
    )
    if not isinstance(until, datetime):
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > datetime.now(UTC)


async def packs_for(session: AsyncSession, *, organization_id: int) -> list[dict]:
    """The packs this account may buy, as the screen shows them.

    The gate is a convenience on the list; the order enforces it. So a
    problem deciding eligibility falls back to the public ladder rather than
    failing the balance screen, and says so in the log.
    """
    try:
        currency = await billing_currency(session, organization_id=organization_id)
        eligible = currency == INR and await may_buy_restricted(
            session, organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001 - the list must never fail the screen
        logger.warning(
            "Could not decide pack eligibility for org {}: {}", organization_id, exc
        )
        currency, eligible = INR, False
    return packs_as_dicts(include_restricted=eligible, currency=currency)

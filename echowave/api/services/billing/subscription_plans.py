"""The catalogue of plans, and the arithmetic that stops one being sold at a loss.

A plan sells two things under one monthly instruction: a call balance and an
entitlement to some phone numbers. Both halves are settled by machinery that
already existed — the balance is a credit ledger grant, the numbers are
recurring charges — so this module is a catalogue and a set of rules, not a
third billing path.

Three rules, each because breaking it costs money quietly.

**A plan may never grant more balance than it charges.** Balance is spendable at
our cost the moment it lands, so ₹2,500 of it sold for ₹2,000 is a ₹500 loss
per cycle, per account, for as long as nobody looks. :func:`save` refuses.

**The entitlement is a count, and it is the reason numbers stop being free.**
An account on a plan has one authorised mandate, and a rental charge attached to
it is skipped by the monthly job because the bank is collecting for it instead.
Attach *every* number to that mandate and every number is skipped — while the
collection settles only one of them. Numbers beyond the entitlement must carry
no mandate, so the balance pays for them like any other rental.

**The seeded starter plan is the one that was already being sold.** Its figures
come from the environment, not from literals here, so a deployment that has
moved the rental price does not silently start selling a different plan than the
one its constants describe.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import SubscriptionPlanModel

#: The plan every deployment starts with, and the one a mandate written before
#: the plans table existed belongs to.
STARTER = "starter"


class PlanError(ValueError):
    """A plan could not be saved, or is not one that can be sold."""


@dataclass(frozen=True)
class Plan:
    """A plan as the rest of the system reads it.

    A frozen view rather than the ORM row, so nothing outside this module can
    edit a price by assigning to an attribute and committing a session it did
    not open.
    """

    code: str
    label: str
    blurb: str
    price_paise: int
    balance_paise: int
    included_numbers: int
    extra_number_price_paise: int
    razorpay_plan_id: str | None
    enabled: bool
    sort_order: int

    @property
    def numbers_value_paise(self) -> int:
        """What the included numbers would cost bought separately."""
        return self.included_numbers * self.extra_number_price_paise

    @property
    def parts_paise(self) -> int:
        """The plan's contents at list price, before whatever discount it is."""
        return self.balance_paise + self.numbers_value_paise

    @property
    def discount_paise(self) -> int:
        """What the customer saves against buying the parts. Negative is a premium."""
        return self.parts_paise - self.price_paise


def _view(row: SubscriptionPlanModel) -> Plan:
    return Plan(
        code=row.code,
        label=row.label,
        blurb=row.blurb or "",
        price_paise=int(row.price_paise),
        balance_paise=int(row.balance_paise or 0),
        included_numbers=int(row.included_numbers or 0),
        # A plan with no figure of its own follows the platform rental price
        # rather than pinning a copy that goes stale the day it moves.
        extra_number_price_paise=int(
            row.extra_number_price_paise
            if row.extra_number_price_paise is not None
            else constants.NUMBER_RENTAL_PRICE_PAISE
        ),
        razorpay_plan_id=row.razorpay_plan_id,
        enabled=bool(row.enabled),
        sort_order=int(row.sort_order or 0),
    )


async def list_plans(session: AsyncSession, *, enabled_only: bool = True) -> list[Plan]:
    """Every plan, cheapest-intent first.

    Ordered by ``sort_order`` then price so the screen's order is an operator's
    decision, with a stable tiebreak rather than insertion order.
    """
    query = select(SubscriptionPlanModel)
    if enabled_only:
        query = query.where(SubscriptionPlanModel.enabled.is_(True))
    rows = (
        await session.execute(
            query.order_by(
                SubscriptionPlanModel.sort_order, SubscriptionPlanModel.price_paise
            )
        )
    ).scalars()
    return [_view(row) for row in rows]


async def get_plan(session: AsyncSession, *, code: str) -> Plan | None:
    row = await session.scalar(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == code)
    )
    return _view(row) if row is not None else None


async def resolve(session: AsyncSession, *, code: str | None) -> Plan | None:
    """The plan named by ``code``, or the starter plan when nothing is named.

    ``None`` is what a mandate written before the plans table carries, and it
    means the one plan that existed then. Falling back rather than failing keeps
    every such account collecting and granting exactly what it did before.
    """
    return await get_plan(session, code=code or STARTER)


async def save(
    session: AsyncSession,
    *,
    code: str,
    label: str,
    price_paise: int,
    balance_paise: int,
    included_numbers: int,
    blurb: str = "",
    extra_number_price_paise: int | None = None,
    razorpay_plan_id: str | None = None,
    enabled: bool = True,
    sort_order: int = 0,
) -> Plan:
    """Create or update a plan, refusing one that cannot be sold.

    The checks are the whole point of routing this through a function rather
    than letting an admin screen write the row. Each rejects a value that would
    otherwise look plausible on a form and lose money every month.
    """
    code = (code or "").strip().lower()
    if not code:
        raise PlanError("A plan needs a code.")
    if not (label or "").strip():
        raise PlanError("A plan needs a name customers will read.")
    if price_paise <= 0:
        raise PlanError("A plan's price must be more than nothing.")
    if balance_paise < 0 or included_numbers < 0:
        raise PlanError("A plan cannot include a negative amount of anything.")
    if balance_paise > price_paise:
        # The one that is a loss on contact rather than a thin margin: granted
        # balance is spendable immediately and at our cost.
        raise PlanError(
            f"This plan grants ₹{balance_paise / 100:,.0f} of balance for "
            f"₹{price_paise / 100:,.0f}. Balance is spent at our cost, so a "
            "plan that grants more than it collects loses money on every "
            "cycle. Raise the price or lower the balance."
        )
    if included_numbers and not (
        extra_number_price_paise
        if extra_number_price_paise is not None
        else constants.NUMBER_RENTAL_PRICE_PAISE
    ):
        raise PlanError("A plan including numbers needs a price for extra ones.")

    row = await session.scalar(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == code)
    )
    if row is None:
        row = SubscriptionPlanModel(code=code)
        session.add(row)

    row.label = label.strip()
    row.blurb = (blurb or "").strip()
    row.price_paise = int(price_paise)
    row.balance_paise = int(balance_paise)
    row.included_numbers = int(included_numbers)
    row.extra_number_price_paise = (
        int(extra_number_price_paise) if extra_number_price_paise is not None else None
    )
    row.razorpay_plan_id = (razorpay_plan_id or "").strip() or None
    row.enabled = bool(enabled)
    row.sort_order = int(sort_order)
    await session.flush()

    plan = _view(row)
    if plan.discount_paise < 0:
        # Not refused — a plan may legitimately cost more than its parts if it
        # carries something the parts do not. Said out loud because the usual
        # cause is a typo.
        logger.warning(
            "Plan {} is priced ₹{:.2f} above its contents at list price",
            code,
            -plan.discount_paise / 100,
        )
    return plan


async def ensure_seeded(session: AsyncSession) -> Plan:
    """Make sure the starter plan exists, without overwriting an edited one.

    Reproduces exactly what the constants sold before this table: ₹2,500 of
    balance and one number, priced at the sum. An operator who has since
    changed any of it keeps their version — a seeder that reset a price on
    every deploy would be a price change nobody approved.
    """
    existing = await get_plan(session, code=STARTER)
    if existing is not None:
        return existing
    return await save(
        session,
        code=STARTER,
        label="Starter",
        blurb="A phone number and a month of calling, on one monthly payment.",
        price_paise=constants.STARTER_PLAN_PRICE_PAISE,
        balance_paise=constants.STARTER_PLAN_BALANCE_PAISE,
        included_numbers=1,
        extra_number_price_paise=constants.NUMBER_RENTAL_PRICE_PAISE,
        razorpay_plan_id=constants.RAZORPAY_STARTER_PLAN_ID,
        sort_order=0,
    )

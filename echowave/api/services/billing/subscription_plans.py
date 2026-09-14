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

#: The plan an account with no authorised mandate is on. Granted, never
#: bought: it exists as a row so its caps are rows like every other plan's.
FREE = "free"

#: What a rupee of granted balance costs us, in basis points: the provider
#: share of a composed rupee at the rate book's list prices (pricing study
#: section 11: Rs 2.36 of cost inside a Rs 3.81 composed minute, 62%). A
#: credit is fifty paise of *marked-up* cost (KAN-52), so the loss guard on a
#: plan compares the price against the balance at cost, not at face value.
#: The ladder deliberately sells more credits per rupee as the plan grows
#: (1:2 at Everyday and Business, 1:2.5 at Growth, 1:3 at Scale), which only
#: works because plan credits expire at cycle end; this figure is the check
#: that a plan spent in full still does not lose money.
BALANCE_COST_BPS = 6_200

#: ``billing_period`` values a mandate can carry.
MONTHLY = "monthly"
ANNUAL = "annual"


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
    #: Bytes of knowledge base the plan buys. Zero means the plan does not
    #: include one at all, which is what an account holding no plan resolves to
    #: — see :func:`knowledge_base_allowance_for`.
    knowledge_base_bytes: int
    #: The largest single document. Zero follows the deployment ceiling.
    knowledge_base_max_file_bytes: int
    #: Per-minute platform fee in millipaise, applied when the mandate is
    #: authorised. ``None`` means the plan says nothing and the account keeps
    #: the rate it has — not the same as zero, which would give the fee away.
    platform_rate_mpaise: int | None
    razorpay_plan_id: str | None
    #: The provider plan for a zero-rated account — outside India with an LUT
    #: on file. Created at the **net** price rather than the gross, because
    #: that is the whole of what such an account owes. Null until an operator
    #: creates it, and until then an export account cannot subscribe: a
    #: visible refusal beats an invisible 18% overcharge every month.
    razorpay_plan_id_export: str | None
    enabled: bool
    sort_order: int
    #: May deployed bots use the phone? False on Free and Everyday.
    voice_allowed: bool = True
    #: Bought, or granted? Free and Campus Builder are granted.
    purchasable: bool = True
    #: Dollars for a foreign, text-only account. None means India-only.
    price_usd_cents: int | None = None
    #: A year, net. None means no annual option.
    annual_price_paise: int | None = None
    razorpay_plan_id_annual: str | None = None
    razorpay_plan_id_annual_export: str | None = None

    @property
    def credits(self) -> int:
        """The monthly grant in the unit the customer sees."""
        from api.services.billing.credits import credits_of_balance

        return credits_of_balance(self.balance_paise)

    @property
    def annual_credits(self) -> int:
        return self.credits * 12

    @property
    def cost_of_balance_paise(self) -> int:
        """What the granted balance costs us if it is all spent: the
        marked-up rupee at the rate book's thinnest markup."""
        return self.balance_paise * BALANCE_COST_BPS // 10_000

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
        knowledge_base_bytes=int(row.knowledge_base_bytes or 0),
        # A plan with no figure of its own follows the deployment ceiling,
        # exactly as extra_number_price_paise follows the rental price.
        knowledge_base_max_file_bytes=int(
            row.knowledge_base_max_file_bytes
            or constants.KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES
        ),
        platform_rate_mpaise=(
            int(row.platform_rate_mpaise)
            if row.platform_rate_mpaise is not None
            else None
        ),
        razorpay_plan_id=row.razorpay_plan_id,
        razorpay_plan_id_export=row.razorpay_plan_id_export,
        enabled=bool(row.enabled),
        sort_order=int(row.sort_order or 0),
        voice_allowed=bool(
            row.voice_allowed if row.voice_allowed is not None else True
        ),
        purchasable=bool(row.purchasable if row.purchasable is not None else True),
        price_usd_cents=(
            int(row.price_usd_cents) if row.price_usd_cents is not None else None
        ),
        annual_price_paise=(
            int(row.annual_price_paise) if row.annual_price_paise is not None else None
        ),
        razorpay_plan_id_annual=row.razorpay_plan_id_annual,
        razorpay_plan_id_annual_export=row.razorpay_plan_id_annual_export,
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


async def next_voice_plan_code(session: AsyncSession, *, code: str) -> str | None:
    """The rung above ``code`` that puts bots on the phone, or None on the last.

    Voice overage is charged at the next tier's per-minute rate (KAN-55):
    Business's overage minute costs what Growth's plan minute costs, Growth's
    what Scale's does, and Scale, being the last rung, pays its own. Starter
    is Business's predecessor and climbs from there.
    """
    current = "business" if code == STARTER else code
    plans = await list_plans(session, enabled_only=True)
    here = next((p for p in plans if p.code == current), None)
    if here is None:
        return None
    for plan in plans:
        if (
            plan.sort_order > here.sort_order
            and plan.voice_allowed
            and plan.purchasable
            and plan.code != current
        ):
            return plan.code
    return None


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


@dataclass(frozen=True)
class KnowledgeBaseAllowance:
    """What an account may keep in its knowledge base, and in one file.

    Both numbers together because all three gates need both — the presigned URL
    is minted against the per-file limit and refused against the total, the
    process-document call checks the same pair, and the worker enforces the
    per-file limit again on an object the store never checked. Returning them
    separately is how two of the three end up reading one and assuming the
    other.
    """

    total_bytes: int
    max_file_bytes: int

    @property
    def includes_a_knowledge_base(self) -> bool:
        return self.total_bytes > 0


#: No knowledge base at all. Named rather than written as a bare pair of
#: zeros, because it is a decision this module makes and not an absence of
#: one: it is what the free tier collapses to when an operator sets it to zero.
NO_KNOWLEDGE_BASE = KnowledgeBaseAllowance(total_bytes=0, max_file_bytes=0)

#: What an account with no plan gets: room for one small document, so a free
#: signup can see the feature work before paying for it. A ceiling rather
#: than a refusal, because a wall at the first upload is where a clinic or a
#: gym that would have paid ₹3,000 a month stops evaluating.
FREE_KNOWLEDGE_BASE = KnowledgeBaseAllowance(
    total_bytes=constants.FREE_KNOWLEDGE_BASE_BYTES,
    max_file_bytes=min(
        constants.FREE_KNOWLEDGE_BASE_FILE_BYTES, constants.FREE_KNOWLEDGE_BASE_BYTES
    ),
)

#: What an account run by Decibyl staff gets, whatever it has paid. The
#: per-file figure is the deployment ceiling: it is what the worker can
#: actually hold in memory, and staff are not exempt from physics.
STAFF_KNOWLEDGE_BASE = KnowledgeBaseAllowance(
    total_bytes=constants.STAFF_KNOWLEDGE_BASE_BYTES,
    max_file_bytes=constants.KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES,
)


async def knowledge_base_allowance_for(
    session: AsyncSession, *, organization_id: int
) -> KnowledgeBaseAllowance:
    """How much knowledge base this account's plan buys it.

    Everything for an account run by staff; the small free allowance for an
    account with no authorised plan. Ingestion embeds every document on our
    own model key, and embeddings have no rate anywhere — so an unsubscribed
    account uploading a corpus spends our money and bills nothing. The free
    tier is sized for one document, which is what makes the knowledge base a
    thing a subscription buys while still letting a signup see it work.

    A mandate that exists but is not yet authorised resolves to the free tier
    too. The account has started subscribing and not finished; entitling it
    on the strength of an instruction the bank has not confirmed would hand
    out the plan to anyone who begins checkout and abandons it.

    Imported lazily because ``mandates`` imports this module for
    :func:`resolve`, and the cycle is only benign at call time.
    """
    from api.services.billing.mandates import (
        PURPOSE_STARTER_PLAN,
        get_mandate,
        is_authorised,
    )
    from api.services.billing.staff_accounts import is_staff_account

    if await is_staff_account(session, organization_id=organization_id):
        return STAFF_KNOWLEDGE_BASE
    mandate = await get_mandate(
        session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
    )
    if not is_authorised(mandate):
        return FREE_KNOWLEDGE_BASE
    plan = await resolve(session, code=mandate.plan_code)
    if plan is None or plan.knowledge_base_bytes <= 0:
        return FREE_KNOWLEDGE_BASE
    return KnowledgeBaseAllowance(
        total_bytes=plan.knowledge_base_bytes,
        max_file_bytes=plan.knowledge_base_max_file_bytes,
    )


async def set_provider_plan_ids(
    session: AsyncSession,
    *,
    code: str,
    razorpay_plan_id: str | None = None,
    razorpay_plan_id_export: str | None = None,
) -> Plan:
    """Pin a plan's provider ids without touching what it costs or contains.

    Separate from :func:`save` because the two answer different questions.
    ``save`` rewrites the whole row, so using it to add a missing id would also
    reset a price an operator had edited -- silently, and to whatever the caller
    happened to be holding. Pinning is not a re-pricing.

    ``None`` leaves an id alone rather than clearing it, so a caller that knows
    only the domestic id cannot blank the export one by omission.

    The same-id check from ``save`` applies here too, and for the same reason: a
    pinned plan holds one fixed amount at the provider, so one id cannot collect
    both the gross and the net.
    """
    row = await session.scalar(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == code)
    )
    if row is None:
        raise PlanError(f"No plan with code {code!r}.")

    if razorpay_plan_id is not None:
        row.razorpay_plan_id = razorpay_plan_id.strip() or None
    if razorpay_plan_id_export is not None:
        row.razorpay_plan_id_export = razorpay_plan_id_export.strip() or None

    if row.razorpay_plan_id_export and row.razorpay_plan_id_export == (
        row.razorpay_plan_id
    ):
        raise PlanError(
            "The export plan must be a separate Razorpay plan, created at the "
            "net price. The same plan cannot collect two different amounts."
        )

    await session.flush()
    return _view(row)


async def set_plan_platform_rate(
    session: AsyncSession, *, code: str, platform_rate_mpaise: int | None
) -> Plan:
    """Set a plan's per-minute fee without touching anything else about it.

    The narrow sibling of :func:`set_provider_plan_ids`, and separate from
    :func:`save` for the same reason: ``save`` rewrites the whole row, so using
    it to price a tier would also reset a balance or an entitlement somebody had
    edited.

    ``None`` clears the fee, returning accounts that later authorise on this
    plan to whatever rate they already hold. That is a real thing to want — it
    is how a tier stops overriding the list price — so it is expressible rather
    than being conflated with "leave it alone".
    """
    row = await session.scalar(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == code)
    )
    if row is None:
        raise PlanError(f"No plan with code {code!r}.")
    if platform_rate_mpaise is not None and platform_rate_mpaise < 0:
        raise PlanError("A plan cannot charge a negative platform fee.")

    row.platform_rate_mpaise = (
        int(platform_rate_mpaise) if platform_rate_mpaise is not None else None
    )
    await session.flush()
    return _view(row)


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
    knowledge_base_bytes: int = 0,
    knowledge_base_max_file_bytes: int = 0,
    platform_rate_mpaise: int | None = None,
    razorpay_plan_id: str | None = None,
    razorpay_plan_id_export: str | None = None,
    enabled: bool = True,
    sort_order: int = 0,
    voice_allowed: bool = True,
    purchasable: bool = True,
    price_usd_cents: int | None = None,
    annual_price_paise: int | None = None,
    razorpay_plan_id_annual: str | None = None,
    razorpay_plan_id_annual_export: str | None = None,
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
    if purchasable and price_paise <= 0:
        raise PlanError(
            "A plan on sale must cost more than nothing. A granted plan (Free, "
            "Campus Builder) may cost nothing if it is marked not purchasable."
        )
    if price_paise < 0:
        raise PlanError("A plan cannot have a negative price.")
    if price_usd_cents is not None and price_usd_cents < 0:
        raise PlanError("A dollar price cannot be negative.")
    if annual_price_paise is not None:
        if annual_price_paise <= 0:
            raise PlanError("An annual price must be more than nothing, or left empty.")
        if annual_price_paise > price_paise * 12:
            raise PlanError(
                "A year costs more than twelve months would. Annual is a "
                "discount (ten months for twelve), not a premium."
            )
    if (
        balance_paise < 0
        or included_numbers < 0
        or knowledge_base_bytes < 0
        or knowledge_base_max_file_bytes < 0
    ):
        raise PlanError("A plan cannot include a negative amount of anything.")
    if platform_rate_mpaise is not None and platform_rate_mpaise < 0:
        raise PlanError("A plan cannot charge a negative platform fee.")
    if 0 < knowledge_base_bytes < knowledge_base_max_file_bytes:
        # A per-file limit above the total is a limit that can never be
        # reached: the upload is refused by the total first, and the number on
        # the screen is one no document could ever hit.
        raise PlanError(
            "This plan accepts a single file larger than its whole knowledge "
            "base. Raise the total, or lower the per-file limit."
        )
    # The loss guard. Granted balance is spendable the moment it lands, but
    # it is spent at the *charged* rate -- a credit is fifty paise of
    # marked-up cost (KAN-52) -- so what a plan can lose is the balance at
    # cost, ``BALANCE_COST_BPS`` of its face value, plus the carrier's rent for
    # every included number, which is owed whether the customer calls or not.
    # Face value would refuse Scale (40,000 credits for Rs 19,999) for a loss
    # it does not make; ignoring the rent would pass a Rs 2,000 plan granting
    # Rs 1,990 and one number, which loses the rent every cycle. A granted
    # plan (not purchasable) is exempt: it is a decision to give, not a sale.
    balance_cost = balance_paise * BALANCE_COST_BPS // 10_000
    carrier_cost = included_numbers * constants.NUMBER_RENTAL_COST_PAISE
    if purchasable and balance_cost + carrier_cost > price_paise:
        shortfall = balance_cost + carrier_cost - price_paise
        numbers = (
            f" and ₹{carrier_cost / 100:,.2f} renting {included_numbers} "
            f"number{'s' if included_numbers != 1 else ''} from the carrier"
            if carrier_cost
            else ""
        )
        raise PlanError(
            f"This plan collects ₹{price_paise / 100:,.2f} net and can spend "
            f"₹{balance_cost / 100:,.2f} of it at cost on the "
            f"₹{balance_paise / 100:,.0f} of balance it grants{numbers} — "
            f"₹{shortfall / 100:,.2f} more than it takes, so it loses money "
            "every cycle, on every account. Lower the balance, raise the price, "
            "or include fewer numbers."
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
    row.knowledge_base_bytes = int(knowledge_base_bytes)
    row.knowledge_base_max_file_bytes = int(knowledge_base_max_file_bytes)
    row.platform_rate_mpaise = (
        int(platform_rate_mpaise) if platform_rate_mpaise is not None else None
    )
    row.razorpay_plan_id = (razorpay_plan_id or "").strip() or None
    row.razorpay_plan_id_export = (razorpay_plan_id_export or "").strip() or None
    if (
        row.razorpay_plan_id_export
        and row.razorpay_plan_id_export == row.razorpay_plan_id
    ):
        # One id cannot be pinned at two amounts. Pointing both at the same
        # provider plan does not make an export account pay the net — it makes
        # the guard pass for one of them and refuse the other, at random
        # depending on which account subscribes.
        raise PlanError(
            "The export plan must be a separate Razorpay plan, created at the "
            "net price. The same plan cannot collect two different amounts."
        )
    row.enabled = bool(enabled)
    row.sort_order = int(sort_order)
    row.voice_allowed = bool(voice_allowed)
    row.purchasable = bool(purchasable)
    row.price_usd_cents = int(price_usd_cents) if price_usd_cents is not None else None
    row.annual_price_paise = (
        int(annual_price_paise) if annual_price_paise is not None else None
    )
    row.razorpay_plan_id_annual = (razorpay_plan_id_annual or "").strip() or None
    row.razorpay_plan_id_annual_export = (
        razorpay_plan_id_annual_export or ""
    ).strip() or None
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


MB = 1024 * 1024


def _credits(n: int) -> int:
    from api.services.billing.credits import paise_for_credits

    return paise_for_credits(n)


#: The ladder decided 14 Sept 2026 (KAN-47, seeded by KAN-53). Prices are
#: net of GST; a credit is fifty paise; the credits-per-rupee ratio rises with
#: the plan (2.0, 2.0, 2.5, 3.0) and plan credits expire at cycle end.
#: Knowledge is capped in *pages* in
#: ``plan_limits``; the byte figures here are the storage ceilings behind
#: those pages and follow the single-upload row of the caps table. Changing a
#: published price or grant needs a note in the pricing spec and a comment
#: on KAN-47 first.
LADDER_SEED: tuple[dict, ...] = (
    dict(
        code=FREE,
        label="Free",
        sort_order=0,
        purchasable=False,
        blurb="Try one bot on web chat, WhatsApp or email. 1,000 credits to start.",
        price_paise=0,
        balance_paise=0,
        included_numbers=0,
        voice_allowed=False,
        platform_rate_mpaise=0,
        knowledge_base_bytes=constants.FREE_KNOWLEDGE_BASE_BYTES,
        knowledge_base_max_file_bytes=constants.FREE_KNOWLEDGE_BASE_FILE_BYTES,
    ),
    dict(
        code="everyday",
        label="Everyday",
        sort_order=10,
        blurb="WhatsApp, email, web chat, knowledge and routines. No phone line.",
        price_paise=99_900,
        price_usd_cents=1_000,
        annual_price_paise=999_000,
        balance_paise=_credits(2_000),
        included_numbers=0,
        voice_allowed=False,
        platform_rate_mpaise=0,
        knowledge_base_bytes=50 * MB,
        knowledge_base_max_file_bytes=25 * MB,
    ),
    dict(
        code="business",
        label="Business",
        sort_order=20,
        blurb="The first voice plan: a phone number and 6,000 credits a month.",
        price_paise=299_900,
        annual_price_paise=2_999_000,
        balance_paise=_credits(6_000),
        included_numbers=1,
        voice_allowed=True,
        platform_rate_mpaise=0,
        knowledge_base_bytes=200 * MB,
        knowledge_base_max_file_bytes=100 * MB,
    ),
    dict(
        code="growth",
        label="Growth",
        sort_order=30,
        blurb="Campaigns. Two numbers and 25,000 credits a month.",
        price_paise=999_900,
        annual_price_paise=9_999_000,
        balance_paise=_credits(25_000),
        included_numbers=2,
        voice_allowed=True,
        platform_rate_mpaise=0,
        knowledge_base_bytes=1024 * MB,
        knowledge_base_max_file_bytes=250 * MB,
    ),
    dict(
        code="scale",
        label="Scale",
        sort_order=40,
        blurb="Multi-location and agencies. Four numbers and 60,000 credits a month.",
        price_paise=1_999_900,
        annual_price_paise=19_999_000,
        balance_paise=_credits(60_000),
        included_numbers=4,
        voice_allowed=True,
        platform_rate_mpaise=0,
        knowledge_base_bytes=5 * 1024 * MB,
        knowledge_base_max_file_bytes=1024 * MB,
    ),
    # Campus Builder (KAN-69): Business features and 300 voice minutes a month
    # for a college email, granted rather than sold. Seeded off sale until the
    # eligibility check ships; the figures are the decided ones.
    dict(
        code="campus",
        label="Campus Builder",
        sort_order=50,
        purchasable=False,
        enabled=False,
        blurb="For students: Business features and 300 voice minutes a month.",
        price_paise=0,
        balance_paise=_credits(3_600),
        included_numbers=0,
        voice_allowed=True,
        platform_rate_mpaise=0,
        knowledge_base_bytes=200 * MB,
        knowledge_base_max_file_bytes=100 * MB,
    ),
)


async def ensure_seeded(session: AsyncSession) -> Plan:
    """Make sure every plan exists, without overwriting an edited one.

    The starter plan is reproduced exactly as the constants sold it before
    this table: ₹2,500 of balance and one number, priced at the sum. The
    ladder (``LADDER_SEED``) lands beside it, and each plan's caps land in
    ``plan_limits``. An operator who has since changed any of it keeps their
    version — a seeder that reset a price on every deploy would be a price
    change nobody approved.

    The first time the ladder lands, Starter is withdrawn from sale: Business
    is its successor at the same price and grant. Accounts on Starter keep
    collecting and granting what they bought; it is hidden from the picker,
    not deleted. Returns the starter plan, which is what every caller before
    the ladder expected back.
    """
    from api.services.billing import plan_limits

    starter = await get_plan(session, code=STARTER)
    if starter is None:
        starter = await save(
            session,
            code=STARTER,
            label="Starter",
            blurb="A phone number and a month of calling, on one monthly payment.",
            price_paise=constants.STARTER_PLAN_PRICE_PAISE,
            balance_paise=constants.STARTER_PLAN_BALANCE_PAISE,
            included_numbers=1,
            extra_number_price_paise=constants.NUMBER_RENTAL_PRICE_PAISE,
            knowledge_base_bytes=constants.STARTER_PLAN_KNOWLEDGE_BASE_BYTES,
            knowledge_base_max_file_bytes=constants.STARTER_PLAN_KNOWLEDGE_BASE_FILE_BYTES,
            razorpay_plan_id=constants.RAZORPAY_STARTER_PLAN_ID,
            sort_order=0,
            # Withdrawn from sale below the moment the ladder exists; created
            # on sale only for a deployment that never seeds the ladder.
            enabled=True,
        )

    ladder_landed = False
    for seed in LADDER_SEED:
        if await get_plan(session, code=seed["code"]) is None:
            await save(session, **seed)
            ladder_landed = True

    if ladder_landed and starter.enabled:
        row = await session.scalar(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == STARTER)
        )
        if row is not None:
            row.enabled = False
            await session.flush()
            starter = _view(row)
            logger.info("Starter withdrawn from sale; Business is its successor")

    await plan_limits.ensure_seeded(session)
    return starter


#: What a deployment that has not seeded resolves a plan-less account to, so
#: no caller has to special-case an empty table.
_FREE_FALLBACK = Plan(
    code=FREE,
    label="Free",
    blurb="",
    price_paise=0,
    balance_paise=0,
    included_numbers=0,
    extra_number_price_paise=constants.NUMBER_RENTAL_PRICE_PAISE,
    knowledge_base_bytes=constants.FREE_KNOWLEDGE_BASE_BYTES,
    knowledge_base_max_file_bytes=constants.FREE_KNOWLEDGE_BASE_FILE_BYTES,
    platform_rate_mpaise=None,
    razorpay_plan_id=None,
    razorpay_plan_id_export=None,
    enabled=True,
    sort_order=0,
    voice_allowed=False,
    purchasable=False,
)


async def plan_for_organization(session: AsyncSession, *, organization_id: int) -> Plan:
    """The plan an account is on: its authorised plan mandate's, else Free.

    A mandate that exists but is not yet authorised is Free too: the account
    has started subscribing and not finished, and entitling it on an
    instruction the bank has not confirmed would hand out the plan to anyone
    who begins checkout and abandons it.
    """
    from api.services.billing.mandates import (
        PURPOSE_STARTER_PLAN,
        get_mandate,
        is_authorised,
    )

    mandate = await get_mandate(
        session, organization_id=organization_id, purpose=PURPOSE_STARTER_PLAN
    )
    if is_authorised(mandate):
        plan = await resolve(session, code=mandate.plan_code)
        if plan is not None:
            return plan
    return await get_plan(session, code=FREE) or _FREE_FALLBACK


class VoiceNotIncluded(PlanError):
    """The account's plan does not put bots on the phone."""

    def __init__(self, *, plan: Plan, upgrade_to: str | None):
        self.plan_code = plan.code
        self.upgrade_to = upgrade_to
        rung = (upgrade_to or "business").capitalize()
        super().__init__(
            f"The {plan.label} plan is text only: bots on it answer WhatsApp, "
            f"email and web chat, not the phone. Move to {rung} to attach a "
            "number or run a campaign."
        )


async def assert_voice_allowed(session: AsyncSession, *, organization_id: int) -> Plan:
    """Refuse to put an account's bots on the phone unless its plan allows it.

    Checked where a bot meets a line: buying or attaching a number, and
    starting a campaign. Not checked in the builder: hearing a bot in the
    app is how a customer decides to pay for the phone.

    Two accounts pass without a voice plan. Staff, because nothing on the
    company's own account is for sale. And an account renting a number on
    its own rental mandate, the shape that predates the ladder: it is paying
    for a line, and the ladder must not take it away.
    """
    from api.services.billing.mandates import (
        PURPOSE_NUMBER_RENTAL,
        get_mandate,
        is_authorised,
    )
    from api.services.billing.staff_accounts import is_staff_account

    plan = await plan_for_organization(session, organization_id=organization_id)
    if plan.voice_allowed:
        return plan
    if await is_staff_account(session, organization_id=organization_id):
        return plan
    rental = await get_mandate(
        session, organization_id=organization_id, purpose=PURPOSE_NUMBER_RENTAL
    )
    if is_authorised(rental):
        return plan
    upgrade_to = None
    for candidate in await list_plans(session):
        if candidate.voice_allowed and candidate.purchasable:
            upgrade_to = candidate.code
            break
    raise VoiceNotIncluded(plan=plan, upgrade_to=upgrade_to)


def voice_not_included_detail(exc: VoiceNotIncluded) -> dict:
    """The 403 body a screen can act on: the message, and where to go."""
    return {
        "error": "voice_not_included",
        "message": str(exc),
        "plan_code": exc.plan_code,
        "upgrade_to": exc.upgrade_to,
        "raise_path": f"upgrade:{exc.upgrade_to}" if exc.upgrade_to else "support",
    }

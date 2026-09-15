"""Promo codes: discounts and bonus credits a customer types at checkout (KAN-134).

Staff create a code in superadmin; a customer types it on the top-up screen
or when starting a plan. Three kinds:

* ``percent`` -- a share off the price. Reduces what the gateway charges and
  prints on the receipt voucher as a discount line; GST is on the discounted
  amount.
* ``amount`` -- a fixed sum off, in one currency. Same treatment.
* ``bonus_credits`` -- credits on top of what the purchase grants, landed as
  a ``trial`` row when the money does, once per account per code.

**Where each kind applies.** A top-up (pack or free amount) takes any kind.
A plan takes ``bonus_credits`` only: a plan is collected by a standing
instruction against a Razorpay plan pinned at one amount, and a per-customer
discount on that amount would need a plan per code at the gateway. That is a
follow-up, not something to fake by charging the full amount and refunding.

**When a code is checked.** At the order, before any money moves, and the
outcome is written on the order (``payments.promo_code``,
``discount_minor``). The redemption itself is recorded when the capture
webhook lands, tied to the payment, so an abandoned checkout does not use up
a limited code. That leaves a small window where two accounts can both be
told a code with one use left is theirs; the second capture still records,
because refusing money the gateway has already taken is worse than one extra
redemption of a launch code.

**Revoking never claws back.** ``active`` false stops new orders. An order
placed before, or a plan subscribed with a bonus code before, is honoured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentMandateModel,
    PaymentModel,
    PromoCodeModel,
    PromoRedemptionModel,
)
from api.enums import CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.billing.money import round_half_up_div

KIND_PERCENT = "percent"
KIND_AMOUNT = "amount"
KIND_BONUS = "bonus_credits"
KINDS = (KIND_PERCENT, KIND_AMOUNT, KIND_BONUS)

#: What a code may be limited to.
TARGET_ANY = "any"
TARGET_ANY_PLAN = "any_plan"
TARGET_ANY_PACK = "any_pack"


class PromoError(ValueError):
    """The code cannot be used on this purchase. The message is for the
    customer, in words that say what to do about it."""


def normalize(code: str | None) -> str | None:
    if not code:
        return None
    cleaned = code.strip().upper().replace(" ", "")
    return cleaned[:32] or None


@dataclass(frozen=True)
class Purchase:
    """What the code is being applied to, as checkout knows it."""

    #: pack | amount | plan
    kind: str
    #: The pack or plan code; None for a free amount.
    code: str | None
    currency: str
    #: The net price in minor units of ``currency``, before the code.
    amount_minor: int


@dataclass(frozen=True)
class Applied:
    promo: PromoCodeModel
    #: Taken off the price, in the purchase's minor units. Zero for a bonus.
    discount_minor: int
    #: Granted on capture. Zero for a discount.
    bonus_credits: int

    @property
    def code(self) -> str:
        return self.promo.code

    def as_dict(self) -> dict:
        return {
            "code": self.promo.code,
            "kind": self.promo.kind,
            "discount_minor": self.discount_minor,
            "bonus_credits": self.bonus_credits,
        }


async def get_by_code(session: AsyncSession, code: str | None) -> PromoCodeModel | None:
    normalized = normalize(code)
    if not normalized:
        return None
    return await session.scalar(
        select(PromoCodeModel).where(PromoCodeModel.code == normalized)
    )


def _applies(promo: PromoCodeModel, purchase: Purchase) -> bool:
    target = promo.applies_to or TARGET_ANY
    if target == TARGET_ANY:
        return True
    if purchase.kind == "plan":
        return target == TARGET_ANY_PLAN or target == f"plan:{purchase.code}"
    if target == TARGET_ANY_PACK:
        # A free amount is a pack-shaped purchase for this purpose: the code
        # is for prepaid credit, and that is what both buy.
        return True
    return purchase.code is not None and target == f"pack:{purchase.code}"


async def _redemptions(session: AsyncSession, promo_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(PromoRedemptionModel.id)).where(
                PromoRedemptionModel.promo_code_id == promo_id
            )
        )
        or 0
    )


async def _redemptions_by(
    session: AsyncSession, promo_id: int, organization_id: int
) -> int:
    return int(
        await session.scalar(
            select(func.count(PromoRedemptionModel.id)).where(
                PromoRedemptionModel.promo_code_id == promo_id,
                PromoRedemptionModel.organization_id == organization_id,
            )
        )
        or 0
    )


async def _has_paid_before(session: AsyncSession, organization_id: int) -> bool:
    """A captured top-up or a collected plan cycle: real money, either way."""
    return (
        await session.scalar(
            select(CreditLedgerModel.id)
            .where(
                CreditLedgerModel.organization_id == organization_id,
                CreditLedgerModel.kind.in_(
                    [CreditLedgerKind.TOPUP.value, CreditLedgerKind.PLAN.value]
                ),
            )
            .limit(1)
        )
    ) is not None


async def _is_internal(session: AsyncSession, organization_id: int) -> bool:
    return bool(
        await session.scalar(
            select(OrganizationModel.internal_billing).where(
                OrganizationModel.id == organization_id
            )
        )
    )


def _discount_for(promo: PromoCodeModel, purchase: Purchase) -> int:
    if promo.kind == KIND_PERCENT:
        return round_half_up_div(purchase.amount_minor * int(promo.value), 100)
    if promo.kind == KIND_AMOUNT:
        return min(int(promo.value), purchase.amount_minor)
    return 0


async def validate(
    session: AsyncSession,
    *,
    code: str | None,
    organization_id: int,
    purchase: Purchase,
    now: datetime | None = None,
) -> Applied:
    """The code applied to this purchase for this account, or a PromoError
    saying why not. Every refusal is worded for the customer."""
    now = now or datetime.now(UTC)
    promo = await get_by_code(session, code)
    if promo is None:
        raise PromoError("That code is not one we know.")
    if not promo.active:
        raise PromoError("That code is no longer active.")
    if promo.valid_from and now < promo.valid_from:
        raise PromoError("That code is not valid yet.")
    if promo.valid_until and now > promo.valid_until:
        raise PromoError("That code has expired.")
    if await _is_internal(session, organization_id):
        raise PromoError("Internal accounts cannot redeem codes.")
    if not _applies(promo, purchase):
        raise PromoError("That code does not apply to this purchase.")
    if purchase.kind == "plan" and promo.kind != KIND_BONUS:
        raise PromoError(
            "That code is for top-ups. On a plan, only bonus-credit codes apply."
        )
    if (
        promo.kind == KIND_AMOUNT
        and (promo.currency or "INR").upper() != purchase.currency.upper()
    ):
        raise PromoError("That code is for a different currency.")
    if promo.first_payment_only and await _has_paid_before(session, organization_id):
        raise PromoError("That code is for a first payment only.")
    if (
        promo.max_redemptions is not None
        and await _redemptions(session, promo.id) >= promo.max_redemptions
    ):
        raise PromoError("That code has been used up.")
    if await _redemptions_by(session, promo.id, organization_id) >= int(
        promo.max_per_account or 1
    ):
        raise PromoError("You have already used that code.")

    discount = _discount_for(promo, purchase)
    if promo.kind != KIND_BONUS and discount <= 0:
        raise PromoError("That code takes nothing off this purchase.")
    bonus = int(promo.value) if promo.kind == KIND_BONUS else 0
    return Applied(promo=promo, discount_minor=discount, bonus_credits=bonus)


# ---------------------------------------------------------------------------
# Recording, when the money lands
# ---------------------------------------------------------------------------


async def _grant_bonus(
    session: AsyncSession, *, organization_id: int, promo: PromoCodeModel, ref: str
) -> int:
    """The bonus as a trial row, once per account per code by index."""
    from api.services.billing.costing import current_balance_paise

    credits = int(promo.value)
    amount = credits * PAISE_PER_CREDIT
    from api.services.billing.ledger_lock import lock_organization_ledger

    await lock_organization_ledger(session, organization_id=organization_id)
    balance = await current_balance_paise(session, organization_id=organization_id)
    try:
        async with session.begin_nested():
            session.add(
                CreditLedgerModel(
                    organization_id=organization_id,
                    delta_paise=amount,
                    kind=CreditLedgerKind.TRIAL.value,
                    ref_type=f"promo:{promo.code}",
                    ref_id=str(organization_id),
                    balance_after_paise=balance + amount,
                    note=f"Promo {promo.code}: {credits:,} bonus credits ({ref})",
                )
            )
            await session.flush()
    except IntegrityError:
        logger.info(
            "Promo {} bonus for org {} was already granted", promo.code, organization_id
        )
        return 0
    return credits


async def _record(
    session: AsyncSession,
    *,
    promo: PromoCodeModel,
    organization_id: int,
    payment_id: int | None = None,
    mandate_id: int | None = None,
    discount_minor: int = 0,
    bonus_credits: int = 0,
) -> bool:
    try:
        async with session.begin_nested():
            session.add(
                PromoRedemptionModel(
                    promo_code_id=promo.id,
                    organization_id=organization_id,
                    payment_id=payment_id,
                    mandate_id=mandate_id,
                    discount_minor=discount_minor,
                    bonus_credits=bonus_credits,
                )
            )
            await session.flush()
    except IntegrityError:
        return False
    return True


async def settle_payment(
    session: AsyncSession, *, payment: PaymentModel, payment_ref: str
) -> dict:
    """Record the redemption for a captured order and grant its bonus.

    Called inside the webhook's transaction after the credit is written.
    Never raises for a business reason: the code was validated at the order,
    and the money has moved.
    """
    if not payment.promo_code:
        return {"status": "none"}
    promo = await get_by_code(session, payment.promo_code)
    if promo is None:
        logger.error(
            "Payment {} carries promo {} which no longer exists",
            payment.id,
            payment.promo_code,
        )
        return {"status": "missing"}
    bonus = 0
    if promo.kind == KIND_BONUS:
        bonus = await _grant_bonus(
            session,
            organization_id=payment.organization_id,
            promo=promo,
            ref=payment_ref,
        )
    recorded = await _record(
        session,
        promo=promo,
        organization_id=payment.organization_id,
        payment_id=payment.id,
        discount_minor=int(payment.discount_minor or 0),
        bonus_credits=bonus,
    )
    return {
        "status": "recorded" if recorded else "already_recorded",
        "code": promo.code,
        "discount_minor": int(payment.discount_minor or 0),
        "bonus_credits": bonus,
    }


async def settle_plan_collection(
    session: AsyncSession, *, mandate: PaymentMandateModel, payment_ref: str
) -> dict:
    """A plan subscribed with a bonus code: the bonus lands with the first
    collection, once, and the redemption is tied to the mandate."""
    if not mandate.promo_code:
        return {"status": "none"}
    promo = await get_by_code(session, mandate.promo_code)
    if promo is None or promo.kind != KIND_BONUS:
        return {"status": "missing"}
    bonus = await _grant_bonus(
        session, organization_id=mandate.organization_id, promo=promo, ref=payment_ref
    )
    recorded = await _record(
        session,
        promo=promo,
        organization_id=mandate.organization_id,
        mandate_id=mandate.id,
        bonus_credits=bonus,
    )
    return {
        "status": "recorded" if recorded else "already_recorded",
        "code": promo.code,
        "bonus_credits": bonus,
    }


# ---------------------------------------------------------------------------
# Staff: create, change, revoke, report
# ---------------------------------------------------------------------------

ALLOWED_TARGET_PREFIXES = ("plan:", "pack:")


def _check_target(applies_to: str) -> str:
    target = (applies_to or TARGET_ANY).strip().lower()
    if target in (TARGET_ANY, TARGET_ANY_PLAN, TARGET_ANY_PACK):
        return target
    if target.startswith(ALLOWED_TARGET_PREFIXES) and len(target) > 5:
        return target
    raise ValueError(
        "applies_to must be any, any_plan, any_pack, plan:<code> or pack:<code>"
    )


def _check_terms(*, kind: str, value: int, currency: str | None) -> str | None:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if value <= 0:
        raise ValueError("value must be positive")
    if kind == KIND_PERCENT and value > 100:
        raise ValueError("a percent code cannot take more than 100% off")
    if kind == KIND_AMOUNT:
        return (currency or "INR").upper()
    return None


async def create(
    session: AsyncSession,
    *,
    code: str,
    kind: str,
    value: int,
    currency: str | None = None,
    applies_to: str = TARGET_ANY,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    max_redemptions: int | None = None,
    max_per_account: int = 1,
    first_payment_only: bool = False,
    note: str | None = None,
    created_by: int | None = None,
) -> PromoCodeModel:
    normalized = normalize(code)
    if not normalized or len(normalized) < 3:
        raise ValueError("A code needs at least three characters")
    if await get_by_code(session, normalized) is not None:
        raise ValueError(f"{normalized} already exists")
    promo = PromoCodeModel(
        code=normalized,
        kind=kind,
        value=int(value),
        currency=_check_terms(kind=kind, value=int(value), currency=currency),
        applies_to=_check_target(applies_to),
        valid_from=valid_from,
        valid_until=valid_until,
        max_redemptions=max_redemptions,
        max_per_account=max(1, int(max_per_account or 1)),
        first_payment_only=bool(first_payment_only),
        note=note,
        created_by=created_by,
    )
    session.add(promo)
    await session.flush()
    return promo


EDITABLE = (
    "kind",
    "value",
    "currency",
    "applies_to",
    "valid_from",
    "valid_until",
    "max_redemptions",
    "max_per_account",
    "first_payment_only",
    "active",
    "note",
)


async def update(session: AsyncSession, *, promo_id: int, **fields) -> PromoCodeModel:
    """Change a code's terms. Only the fields passed change; the code itself
    never does, because it is on customers' screens and in campaigns."""
    promo = await session.get(PromoCodeModel, promo_id)
    if promo is None:
        raise ValueError(f"Promo code {promo_id} not found")
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Cannot change {', '.join(sorted(unknown))}")
    kind = fields.get("kind") or promo.kind
    value = fields["value"] if fields.get("value") is not None else promo.value
    currency = fields.get("currency", promo.currency)
    fields["currency"] = _check_terms(kind=kind, value=int(value), currency=currency)
    if "applies_to" in fields:
        fields["applies_to"] = _check_target(fields["applies_to"] or TARGET_ANY)
    if "max_per_account" in fields:
        fields["max_per_account"] = max(1, int(fields["max_per_account"] or 1))
    for key, val in fields.items():
        setattr(promo, key, val)
    await session.flush()
    return promo


async def revoke(session: AsyncSession, *, promo_id: int) -> PromoCodeModel:
    promo = await session.get(PromoCodeModel, promo_id)
    if promo is None:
        raise ValueError(f"Promo code {promo_id} not found")
    promo.active = False
    await session.flush()
    return promo


def as_dict(promo: PromoCodeModel) -> dict:
    return {
        "id": promo.id,
        "code": promo.code,
        "kind": promo.kind,
        "value": promo.value,
        "currency": promo.currency,
        "applies_to": promo.applies_to,
        "valid_from": promo.valid_from.isoformat() if promo.valid_from else None,
        "valid_until": promo.valid_until.isoformat() if promo.valid_until else None,
        "max_redemptions": promo.max_redemptions,
        "max_per_account": promo.max_per_account,
        "first_payment_only": promo.first_payment_only,
        "active": promo.active,
        "note": promo.note,
        "created_at": promo.created_at.isoformat() if promo.created_at else None,
    }


async def report(session: AsyncSession) -> list[dict]:
    """Every code with what it has cost: redemptions, discount given, bonus
    credits granted."""
    promos = list(
        (
            await session.scalars(
                select(PromoCodeModel).order_by(PromoCodeModel.created_at.desc())
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(
                PromoRedemptionModel.promo_code_id,
                func.count(PromoRedemptionModel.id),
                func.coalesce(func.sum(PromoRedemptionModel.discount_minor), 0),
                func.coalesce(func.sum(PromoRedemptionModel.bonus_credits), 0),
            ).group_by(PromoRedemptionModel.promo_code_id)
        )
    ).all()
    stats = {row[0]: (int(row[1]), int(row[2]), int(row[3])) for row in rows}
    out = []
    for promo in promos:
        count, discount, bonus = stats.get(promo.id, (0, 0, 0))
        out.append(
            {
                **as_dict(promo),
                "redemptions": count,
                "discount_minor_total": discount,
                "bonus_credits_total": bonus,
            }
        )
    return out

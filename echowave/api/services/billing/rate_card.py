"""Setting prices, from the admin panel rather than from source.

Everything Decibyl charges or pays used to live in one of two places that an
operator cannot reach: a Python constant, or the demo seed script. This module
makes the whole rate card editable — the global platform rate and pulse, volume
tiers, provider unit rates and the exchange rate — while keeping the one
property the billing engine depends on:

**Rates are never updated in place.** Setting a price closes the currently-open
row by stamping ``effective_to`` and inserts a new one. Recomputing an invoice
from last month therefore still reproduces the number that was actually
charged, no matter how many times the price has moved since.

The global default is stored as a **volume tier with a threshold of zero**.
That is not a trick: a tier matches when the account's period minutes reach its
threshold, and every account has reached zero, so a zero-threshold tier is
exactly "the price everyone pays unless something more specific applies". It
needs no new table and no new branch in :func:`resolve_platform_rate`.

The constants in ``money.py`` remain as the last-resort fallback for a database
with no rate rows at all — a fresh install, or a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    BillingAuditLogModel,
    OrganizationModel,
    OrganizationRateHistoryModel,
    PlatformVolumeTierModel,
    ProviderRateModel,
    UsdInrRateHistoryModel,
)
from api.enums import BillingAuditAction, CostComponent, RateUnit
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PULSE_SECONDS,
    DEFAULT_USD_INR_PAISE,
)

#: The threshold that means "everyone". See the module docstring.
GLOBAL_TIER_MIN_MINUTES = 0
GLOBAL_TIER_NAME = "Global default"


class RateCardError(ValueError):
    """A price change that would produce an inconsistent rate card."""


def _resolve_effective_from(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _check_currency(micros_usd: int | None, mpaise: int | None) -> None:
    """Exactly one currency, matching the database's own constraint.

    Checked here as well so the caller gets a sentence rather than an
    IntegrityError, and so the rule is stated where a reader of this module
    will see it.
    """
    if (micros_usd is None) == (mpaise is None):
        raise RateCardError(
            "Give a price in dollars or in rupees, not both and not neither. "
            "Dollars is the list price; rupees is for a contract written that way."
        )
    if micros_usd is not None and micros_usd < 0:
        raise RateCardError("A price cannot be negative.")
    if mpaise is not None and mpaise < 0:
        raise RateCardError("A price cannot be negative.")


def _check_pulse(pulse_seconds: int | None) -> None:
    if pulse_seconds is None:
        return
    if pulse_seconds < 1 or pulse_seconds > 60:
        raise RateCardError(
            "The pulse must be between 1 and 60 seconds. Above 60 it is no "
            "longer a pulse, it is minute billing with extra steps."
        )


async def _close_open_row(session: AsyncSession, row, effective_from: datetime) -> None:
    """Stamp the outgoing row's end so the two windows meet exactly.

    Refuses to close a row that has not started yet: two rows opening at the
    same instant would leave the resolver picking arbitrarily between them.
    """
    if row is None:
        return
    if row.effective_from >= effective_from:
        raise RateCardError(
            "The new price must start after the current one did. "
            f"The current price took effect at {row.effective_from.isoformat()}."
        )
    row.effective_to = effective_from


def _audit(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    action: BillingAuditAction,
    old_value: dict,
    new_value: dict,
    organization_id: int | None = None,
    note: str | None = None,
) -> None:
    session.add(
        BillingAuditLogModel(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action.value,
            old_value=old_value,
            new_value=new_value,
            note=note,
        )
    )


# ---------------------------------------------------------------------------
# The global default, and volume tiers
# ---------------------------------------------------------------------------


async def _open_tier(
    session: AsyncSession, min_period_minutes: int
) -> PlatformVolumeTierModel | None:
    return await session.scalar(
        select(PlatformVolumeTierModel)
        .where(
            PlatformVolumeTierModel.min_period_minutes == min_period_minutes,
            PlatformVolumeTierModel.effective_to.is_(None),
        )
        .order_by(PlatformVolumeTierModel.effective_from.desc())
        .limit(1)
    )


async def set_volume_tier(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    min_period_minutes: int,
    name: str,
    platform_rate_micros_usd: int | None = None,
    platform_rate_mpaise: int | None = None,
    pulse_seconds: int | None = None,
    effective_from: datetime | None = None,
    note: str | None = None,
) -> PlatformVolumeTierModel:
    """Open a new price at a volume threshold, closing the previous one.

    ``min_period_minutes = 0`` is the global default — the price an account
    pays with no override and no volume reached.
    """
    if min_period_minutes < 0:
        raise RateCardError("A volume threshold cannot be negative.")
    _check_currency(platform_rate_micros_usd, platform_rate_mpaise)
    _check_pulse(pulse_seconds)

    at = _resolve_effective_from(effective_from)
    current = await _open_tier(session, min_period_minutes)
    old = (
        {
            "platform_rate_micros_usd": current.platform_rate_micros_usd,
            "platform_rate_mpaise": current.platform_rate_mpaise,
            "pulse_seconds": current.pulse_seconds,
        }
        if current is not None
        else {}
    )
    await _close_open_row(session, current, at)

    row = PlatformVolumeTierModel(
        name=name,
        min_period_minutes=min_period_minutes,
        platform_rate_micros_usd=platform_rate_micros_usd,
        platform_rate_mpaise=platform_rate_mpaise,
        pulse_seconds=pulse_seconds,
        effective_from=at,
    )
    session.add(row)

    _audit(
        session,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.PLATFORM_RATE_CHANGED,
        old_value=old,
        new_value={
            "min_period_minutes": min_period_minutes,
            "platform_rate_micros_usd": platform_rate_micros_usd,
            "platform_rate_mpaise": platform_rate_mpaise,
            "pulse_seconds": pulse_seconds,
            "effective_from": at.isoformat(),
        },
        note=note,
    )
    logger.info(
        "Platform rate at >={} min set to {} by user {}",
        min_period_minutes,
        platform_rate_micros_usd or platform_rate_mpaise,
        actor_user_id,
    )
    return row


async def retire_volume_tier(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    min_period_minutes: int,
    effective_from: datetime | None = None,
) -> None:
    """Stop a tier applying, without deleting the history behind it.

    The global tier cannot be retired: removing it would silently drop every
    account back to a constant in source, which is the situation this module
    exists to end.
    """
    if min_period_minutes == GLOBAL_TIER_MIN_MINUTES:
        raise RateCardError(
            "The global default cannot be removed — change its price instead."
        )

    current = await _open_tier(session, min_period_minutes)
    if current is None:
        raise RateCardError("No open tier at that threshold.")

    at = _resolve_effective_from(effective_from)
    await _close_open_row(session, current, at)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.PLATFORM_RATE_CHANGED,
        old_value={
            "min_period_minutes": min_period_minutes,
            "platform_rate_micros_usd": current.platform_rate_micros_usd,
            "platform_rate_mpaise": current.platform_rate_mpaise,
        },
        new_value={"retired_at": at.isoformat()},
    )


# ---------------------------------------------------------------------------
# Provider unit rates
# ---------------------------------------------------------------------------


async def set_provider_rate(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    provider: str,
    component: CostComponent | str,
    unit: RateUnit | str,
    rate_mpaise: int,
    model: str = "",
    effective_from: datetime | None = None,
    note: str | None = None,
) -> ProviderRateModel:
    """Open a new provider unit rate, closing the previous one for that key.

    Keyed by (provider, model, component) — the same key the resolver uses, so
    what is edited here is exactly what gets billed. A model-specific rate and
    the provider-wide fallback (``model = ""``) are separate rows and are
    closed independently.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    unit_value = unit.value if isinstance(unit, RateUnit) else str(unit)
    # Validate against the enums rather than trusting the caller: an unknown
    # unit would be stored happily and then fail at costing time, on a call.
    try:
        CostComponent(component_value)
        RateUnit(unit_value)
    except ValueError as exc:
        raise RateCardError(str(exc)) from exc

    if rate_mpaise < 0:
        raise RateCardError("A rate cannot be negative.")

    provider = provider.strip().lower()
    model = (model or "").strip().lower()
    if not provider:
        raise RateCardError("Name the provider this rate is for.")

    at = _resolve_effective_from(effective_from)
    current = await session.scalar(
        select(ProviderRateModel)
        .where(
            ProviderRateModel.provider == provider,
            ProviderRateModel.model == model,
            ProviderRateModel.component == component_value,
            ProviderRateModel.effective_to.is_(None),
        )
        .order_by(ProviderRateModel.effective_from.desc())
        .limit(1)
    )
    old = {"rate_mpaise": current.rate_mpaise} if current is not None else {}
    await _close_open_row(session, current, at)

    row = ProviderRateModel(
        provider=provider,
        model=model,
        component=component_value,
        unit=unit_value,
        rate_mpaise=rate_mpaise,
        effective_from=at,
        note=note,
    )
    session.add(row)

    _audit(
        session,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.PROVIDER_RATE_CHANGED,
        old_value=old,
        new_value={
            "provider": provider,
            "model": model,
            "component": component_value,
            "unit": unit_value,
            "rate_mpaise": rate_mpaise,
            "effective_from": at.isoformat(),
        },
        note=note,
    )
    return row


async def retire_provider_rate(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    provider: str,
    component: CostComponent | str,
    model: str = "",
    effective_from: datetime | None = None,
) -> None:
    """Stop pricing a provider or model.

    Deliberately leaves the usage **uncosted** rather than free: the cost
    engine reports usage with no rate on file, and the unit-economics screen
    counts it. Silently pricing at zero would overstate margin.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    current = await session.scalar(
        select(ProviderRateModel)
        .where(
            ProviderRateModel.provider == provider.strip().lower(),
            ProviderRateModel.model == (model or "").strip().lower(),
            ProviderRateModel.component == component_value,
            ProviderRateModel.effective_to.is_(None),
        )
        .order_by(ProviderRateModel.effective_from.desc())
        .limit(1)
    )
    if current is None:
        raise RateCardError("No open rate for that provider and model.")

    at = _resolve_effective_from(effective_from)
    await _close_open_row(session, current, at)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.PROVIDER_RATE_CHANGED,
        old_value={"rate_mpaise": current.rate_mpaise},
        new_value={"retired_at": at.isoformat()},
    )


# ---------------------------------------------------------------------------
# Exchange rate
# ---------------------------------------------------------------------------


async def set_exchange_rate(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    paise_per_usd: int,
    source: str | None = None,
    note: str | None = None,
) -> UsdInrRateHistoryModel:
    """Open a new USD→INR rate.

    Always takes effect **now**, never retroactively. Backdating this would
    reprice calls that have already been invoiced, which is not something a
    form should be able to do.
    """
    if paise_per_usd <= 0:
        raise RateCardError("The exchange rate must be greater than zero.")

    at = datetime.now(UTC)
    current = await session.scalar(
        select(UsdInrRateHistoryModel)
        .where(UsdInrRateHistoryModel.effective_to.is_(None))
        .order_by(UsdInrRateHistoryModel.effective_from.desc())
        .limit(1)
    )
    old = {"paise_per_usd": current.paise_per_usd} if current is not None else {}
    if current is not None:
        current.effective_to = at

    row = UsdInrRateHistoryModel(
        paise_per_usd=paise_per_usd,
        effective_from=at,
        source=source,
        set_by=actor_user_id,
        note=note,
    )
    session.add(row)

    _audit(
        session,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.EXCHANGE_RATE_CHANGED,
        old_value=old,
        new_value={"paise_per_usd": paise_per_usd, "source": source},
        note=note,
    )
    return row


# ---------------------------------------------------------------------------
# Reading the card
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateCard:
    """Every price currently in force, and where each one comes from."""

    global_tier: dict | None
    volume_tiers: list[dict]
    provider_rates: list[dict]
    exchange_rate: dict | None
    #: True when no zero-threshold tier exists, so the platform rate is coming
    #: from the constant in money.py rather than from anything an operator set.
    using_fallback_platform_rate: bool
    fallback: dict


def _tier_dict(row: PlatformVolumeTierModel) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "min_period_minutes": row.min_period_minutes,
        "platform_rate_micros_usd": row.platform_rate_micros_usd,
        "platform_rate_mpaise": row.platform_rate_mpaise,
        "pulse_seconds": row.pulse_seconds or DEFAULT_PULSE_SECONDS,
        "pulse_is_default": row.pulse_seconds is None,
        "effective_from": row.effective_from.isoformat(),
    }


async def get_rate_card(session: AsyncSession) -> RateCard:
    """The prices in force right now, for the admin screen."""
    tiers = list(
        (
            await session.scalars(
                select(PlatformVolumeTierModel)
                .where(PlatformVolumeTierModel.effective_to.is_(None))
                .order_by(PlatformVolumeTierModel.min_period_minutes)
            )
        ).all()
    )
    global_tier = next(
        (t for t in tiers if t.min_period_minutes == GLOBAL_TIER_MIN_MINUTES), None
    )

    provider_rates = list(
        (
            await session.scalars(
                select(ProviderRateModel)
                .where(ProviderRateModel.effective_to.is_(None))
                .order_by(
                    ProviderRateModel.component,
                    ProviderRateModel.provider,
                    ProviderRateModel.model,
                )
            )
        ).all()
    )

    fx = await session.scalar(
        select(UsdInrRateHistoryModel)
        .where(UsdInrRateHistoryModel.effective_to.is_(None))
        .order_by(UsdInrRateHistoryModel.effective_from.desc())
        .limit(1)
    )

    return RateCard(
        global_tier=_tier_dict(global_tier) if global_tier else None,
        volume_tiers=[
            _tier_dict(t)
            for t in tiers
            if t.min_period_minutes != GLOBAL_TIER_MIN_MINUTES
        ],
        provider_rates=[
            {
                "id": r.id,
                "provider": r.provider,
                "model": r.model or None,
                "component": r.component,
                "unit": r.unit,
                "rate_mpaise": r.rate_mpaise,
                # Both currencies, derived from one stored number. Vendors quote
                # in dollars and we invoice in rupees, so a card showing only
                # one of them makes somebody do the conversion in their head at
                # a rate they have guessed.
                "rate_inr": r.rate_mpaise / 100_000,
                "rate_usd": (
                    r.rate_mpaise / (fx.paise_per_usd * 1000)
                    if fx and fx.paise_per_usd
                    else None
                ),
                "effective_from": r.effective_from.isoformat(),
                "note": r.note,
            }
            for r in provider_rates
        ],
        exchange_rate=(
            {
                "paise_per_usd": fx.paise_per_usd,
                "source": fx.source,
                "effective_from": fx.effective_from.isoformat(),
                "recorded_at": (fx.created_at or fx.effective_from).isoformat(),
            }
            if fx
            else None
        ),
        using_fallback_platform_rate=global_tier is None,
        fallback={
            "platform_rate_micros_usd": DEFAULT_PLATFORM_RATE_MICROS_USD,
            "pulse_seconds": DEFAULT_PULSE_SECONDS,
            "paise_per_usd": DEFAULT_USD_INR_PAISE,
        },
    )


async def set_account_rate(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    organization_id: int,
    platform_rate_micros_usd: int | None = None,
    platform_rate_mpaise: int | None = None,
    pulse_seconds: int | None = None,
    effective_from: datetime | None = None,
    note: str | None = None,
) -> OrganizationRateHistoryModel:
    """Override one account's price, in either currency, with its own pulse."""
    _check_currency(platform_rate_micros_usd, platform_rate_mpaise)
    _check_pulse(pulse_seconds)

    org = await session.get(OrganizationModel, organization_id)
    if org is None:
        raise RateCardError("Account not found.")

    at = _resolve_effective_from(effective_from)
    current = await session.scalar(
        select(OrganizationRateHistoryModel)
        .where(
            OrganizationRateHistoryModel.organization_id == organization_id,
            OrganizationRateHistoryModel.effective_to.is_(None),
        )
        .order_by(OrganizationRateHistoryModel.effective_from.desc())
        .limit(1)
    )
    old = (
        {
            "platform_rate_micros_usd": current.platform_rate_micros_usd,
            "platform_rate_mpaise": current.platform_rate_mpaise,
            "pulse_seconds": current.pulse_seconds,
        }
        if current is not None
        else {}
    )
    await _close_open_row(session, current, at)

    row = OrganizationRateHistoryModel(
        organization_id=organization_id,
        platform_rate_micros_usd=platform_rate_micros_usd,
        platform_rate_mpaise=platform_rate_mpaise,
        pulse_seconds=pulse_seconds,
        effective_from=at,
        set_by=actor_user_id,
        note=note,
    )
    session.add(row)
    # Convenience mirror on the account, for list screens. Only meaningful for
    # a rupee-native rate: a dollar price has no fixed rupee value.
    org.platform_rate_mpaise = platform_rate_mpaise

    _audit(
        session,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=BillingAuditAction.PLATFORM_RATE_CHANGED,
        old_value=old,
        new_value={
            "platform_rate_micros_usd": platform_rate_micros_usd,
            "platform_rate_mpaise": platform_rate_mpaise,
            "pulse_seconds": pulse_seconds,
            "effective_from": at.isoformat(),
        },
        note=note,
    )
    return row

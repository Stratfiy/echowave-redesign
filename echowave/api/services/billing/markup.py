"""The multiple charged on managed model usage, and how it gets changed.

One number decides what every managed call in the system costs. Moving it from
1.3 to 1.4 changes the price of every account at once, silently, with no
deploy — which is the point of making it editable and also the reason it is the
only setting here that needs a code from the inbox before it applies.

Three properties carry this module.

**The value is effective-dated, never overwritten.** Re-costing a call from
March has to reproduce what that customer was actually charged, and a mutable
setting makes that impossible the first time anyone edits it. Changing the
markup closes the open row and inserts a new one, exactly as the platform rate
does.

**The proposed value lives with the challenge, not in the confirming request.**
The confirmation carries only a code. Someone who replays or tampers with it
cannot swap in a different multiple: the number that applies is the one that
was emailed about, or nothing applies at all.

**The environment variable remains the seed.** With no rows in the table the
resolver returns ``MANAGED_PROVIDER_MARKUP_BPS``, so nothing breaks before the
first edit and a fresh install behaves exactly as it did.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import MANAGED_PROVIDER_MARKUP_BPS
from api.db.models import (
    ManagedMarkupHistoryModel,
    ManagedMarkupOverrideModel,
    MarkupChangeChallengeModel,
)
from api.enums import CostComponent
from api.services.auth import otp

#: Where a code for a markup change is sent. Deliberately a fixed operator
#: address rather than the signed-in user's: the point of the second factor is
#: that changing the price of every account needs access to the company inbox,
#: not merely a session that is already open.
MARKUP_NOTICE_ADDRESS = "hello@decibyl.ai"

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5

#: 1.0x to 3.0x. The floor is the interesting one: below 10000 every managed
#: call sells model usage for less than the vendor charged, on every account,
#: until somebody notices. The ceiling is a fat-finger guard — 140000 instead
#: of 14000 is one keystroke.
MIN_MARKUP_BPS = 10_000
MAX_MARKUP_BPS = 30_000


class MarkupError(ValueError):
    """A markup change could not be started or applied."""


@dataclass(frozen=True)
class AppliedMarkupChange:
    """What actually changed, for the audit row and the response.

    Carries the previous value as well as the new one because the caller needs
    both and the history row only knows the latter — reading the closed row
    back would be a second query for something already in hand.
    """

    markup_bps: int
    previous_markup_bps: int
    effective_from: datetime
    note: str | None


@dataclass(frozen=True)
class StartedMarkupChange:
    """A pending change and the code that would apply it."""

    markup_bps: int
    previous_markup_bps: int
    code: str
    expires_at: datetime
    address: str = MARKUP_NOTICE_ADDRESS


def validate_markup_bps(markup_bps: object) -> int:
    """Coerce and bound a proposed multiple.

    Rejected here as well as in the schema: the schema stops a bad value being
    submitted, this stops one being applied by any other caller.
    """
    try:
        value = int(markup_bps)
    except (TypeError, ValueError) as exc:
        raise MarkupError("The markup must be a whole number of basis points.") from exc

    if not MIN_MARKUP_BPS <= value <= MAX_MARKUP_BPS:
        raise MarkupError(
            f"The markup must be between {MIN_MARKUP_BPS / 10_000:g}x and "
            f"{MAX_MARKUP_BPS / 10_000:g}x. Below cost would sell every managed "
            "call for less than the vendor charged."
        )
    return value


async def resolve_markup_bps(
    session: AsyncSession, *, at: datetime | None = None
) -> int:
    """The multiple in force, falling back to the environment.

    ``at`` reads the value as it stood at a moment, so re-costing an old call
    prices it against the markup that was actually applied rather than
    today's — the whole reason this is a history rather than a setting.
    """
    moment = at or datetime.now(UTC)
    row = (
        await session.execute(
            select(ManagedMarkupHistoryModel)
            .where(
                ManagedMarkupHistoryModel.effective_from <= moment,
                (ManagedMarkupHistoryModel.effective_to.is_(None))
                | (ManagedMarkupHistoryModel.effective_to > moment),
            )
            .order_by(ManagedMarkupHistoryModel.effective_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return row.markup_bps if row is not None else MANAGED_PROVIDER_MARKUP_BPS


async def start_change(
    session: AsyncSession,
    *,
    markup_bps: int,
    actor_user_id: int | None,
    note: str | None = None,
    now: datetime | None = None,
) -> StartedMarkupChange:
    """Stage a change and issue its code. Does not apply anything.

    Sending the mail is the caller's job, so the limits above can be tested
    without a mail server and a delivery failure never has to be untangled
    from a validation one.
    """
    moment = now or datetime.now(UTC)
    value = validate_markup_bps(markup_bps)
    current = await resolve_markup_bps(session, at=moment)

    if value == current:
        raise MarkupError(
            f"The markup is already {value / 10_000:g}x. Nothing would change."
        )

    code = otp.generate_code()
    salt = otp.generate_salt()
    expires_at = moment + timedelta(minutes=CODE_TTL_MINUTES)

    # One pending change at a time. Replacing rather than adding is what makes
    # an abandoned change harmless, and stops a stale code from an earlier
    # attempt applying a value nobody is looking at any more.
    for existing in (
        await session.execute(select(MarkupChangeChallengeModel))
    ).scalars():
        await session.delete(existing)
    await session.flush()

    session.add(
        MarkupChangeChallengeModel(
            markup_bps=value,
            previous_markup_bps=current,
            note=note,
            code_hash=otp.hash_code(code, salt),
            code_salt=salt,
            expires_at=expires_at,
            requested_by=actor_user_id,
            created_at=moment,
        )
    )
    await session.flush()

    # The value, never the code. A log line carrying the code is a way for
    # anyone who can read logs to change what every account pays.
    logger.info(
        "Markup change staged: {}x -> {}x, awaiting confirmation",
        current / 10_000,
        value / 10_000,
    )
    return StartedMarkupChange(
        markup_bps=value,
        previous_markup_bps=current,
        code=code,
        expires_at=expires_at,
    )


async def confirm_change(
    session: AsyncSession,
    *,
    code: str,
    actor_user_id: int | None,
    now: datetime | None = None,
) -> AppliedMarkupChange:
    """Apply the pending change, if the code is right.

    Closes the open history row and opens a new one. The value applied is the
    one staged — the caller does not get to say what it is.
    """
    moment = now or datetime.now(UTC)
    challenge = (
        await session.execute(select(MarkupChangeChallengeModel).limit(1))
    ).scalar_one_or_none()

    if challenge is None:
        raise MarkupError("No markup change is waiting to be confirmed.")

    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if moment >= expires_at:
        await session.delete(challenge)
        await session.flush()
        raise MarkupError("That code has expired. Start the change again.")

    if challenge.attempts >= MAX_ATTEMPTS:
        await session.delete(challenge)
        await session.flush()
        raise MarkupError(
            "Too many incorrect codes. The change has been cancelled — start it again."
        )

    expected = otp.hash_code((code or "").strip(), challenge.code_salt)
    # Constant time: a plain == leaks how much of a guess was right through
    # how long the comparison took.
    if not hmac.compare_digest(expected, challenge.code_hash):
        challenge.attempts += 1
        await session.flush()
        raise MarkupError("That code is not right.")

    value = challenge.markup_bps
    previous = challenge.previous_markup_bps

    # Close whatever is open before opening the new row, or the partial unique
    # index refuses the insert — which is the index doing its job.
    await session.execute(
        update(ManagedMarkupHistoryModel)
        .where(ManagedMarkupHistoryModel.effective_to.is_(None))
        .values(effective_to=moment)
    )
    await session.flush()

    note = challenge.note
    session.add(
        ManagedMarkupHistoryModel(
            markup_bps=value,
            effective_from=moment,
            set_by=actor_user_id,
            note=note,
            created_at=moment,
        )
    )
    await session.delete(challenge)
    await session.flush()

    logger.info(
        "Markup changed: {}x -> {}x by user {}",
        previous / 10_000,
        value / 10_000,
        actor_user_id,
    )
    return AppliedMarkupChange(
        markup_bps=value,
        previous_markup_bps=previous,
        effective_from=moment,
        note=note,
    )


#: Same bounds as the global markup. A per-model override that sold below cost
#: or fat-fingered a fifth digit would be exactly as damaging on one line as
#: the global value would be on every line — just slower to notice, because
#: it only shows up in one model's margin.
MIN_OVERRIDE_MARKUP_BPS = MIN_MARKUP_BPS
MAX_OVERRIDE_MARKUP_BPS = MAX_MARKUP_BPS


@dataclass(frozen=True)
class MarkupOverride:
    """A per-``(component, provider, model)`` markup as the rest of the system
    reads it — a view over the row, not the ORM object, for the same reason
    ``subscription_plans.Plan`` is a view rather than the model."""

    id: int
    provider: str
    component: str
    #: "" is the provider-wide fallback, matching ProviderRateModel.
    model: str
    markup_bps: int
    effective_from: datetime
    note: str | None


def _view(row: ManagedMarkupOverrideModel) -> MarkupOverride:
    return MarkupOverride(
        id=row.id,
        provider=row.provider,
        component=row.component,
        model=row.model or "",
        markup_bps=row.markup_bps,
        effective_from=row.effective_from,
        note=row.note,
    )


async def resolve_markup_override_bps(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    at: datetime,
    model: str = "",
) -> int | None:
    """The markup override in force for this line, or ``None`` if there is
    none — in which case the caller falls back to ``resolve_markup_bps``.

    Most specific wins, exactly ``resolve_provider_rate``'s rule: a
    model-specific row beats the provider-wide ``model=""`` fallback.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    normalized_model = (model or "").strip().lower()

    row = await session.scalar(
        select(ManagedMarkupOverrideModel)
        .where(
            ManagedMarkupOverrideModel.provider == provider,
            ManagedMarkupOverrideModel.component == component_value,
            ManagedMarkupOverrideModel.model.in_(
                [normalized_model, ""] if normalized_model else [""]
            ),
            ManagedMarkupOverrideModel.effective_from <= at,
            or_(
                ManagedMarkupOverrideModel.effective_to.is_(None),
                ManagedMarkupOverrideModel.effective_to > at,
            ),
        )
        .order_by(
            (ManagedMarkupOverrideModel.model == "").asc(),
            ManagedMarkupOverrideModel.effective_from.desc(),
        )
        .limit(1)
    )
    return row.markup_bps if row is not None else None


async def list_markup_overrides(
    session: AsyncSession, *, at: datetime | None = None
) -> list[MarkupOverride]:
    """Every override currently in force, for the admin screen.

    Ordered by provider then component then model so the same list renders in
    the same grouping every time, rather than in whatever order rows happen to
    have been written.
    """
    moment = at or datetime.now(UTC)
    rows = (
        await session.execute(
            select(ManagedMarkupOverrideModel).where(
                and_(
                    ManagedMarkupOverrideModel.effective_from <= moment,
                    or_(
                        ManagedMarkupOverrideModel.effective_to.is_(None),
                        ManagedMarkupOverrideModel.effective_to > moment,
                    ),
                )
            )
        )
    ).scalars()
    return sorted(
        (_view(row) for row in rows),
        key=lambda o: (o.provider, o.component, o.model),
    )


async def set_markup_override(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    markup_bps: int,
    actor_user_id: int | None,
    model: str = "",
    note: str | None = None,
    now: datetime | None = None,
) -> MarkupOverride:
    """Set (or replace) the markup override for one ``(component, provider,
    model)``. No OTP — see ``ManagedMarkupOverrideModel`` for why a
    single-line override does not need the ceremony the global value does.

    Closes whatever is open for this exact key before opening the new row —
    the partial unique index would refuse the insert otherwise, same as every
    other effective-dated table here.
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    provider = (provider or "").strip().lower()
    if not provider:
        raise MarkupError("An override needs a provider.")
    normalized_model = (model or "").strip().lower()
    value = validate_markup_bps(markup_bps)
    if not MIN_OVERRIDE_MARKUP_BPS <= value <= MAX_OVERRIDE_MARKUP_BPS:
        raise MarkupError(
            f"The markup must be between {MIN_OVERRIDE_MARKUP_BPS / 10_000:g}x "
            f"and {MAX_OVERRIDE_MARKUP_BPS / 10_000:g}x."
        )

    moment = now or datetime.now(UTC)
    await session.execute(
        update(ManagedMarkupOverrideModel)
        .where(
            ManagedMarkupOverrideModel.provider == provider,
            ManagedMarkupOverrideModel.component == component_value,
            ManagedMarkupOverrideModel.model == normalized_model,
            ManagedMarkupOverrideModel.effective_to.is_(None),
        )
        .values(effective_to=moment)
    )
    await session.flush()

    row = ManagedMarkupOverrideModel(
        provider=provider,
        component=component_value,
        model=normalized_model,
        markup_bps=value,
        effective_from=moment,
        set_by=actor_user_id,
        note=note,
    )
    session.add(row)
    await session.flush()

    logger.info(
        "Markup override set: {}:{}/{} -> {}x by user {}",
        component_value,
        provider,
        normalized_model or "*",
        value / 10_000,
        actor_user_id,
    )
    return _view(row)


async def clear_markup_override(
    session: AsyncSession,
    *,
    provider: str,
    component: CostComponent | str,
    actor_user_id: int | None,
    model: str = "",
    now: datetime | None = None,
) -> bool:
    """Remove the override for one key, returning it to the global markup.

    Closes the open row rather than deleting it — the history stays for
    re-costing calls billed while the override was in force, the same rule
    every other rate table here follows. Returns ``False`` when nothing was
    open, so a caller can tell "cleared" from "there was nothing to clear."
    """
    component_value = (
        component.value if isinstance(component, CostComponent) else str(component)
    )
    provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    moment = now or datetime.now(UTC)

    result = await session.execute(
        update(ManagedMarkupOverrideModel)
        .where(
            ManagedMarkupOverrideModel.provider == provider,
            ManagedMarkupOverrideModel.component == component_value,
            ManagedMarkupOverrideModel.model == normalized_model,
            ManagedMarkupOverrideModel.effective_to.is_(None),
        )
        .values(effective_to=moment)
    )
    await session.flush()
    cleared = result.rowcount is not None and result.rowcount > 0
    if cleared:
        logger.info(
            "Markup override cleared: {}:{}/{} by user {}",
            component_value,
            provider,
            normalized_model or "*",
            actor_user_id,
        )
    return cleared


def notice_subject() -> str:
    return "Confirm a pricing change on Decibyl"


def notice_body(change: StartedMarkupChange) -> str:
    """Says what would change, not just that something would.

    A code with no context is one somebody approves reflexively. The old and
    new multiples are both here so the reader can tell a legitimate change from
    one they did not start.
    """
    return (
        "Someone asked to change the managed model markup on Decibyl.\n\n"
        f"    {change.previous_markup_bps / 10_000:g}x  ->  "
        f"{change.markup_bps / 10_000:g}x\n\n"
        f"Confirmation code: {change.code}\n\n"
        f"It expires in {CODE_TTL_MINUTES} minutes and applies to every managed "
        "call on every account from the moment it is used.\n\n"
        "If this was not you, do nothing. The change cannot apply without this "
        "code, and it will expire on its own."
    )

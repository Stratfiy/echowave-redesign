"""Every cap a plan carries, as rows, never as constants.

Decided 14 Sept 2026 (KAN-47 section 7, KAN-53): the ladder's caps -- bots,
team members, concurrent calls, campaign dials a day, routines, knowledge
pages, builder messages and the rest -- live in ``plan_limits``, one row per
plan per key, so an operator can move one without a release and so the app
can show a customer *why* they hit a wall and *where* the next rung is.

Three things this module fixes in one place:

* **The registry.** ``LIMITS`` names every key the product knows, its unit,
  and whether the figure was decided or is still a proposal (the spec marks
  concurrency, dial caps, routine intervals and the like *proposed*, and the
  super-admin screen says so). A key not in the registry cannot be seeded or
  saved: an unknown cap is a typo, not a feature.
* **The seed.** ``SEED`` is section 7 of the spec, verbatim. Seeded once per
  plan-and-key and never overwritten, so an operator's edit survives a deploy.
* **The raise-this path.** :func:`resolve` returns the cap for an account
  together with the plan a customer would move to for more, or ``support``
  when no rung above has more. Every screen that refuses on a cap shows that.

Nothing here *enforces* a cap. Enforcement is per feature (knowledge pages is
KAN-57, builder allowance KAN-56, dial caps with the campaign runner) and each
reads its figure through :func:`resolve`, which is what keeps a cap from being
a constant again by the back door.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import PlanLimitModel

#: ``None`` as a stored value means unlimited. Said once here so nobody reads
#: a null as zero, which would be the opposite of what it means.
UNLIMITED = None


@dataclass(frozen=True)
class LimitSpec:
    key: str
    label: str
    unit: str
    #: False for the figures the spec marks *proposed*: seeded so the table is
    #: complete, shown as proposals in the admin screen, and moved without a
    #: release when the founder decides.
    decided: bool
    #: Which way "more" goes. Every cap here is a ceiling.
    note: str = ""


LIMITS: tuple[LimitSpec, ...] = (
    LimitSpec("bots", "Bots", "bots", False),
    LimitSpec("team_members", "Team members", "people", False),
    LimitSpec(
        "concurrent_calls",
        "Concurrent calls",
        "calls",
        False,
        "Protects the media box; moves up with the infra stages.",
    ),
    LimitSpec("campaign_dials_per_day", "Campaign dials a day", "dials", False),
    LimitSpec("routines", "Routines", "routines", False),
    LimitSpec(
        "routine_min_interval_minutes", "Routine minimum interval", "minutes", False
    ),
    LimitSpec("knowledge_pages", "Knowledge pages", "pages", True),
    LimitSpec("single_upload_mb", "Single upload", "MB", False),
    LimitSpec("api_requests_per_minute", "API rate limit", "requests a minute", False),
    LimitSpec("webhook_retries", "Webhook retries", "retries", False),
    LimitSpec("recording_retention_days", "Recording retention", "days", False),
    LimitSpec(
        "desktop_steps_per_task",
        "Desktop companion steps a task",
        "steps",
        True,
        "Zero means the companion is not included.",
    ),
    LimitSpec("builder_messages", "Builder messages a month", "messages", True),
    LimitSpec(
        "builder_voice_minutes", "Builder voice minutes a month", "minutes", False
    ),
    LimitSpec(
        "topup_balance_ceiling_credits", "Top-up balance ceiling", "credits", False
    ),
    LimitSpec(
        "chat_context_tokens",
        "Chat memory",
        "tokens",
        False,
        "How much of a conversation a bot keeps in mind when it replies. "
        "Every reply carries this much, so it is a cost as well as a feature.",
    ),
)

LIMITS_BY_KEY: dict[str, LimitSpec] = {spec.key: spec for spec in LIMITS}

#: The ladder, in the order a customer climbs it. Used to find the next rung
#: with more of a thing. Codes only; the plans themselves are rows.
LADDER: tuple[str, ...] = ("free", "everyday", "business", "growth", "scale")

#: Section 7 of the spec, one column per plan. ``None`` is unlimited. Campus
#: Builder carries Business's caps by decision (KAN-69).
SEED: dict[str, dict[str, int | None]] = {
    "free": {
        "bots": 1,
        "team_members": 1,
        "concurrent_calls": 0,
        "campaign_dials_per_day": 0,
        "routines": 2,
        "routine_min_interval_minutes": 24 * 60,
        "knowledge_pages": 50,
        "single_upload_mb": 10,
        "api_requests_per_minute": 30,
        "webhook_retries": 3,
        "recording_retention_days": 30,
        "desktop_steps_per_task": 0,
        "builder_messages": 30,
        "builder_voice_minutes": 10,
        "topup_balance_ceiling_credits": 2_000,
        "chat_context_tokens": 8_000,
    },
    "everyday": {
        "bots": 3,
        "team_members": 2,
        "concurrent_calls": 0,
        "campaign_dials_per_day": 0,
        "routines": 10,
        "routine_min_interval_minutes": 60,
        "knowledge_pages": 500,
        "single_upload_mb": 25,
        "api_requests_per_minute": 60,
        "webhook_retries": 5,
        "recording_retention_days": 90,
        "desktop_steps_per_task": 0,
        "builder_messages": 30,
        "builder_voice_minutes": 30,
        "topup_balance_ceiling_credits": 20_000,
        "chat_context_tokens": 16_000,
    },
    "business": {
        "bots": 10,
        "team_members": 5,
        "concurrent_calls": 5,
        "campaign_dials_per_day": 500,
        "routines": 50,
        "routine_min_interval_minutes": 15,
        "knowledge_pages": 2_000,
        "single_upload_mb": 100,
        "api_requests_per_minute": 300,
        "webhook_retries": 10,
        "recording_retention_days": 90,
        "desktop_steps_per_task": 200,
        "builder_messages": 100,
        "builder_voice_minutes": 100,
        "topup_balance_ceiling_credits": 100_000,
        "chat_context_tokens": 32_000,
    },
    "growth": {
        "bots": 30,
        "team_members": 15,
        "concurrent_calls": 15,
        "campaign_dials_per_day": 2_000,
        "routines": 200,
        "routine_min_interval_minutes": 5,
        "knowledge_pages": 10_000,
        "single_upload_mb": 250,
        "api_requests_per_minute": 1_000,
        "webhook_retries": 10,
        "recording_retention_days": 180,
        "desktop_steps_per_task": 500,
        "builder_messages": 300,
        "builder_voice_minutes": 300,
        "topup_balance_ceiling_credits": 500_000,
        "chat_context_tokens": 64_000,
    },
    "scale": {
        "bots": UNLIMITED,
        "team_members": UNLIMITED,
        "concurrent_calls": 40,
        "campaign_dials_per_day": 10_000,
        "routines": UNLIMITED,
        "routine_min_interval_minutes": 1,
        "knowledge_pages": 50_000,
        "single_upload_mb": 1_024,
        "api_requests_per_minute": 3_000,
        "webhook_retries": 10,
        "recording_retention_days": 365,
        "desktop_steps_per_task": 1_000,
        "builder_messages": UNLIMITED,
        "builder_voice_minutes": UNLIMITED,
        "topup_balance_ceiling_credits": UNLIMITED,
        "chat_context_tokens": 128_000,
    },
}
SEED["campus"] = dict(SEED["business"])
#: The plan that existed before the ladder. Business is its successor at the
#: same price, so it carries Business's caps for the accounts still on it.
SEED["starter"] = dict(SEED["business"])


class LimitError(ValueError):
    """A cap could not be saved."""


@dataclass(frozen=True)
class Limit:
    """One cap as an account experiences it."""

    key: str
    plan_code: str
    #: ``None`` is unlimited.
    value: int | None
    #: The plan a customer would move to for more, or ``None`` when no rung
    #: above has more -- the screen then offers support instead.
    raise_to: str | None
    spec: LimitSpec

    @property
    def unlimited(self) -> bool:
        return self.value is None

    @property
    def raise_path(self) -> str:
        """Where "raise this" goes: the upgrade, or a support ticket."""
        return f"upgrade:{self.raise_to}" if self.raise_to else "support"

    def allows(self, count: int) -> bool:
        return self.value is None or count < self.value


async def limits_for_plan(
    session: AsyncSession, *, plan_code: str
) -> dict[str, int | None]:
    """Every cap on one plan, keyed. Registry order, seed for anything unsaved."""
    rows = (
        await session.execute(
            select(PlanLimitModel).where(PlanLimitModel.plan_code == plan_code)
        )
    ).scalars()
    stored = {row.key: row.value for row in rows if row.key in LIMITS_BY_KEY}
    fallback = SEED.get(plan_code, {})
    return {spec.key: stored.get(spec.key, fallback.get(spec.key)) for spec in LIMITS}


async def limits_for_plans(
    session: AsyncSession, *, plan_codes: Iterable[str]
) -> dict[str, dict[str, int | None]]:
    codes = list(plan_codes)
    rows = (
        await session.execute(
            select(PlanLimitModel).where(PlanLimitModel.plan_code.in_(codes))
        )
    ).scalars()
    stored: dict[str, dict[str, int | None]] = {code: {} for code in codes}
    for row in rows:
        if row.key in LIMITS_BY_KEY:
            stored.setdefault(row.plan_code, {})[row.key] = row.value
    out: dict[str, dict[str, int | None]] = {}
    for code in codes:
        fallback = SEED.get(code, {})
        out[code] = {
            spec.key: stored.get(code, {}).get(spec.key, fallback.get(spec.key))
            for spec in LIMITS
        }
    return out


def _more(a: int | None, b: int | None) -> bool:
    """Is ``a`` more generous than ``b``? Unlimited beats any number."""
    if a is None:
        return b is not None
    if b is None:
        return False
    return a > b


async def resolve(session: AsyncSession, *, plan_code: str, key: str) -> Limit:
    """The cap for ``key`` on ``plan_code`` and the next rung with more of it."""
    spec = LIMITS_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"{key!r} is not a plan limit; see plan_limits.LIMITS")
    ladder = [code for code in LADDER]
    table = await limits_for_plans(session, plan_codes=[*ladder, plan_code])
    value = table[plan_code][key]
    raise_to = None
    start = ladder.index(plan_code) + 1 if plan_code in ladder else 0
    for code in ladder[start:]:
        if _more(table[code][key], value):
            raise_to = code
            break
    return Limit(
        key=key, plan_code=plan_code, value=value, raise_to=raise_to, spec=spec
    )


async def limit_for_organization(
    session: AsyncSession, *, organization_id: int, key: str
) -> Limit:
    """The cap an account is under, from the plan it is on."""
    from api.services.billing import subscription_plans

    plan = await subscription_plans.plan_for_organization(
        session, organization_id=organization_id
    )
    return await resolve(session, plan_code=plan.code, key=key)


async def save(
    session: AsyncSession, *, plan_code: str, key: str, value: int | None
) -> PlanLimitModel:
    """Set one cap. ``None`` is unlimited; a negative number is a typo."""
    if key not in LIMITS_BY_KEY:
        raise LimitError(f"{key!r} is not a plan limit.")
    if value is not None and int(value) < 0:
        raise LimitError("A cap cannot be negative. Leave it empty for unlimited.")
    row = await session.scalar(
        select(PlanLimitModel).where(
            PlanLimitModel.plan_code == plan_code, PlanLimitModel.key == key
        )
    )
    if row is None:
        row = PlanLimitModel(plan_code=plan_code, key=key)
        session.add(row)
    row.value = int(value) if value is not None else None
    await session.flush()
    return row


async def ensure_seeded(
    session: AsyncSession, *, plan_codes: Iterable[str] | None = None
) -> int:
    """Write every seed row that does not exist yet. Returns how many landed.

    Never overwrites: an operator's figure outranks the spec's, and a seeder
    that reset a cap on every deploy would be a decision nobody made.
    """
    codes = list(plan_codes) if plan_codes is not None else list(SEED)
    existing = (
        await session.execute(
            select(PlanLimitModel.plan_code, PlanLimitModel.key).where(
                PlanLimitModel.plan_code.in_(codes)
            )
        )
    ).all()
    present = {(code, key) for code, key in existing}
    landed = 0
    for code in codes:
        for key, value in SEED.get(code, {}).items():
            if (code, key) in present or key not in LIMITS_BY_KEY:
                continue
            session.add(PlanLimitModel(plan_code=code, key=key, value=value))
            landed += 1
    if landed:
        await session.flush()
    return landed


def registry() -> list[dict[str, object]]:
    """The registry as a screen reads it."""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "unit": spec.unit,
            "decided": spec.decided,
            "note": spec.note,
        }
        for spec in LIMITS
    ]

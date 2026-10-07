"""Stop runaway cost (handoff 15 H: "operators can ... stop runaway cost").

Free beta means nothing is charged, so the credit ledger cannot see a
runaway: a looping routine or a stuck campaign spends our provider money and
debits nobody. This watches what the providers charge *us* over a rolling hour -- call receipts
(``call_cost_items.provider_cost_paise``) and direct model calls
(``model_usage``, priced on the rate card; this includes the AWS fallback
brain) -- for the whole
platform and per workspace, and engages a stop when a ceiling is crossed.

A stop refuses **new** billable work at the one gate every run already goes
through (``quota_service.authorize_workflow_run_start``) with error code
``cost_stopped``. Work in flight finishes: cutting a live call mid-sentence
is not how to save money. A stop stays until a person releases it, or until
the expiry the person who engaged it set -- an automatic stop never lifts
itself, because the condition that caused it has not been looked at.

**Interface for the controls stream.** Operational quotas (model turns,
voice minutes, messages, browser minutes per person per day) are being built
in ``controls``. When they land, their gate calls ``check(organization_id)``
too, and their counters can feed ``SpendSource``. Until then the hook is the
run-start gate and the source is call cost items.

State lives in Redis (one key for the platform, one per workspace) so the
check on the run-start path is one GET. If Redis cannot be read the check
**fails open** and logs: a cache outage must not become a platform outage,
and the evaluator records evidence of every stop in the database anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.services import features

FLAG = "cost_stop"
PLATFORM_KEY = "decibyl:cost_stop:platform"
ORG_KEY_PREFIX = "decibyl:cost_stop:org:"
WINDOW = timedelta(hours=1)
ERROR_CODE = "cost_stopped"
MESSAGE = (
    "New work is paused while we check unusual usage. Nothing you have "
    "already started is affected. We will be back shortly."
)


@dataclass(frozen=True)
class Stop:
    scope: str  # "platform" or "organization"
    organization_id: int | None
    reason: str
    engaged_at: str
    engaged_by: str  # "auto" or "user:<id>"
    expires_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "organization_id": self.organization_id,
            "reason": self.reason,
            "engaged_at": self.engaged_at,
            "engaged_by": self.engaged_by,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class Verdict:
    stopped: bool
    stop: Stop | None = None
    error_code: str = ""
    message: str = ""


class SpendSource(Protocol):
    async def platform_paise(self, session: AsyncSession, since: datetime) -> int: ...

    async def top_organizations(
        self, session: AsyncSession, since: datetime, limit: int
    ) -> list[tuple[int, int]]: ...


class CallCostSpendSource:
    """Provider cost from itemised call receipts, attributed to the workflow's
    workspace."""

    async def platform_paise(self, session: AsyncSession, since: datetime) -> int:
        from api.db.models import CallCostItemModel

        value = await session.scalar(
            select(
                func.coalesce(func.sum(CallCostItemModel.provider_cost_paise), 0)
            ).where(CallCostItemModel.created_at >= since)
        )
        return int(value or 0)

    async def top_organizations(
        self, session: AsyncSession, since: datetime, limit: int
    ) -> list[tuple[int, int]]:
        from api.db.models import CallCostItemModel, WorkflowModel, WorkflowRunModel

        spend = func.sum(CallCostItemModel.provider_cost_paise)
        rows = (
            await session.execute(
                select(WorkflowModel.organization_id, spend)
                .join(
                    WorkflowRunModel,
                    WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
                )
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .where(
                    CallCostItemModel.created_at >= since,
                    WorkflowModel.organization_id.isnot(None),
                )
                .group_by(WorkflowModel.organization_id)
                .order_by(spend.desc())
                .limit(limit)
            )
        ).all()
        return [(int(org), int(total or 0)) for org, total in rows]


class ModelUsageSpendSource:
    """Provider cost of model calls made outside a pipeline run: Decibyl's
    replies, the builder, Studio, routing, and the AWS fallback brain when
    it stands in for Claude (stream aws-gateway). Read from ``model_usage``
    and priced against the rate card exactly as the token report prices
    them (``billing/token_report.py``). A turn on a workspace's own key
    (feature ``...:byok``) cost the platform nothing and is not counted.
    """

    async def _by_organization(
        self, session: AsyncSession, since: datetime
    ) -> dict[int | None, int]:
        from api.db.models import ModelUsageModel
        from api.services.billing.rates import resolve_provider_rate
        from api.services.billing.token_report import (
            AUDIO_FEATURES,
            TOKEN_COMPONENTS,
            _cost_paise,
        )
        from api.services.billing.usage import llm_split_items

        rows = (
            await session.execute(
                select(
                    ModelUsageModel.organization_id,
                    ModelUsageModel.feature,
                    ModelUsageModel.provider,
                    ModelUsageModel.model,
                    ModelUsageModel.prompt_tokens,
                    ModelUsageModel.completion_tokens,
                    ModelUsageModel.cache_read_input_tokens,
                    ModelUsageModel.cache_creation_input_tokens,
                ).where(
                    ModelUsageModel.created_at >= since,
                    ModelUsageModel.audio_seconds == 0,
                    ModelUsageModel.quantity == 0,
                    ~ModelUsageModel.feature.in_(AUDIO_FEATURES),
                )
            )
        ).all()
        rates: dict[tuple[str, str, str], Any] = {}
        spent: dict[int | None, float] = {}
        for org, feature, provider, model, prompt, completion, read, write in rows:
            if str(feature or "").endswith(":byok"):
                continue
            items = llm_split_items(
                {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": write,
                },
                provider=provider,
                model=model,
            )
            for item in items:
                component = getattr(item.component, "value", item.component)
                if component not in TOKEN_COMPONENTS:
                    continue
                rate = None
                for m in (model or "", ""):
                    key = (component, provider, m)
                    if key not in rates:
                        found = await resolve_provider_rate(
                            session,
                            provider=provider,
                            component=component,
                            at=datetime.now(UTC),
                            model=m,
                        )
                        rates[key] = (
                            found
                            if found is not None and (m == "" or found.model == m)
                            else None
                        )
                    rate = rates[key]
                    if rate is not None:
                        break
                if rate is not None:
                    spent[org] = spent.get(org, 0.0) + _cost_paise(item.quantity, rate)
        return {org: int(round(paise)) for org, paise in spent.items()}

    async def platform_paise(self, session: AsyncSession, since: datetime) -> int:
        return sum((await self._by_organization(session, since)).values())

    async def top_organizations(
        self, session: AsyncSession, since: datetime, limit: int
    ) -> list[tuple[int, int]]:
        by_org = await self._by_organization(session, since)
        ranked = sorted(
            ((org, paise) for org, paise in by_org.items() if org is not None),
            key=lambda pair: -pair[1],
        )
        return ranked[:limit]


class CombinedSpendSource:
    """Every provider cost the platform pays: call receipts and direct model
    calls together, so a runaway in either is seen."""

    def __init__(self, *sources: SpendSource):
        self.sources = sources or (CallCostSpendSource(), ModelUsageSpendSource())

    async def platform_paise(self, session: AsyncSession, since: datetime) -> int:
        total = 0
        for source in self.sources:
            total += await source.platform_paise(session, since)
        return total

    async def top_organizations(
        self, session: AsyncSession, since: datetime, limit: int
    ) -> list[tuple[int, int]]:
        summed: dict[int, int] = {}
        for source in self.sources:
            for org, paise in await source.top_organizations(session, since, limit):
                summed[org] = summed.get(org, 0) + paise
        return sorted(summed.items(), key=lambda pair: -pair[1])[:limit]


def _redis():
    import redis.asyncio as aioredis

    return aioredis.from_url(constants.REDIS_URL)


def _decode(raw: Any) -> Stop | None:
    if not raw:
        return None
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        stop = Stop(**data)
    except (ValueError, TypeError):
        return None
    if stop.expires_at:
        try:
            if datetime.fromisoformat(stop.expires_at) <= datetime.now(UTC):
                return None
        except ValueError:
            return None
    return stop


def configured() -> bool:
    return bool(
        constants.COST_STOP_PLATFORM_HOURLY_PAISE
        or constants.COST_STOP_ORG_HOURLY_PAISE
    )


async def check(organization_id: int | None, *, client=None) -> Verdict:
    """Whether new billable work may start for this workspace. One Redis
    round trip; fails open. Off while the ``cost_stop`` flag is off."""
    if not features.is_on(FLAG, organization_id):
        return Verdict(stopped=False)
    own = client is None
    client = client or _redis()
    try:
        keys = [PLATFORM_KEY]
        if organization_id is not None:
            keys.append(f"{ORG_KEY_PREFIX}{organization_id}")
        values = await client.mget(keys)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        logger.warning(
            "Cost stop state unreadable; allowing work: {}", type(exc).__name__
        )
        return Verdict(stopped=False)
    finally:
        if own:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
    for raw in values:
        stop = _decode(raw)
        if stop is not None:
            return Verdict(
                stopped=True, stop=stop, error_code=ERROR_CODE, message=MESSAGE
            )
    return Verdict(stopped=False)


async def engage(
    *,
    scope: str,
    reason: str,
    engaged_by: str,
    organization_id: int | None = None,
    expires_at: datetime | None = None,
    client=None,
) -> Stop:
    if scope not in ("platform", "organization"):
        raise ValueError("scope is platform or organization")
    if scope == "organization" and organization_id is None:
        raise ValueError("an organization stop needs organization_id")
    stop = Stop(
        scope=scope,
        organization_id=organization_id if scope == "organization" else None,
        reason=reason[:300],
        engaged_at=datetime.now(UTC).isoformat(),
        engaged_by=engaged_by,
        expires_at=expires_at.astimezone(UTC).isoformat() if expires_at else None,
    )
    key = PLATFORM_KEY if scope == "platform" else f"{ORG_KEY_PREFIX}{organization_id}"
    own = client is None
    client = client or _redis()
    try:
        ttl = None
        if expires_at is not None:
            ttl = max(int((expires_at - datetime.now(UTC)).total_seconds()), 1)
        await client.set(key, json.dumps(stop.as_dict()), ex=ttl)
    finally:
        if own:
            await client.aclose()
    logger.error(
        "Cost stop engaged ({}{}) by {}: {}",
        scope,
        f" {organization_id}" if organization_id is not None else "",
        engaged_by,
        stop.reason,
    )
    return stop


async def release(
    *, scope: str, organization_id: int | None = None, client=None
) -> bool:
    key = PLATFORM_KEY if scope == "platform" else f"{ORG_KEY_PREFIX}{organization_id}"
    own = client is None
    client = client or _redis()
    try:
        removed = await client.delete(key)
    finally:
        if own:
            await client.aclose()
    return bool(removed)


async def engaged(*, client=None) -> dict[str, Any]:
    own = client is None
    client = client or _redis()
    try:
        platform = _decode(await client.get(PLATFORM_KEY))
        orgs: list[dict[str, Any]] = []
        async for key in client.scan_iter(match=f"{ORG_KEY_PREFIX}*"):
            stop = _decode(await client.get(key))
            if stop is not None:
                orgs.append(stop.as_dict())
    finally:
        if own:
            await client.aclose()
    return {"platform": platform.as_dict() if platform else None, "organizations": orgs}


async def status(*, client=None) -> dict[str, Any]:
    """For the console. Never raises: unreadable state is reported as such."""
    base = {
        "enabled": features.is_on(FLAG),
        "configured": configured(),
        "platform_hourly_paise": constants.COST_STOP_PLATFORM_HOURLY_PAISE or None,
        "organization_hourly_paise": constants.COST_STOP_ORG_HOURLY_PAISE or None,
    }
    try:
        return {**base, **(await engaged(client=client)), "readable": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cost stop status unreadable: {}", type(exc).__name__)
        return {**base, "platform": None, "organizations": [], "readable": False}


async def evaluate(
    session: AsyncSession,
    *,
    source: SpendSource | None = None,
    now: datetime | None = None,
    client=None,
) -> dict[str, Any]:
    """Compare the last hour's provider spend with the ceilings and engage
    stops where crossed. Returns what it saw and did. Run by an ARQ cron."""
    if not features.is_on(FLAG):
        return {"skipped": "off"}
    if not configured():
        return {"skipped": "not_configured"}
    source = source or CombinedSpendSource()
    already = await engaged(client=client)
    stopped_orgs = {o["organization_id"] for o in already["organizations"]}
    since = (now or datetime.now(UTC)) - WINDOW
    result: dict[str, Any] = {"since": since.isoformat(), "engaged": []}

    platform_limit = constants.COST_STOP_PLATFORM_HOURLY_PAISE
    if platform_limit:
        spent = await source.platform_paise(session, since)
        result["platform_paise"] = spent
        if spent > platform_limit and already["platform"] is None:
            stop = await engage(
                scope="platform",
                reason=f"Platform provider spend {spent} paise in the last hour, ceiling {platform_limit}.",
                engaged_by="auto",
                client=client,
            )
            result["engaged"].append(stop.as_dict())
            await _announce(session, stop)

    org_limit = constants.COST_STOP_ORG_HOURLY_PAISE
    if org_limit:
        top = await source.top_organizations(session, since, limit=20)
        result["top_organizations"] = [
            {"organization_id": o, "paise": p} for o, p in top
        ]
        for organization_id, spent in top:
            if spent <= org_limit or organization_id in stopped_orgs:
                continue
            stop = await engage(
                scope="organization",
                organization_id=organization_id,
                reason=f"Workspace provider spend {spent} paise in the last hour, ceiling {org_limit}.",
                engaged_by="auto",
                client=client,
            )
            result["engaged"].append(stop.as_dict())
            await _announce(session, stop)
    await session.commit()
    return result


async def _announce(session: AsyncSession, stop: Stop) -> None:
    from api.services.ops import telemetry

    await telemetry.record(
        session,
        "cost_stop_engaged",
        workspace_id=stop.organization_id,
        properties={"scope": stop.scope, "reason_code": "cost_stopped"},
    )

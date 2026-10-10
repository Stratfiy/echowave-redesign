"""What the platform paid its providers in a window, from what billing metered.

Read-only, and only from the tables ``services/billing`` writes. Nothing here
holds a price: a cost is either one billing stored beside the usage, or a
rate on the rate book (``rates.resolve_provider_rate``) applied the way the
token report applies it. **Usage with neither is reported as usage with no
rate, never as free.**

Sources, each in its own native unit:

* ``call_cost_items`` -- every run's receipt (calls, text replies, routines),
  one line per component, ``provider_cost_paise`` being what the vendor
  charged us. A line with units, a zero rate and a zero cost is the
  settlement's "no rate on file" (``uncosted_alert``), counted as unpriced.
  Decibyl's own fee lines -- ``platform``, ``addon``, ``orchestration`` -- are
  revenue, not spend, and are left out by name: a blocklist, so a component
  billing adds tomorrow shows up here rather than vanishing.
* ``model_usage`` -- model calls outside a run. Token rows are priced on the
  rate book; transcription seconds and other units carry no rate here and
  are shown as usage. Rows on a workspace's own key (``...:byok``) cost us
  nothing and are not spend.
* ``embedding_ingestion_costs``, ``data_lookup_costs``, ``generated_images``
  (platform key only) and number rental periods -- each with the vendor cost
  stored on the row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.image_models import GeneratedImageModel
from api.db.models import (
    CallCostItemModel,
    DataLookupCostModel,
    EmbeddingIngestionCostModel,
    ModelUsageModel,
    OrganizationModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent
from api.services.billing.rollup import ist_day_bounds_utc
from api.services.billing.token_report import AUDIO_FEATURES

#: Receipt lines that are Decibyl's revenue rather than a provider's cost.
REVENUE_COMPONENTS = frozenset(
    {
        CostComponent.PLATFORM.value,
        CostComponent.ADDON.value,
        CostComponent.ORCHESTRATION.value,
    }
)

#: What a line is called in the summary, and the unit its usage is in. A key
#: with no entry is shown under its own name in "units" -- never dropped.
LABELS: dict[str, tuple[str, str]] = {
    CostComponent.LLM.value: ("Language model (blended)", "tokens"),
    CostComponent.LLM_INPUT.value: ("Language model input", "tokens"),
    CostComponent.LLM_CACHED.value: ("Language model cached input", "tokens"),
    CostComponent.LLM_CACHE_WRITE.value: ("Language model cache writes", "tokens"),
    CostComponent.LLM_OUTPUT.value: ("Language model output", "tokens"),
    CostComponent.STT.value: ("Speech to text", "seconds"),
    CostComponent.TTS.value: ("Text to speech", "characters"),
    CostComponent.TELEPHONY.value: ("Telephony", "seconds"),
    CostComponent.EMBEDDING.value: ("Embeddings", "tokens"),
    CostComponent.DATA.value: ("Data lookups", "requests"),
    CostComponent.IMAGE.value: ("Image generation", "images"),
    "numbers": ("Phone number rental", "number-months"),
    "transcription": ("Recording transcription", "seconds"),
}


@dataclass
class Line:
    key: str
    label: str
    unit: str
    units: float = 0.0
    cost_paise: float = 0.0
    #: Units billing metered with no rate to price them.
    unpriced_units: float = 0.0

    def add(self, *, units: float, cost: float, unpriced: float = 0.0) -> None:
        self.units += float(units or 0)
        self.cost_paise += float(cost or 0)
        self.unpriced_units += float(unpriced or 0)

    @property
    def no_rate(self) -> bool:
        return self.unpriced_units > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "units": round(self.units, 2),
            "cost_paise": int(round(self.cost_paise)),
            "unpriced_units": round(self.unpriced_units, 2),
            "no_rate": self.no_rate,
        }


@dataclass
class Metered:
    start: datetime
    end: datetime
    lines: dict[str, Line] = field(default_factory=dict)
    #: Provider cost by workspace; None is usage nobody was attributed.
    by_org: dict[int | None, float] = field(default_factory=dict)
    #: Provider cost by agent (workflow), where the source names one.
    by_agent: dict[int, float] = field(default_factory=dict)

    def line(self, key: str, label: str | None = None, unit: str | None = None) -> Line:
        if key not in self.lines:
            default_label, default_unit = LABELS.get(
                key, (key.replace("_", " ").capitalize(), "units")
            )
            self.lines[key] = Line(
                key=key, label=label or default_label, unit=unit or default_unit
            )
        return self.lines[key]

    def charge(self, *, org: int | None, agent: int | None, cost: float) -> None:
        if not cost:
            return
        self.by_org[org] = self.by_org.get(org, 0.0) + float(cost)
        if agent is not None:
            self.by_agent[agent] = self.by_agent.get(agent, 0.0) + float(cost)

    @property
    def total_paise(self) -> int:
        return int(round(sum(line.cost_paise for line in self.lines.values())))


def ist_day(at: datetime) -> date:
    from api.services.billing.rollup import IST

    return at.astimezone(IST).date()


def day_window(day: date) -> tuple[datetime, datetime]:
    return ist_day_bounds_utc(day)


async def _run_receipts(session: AsyncSession, out: Metered) -> None:
    unpriced = func.sum(
        case(
            (
                (CallCostItemModel.unit_rate_mpaise == 0)
                & (CallCostItemModel.provider_cost_paise == 0),
                CallCostItemModel.units,
            ),
            else_=0,
        )
    )
    rows = (
        await session.execute(
            select(
                CallCostItemModel.component,
                WorkflowModel.organization_id,
                WorkflowRunModel.workflow_id,
                func.coalesce(func.sum(CallCostItemModel.units), 0),
                func.coalesce(func.sum(CallCostItemModel.provider_cost_paise), 0),
                func.coalesce(unpriced, 0),
            )
            .join(
                WorkflowRunModel,
                WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
            )
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(
                CallCostItemModel.created_at >= out.start,
                CallCostItemModel.created_at < out.end,
                CallCostItemModel.component.notin_(REVENUE_COMPONENTS),
            )
            .group_by(
                CallCostItemModel.component,
                WorkflowModel.organization_id,
                WorkflowRunModel.workflow_id,
            )
        )
    ).all()
    for component, org, agent, units, cost, missing in rows:
        out.line(str(component)).add(units=units, cost=cost, unpriced=missing)
        out.charge(org=org, agent=agent, cost=cost)


async def _direct_model_calls(session: AsyncSession, out: Metered) -> None:
    from api.services.billing.rates import resolve_provider_rate
    from api.services.billing.token_report import _cost_paise
    from api.services.billing.usage import llm_split_items

    # A row is tokens, audio or other units, never a mix; grouping by which
    # keeps a transcription row from being summed into a token row.
    shape = case(
        (ModelUsageModel.audio_seconds > 0, "audio"),
        (ModelUsageModel.quantity > 0, "units"),
        else_="tokens",
    )
    rows = (
        await session.execute(
            select(
                ModelUsageModel.organization_id,
                ModelUsageModel.feature,
                ModelUsageModel.provider,
                ModelUsageModel.model,
                func.sum(ModelUsageModel.prompt_tokens),
                func.sum(ModelUsageModel.completion_tokens),
                func.sum(ModelUsageModel.cache_read_input_tokens),
                func.sum(ModelUsageModel.cache_creation_input_tokens),
                func.sum(ModelUsageModel.audio_seconds),
                func.sum(ModelUsageModel.quantity),
                ModelUsageModel.unit,
                shape,
            )
            .where(
                ModelUsageModel.created_at >= out.start,
                ModelUsageModel.created_at < out.end,
            )
            .group_by(
                ModelUsageModel.organization_id,
                ModelUsageModel.feature,
                ModelUsageModel.provider,
                ModelUsageModel.model,
                ModelUsageModel.unit,
                shape,
            )
        )
    ).all()
    at = min(out.end, datetime.now(UTC)) - timedelta(seconds=1)
    rates: dict[tuple[str, str, str], Any] = {}
    for (
        org,
        feature,
        provider,
        model,
        prompt,
        completion,
        read,
        write,
        seconds,
        quantity,
        unit,
        kind,
    ) in rows:
        if str(feature or "").endswith(":byok"):
            continue  # the workspace's own key: their bill, not ours
        if kind == "audio" or feature in AUDIO_FEATURES:
            out.line("transcription").add(
                units=float(seconds or 0), cost=0, unpriced=float(seconds or 0)
            )
            continue
        if kind == "units":
            name = str(unit or "units")
            out.line(
                f"usage:{feature}:{name}",
                label=f"{str(feature).replace('_', ' ')} ({name})",
                unit=name,
            ).add(units=float(quantity), cost=0, unpriced=float(quantity))
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
            component = str(getattr(item.component, "value", item.component))
            key = (component, provider, model or "")
            if key not in rates:
                rates[key] = await resolve_provider_rate(
                    session,
                    provider=provider,
                    component=component,
                    at=at,
                    model=model or "",
                )
            rate = rates[key]
            if rate is None:
                out.line(component).add(
                    units=item.quantity, cost=0, unpriced=item.quantity
                )
                continue
            cost = _cost_paise(item.quantity, rate)
            out.line(component).add(units=item.quantity, cost=cost)
            out.charge(org=org, agent=None, cost=cost)


async def _stored_costs(session: AsyncSession, out: Metered) -> None:
    # Knowledge documents embedded on upload.
    for org, tokens, cost in (
        await session.execute(
            select(
                EmbeddingIngestionCostModel.organization_id,
                func.coalesce(func.sum(EmbeddingIngestionCostModel.tokens), 0),
                func.coalesce(
                    func.sum(EmbeddingIngestionCostModel.vendor_cost_paise), 0
                ),
            )
            .where(
                EmbeddingIngestionCostModel.created_at >= out.start,
                EmbeddingIngestionCostModel.created_at < out.end,
            )
            .group_by(EmbeddingIngestionCostModel.organization_id)
        )
    ).all():
        out.line(CostComponent.EMBEDDING.value).add(
            units=tokens, cost=cost, unpriced=tokens if not cost else 0
        )
        out.charge(org=org, agent=None, cost=cost)

    # Searches and lookups bought outside a run.
    for org, agent, requests, cost in (
        await session.execute(
            select(
                DataLookupCostModel.organization_id,
                DataLookupCostModel.workflow_id,
                func.coalesce(func.sum(DataLookupCostModel.requests), 0),
                func.coalesce(func.sum(DataLookupCostModel.vendor_cost_paise), 0),
            )
            .where(
                DataLookupCostModel.created_at >= out.start,
                DataLookupCostModel.created_at < out.end,
            )
            .group_by(
                DataLookupCostModel.organization_id, DataLookupCostModel.workflow_id
            )
        )
    ).all():
        out.line(CostComponent.DATA.value).add(
            units=requests, cost=cost, unpriced=requests if not cost else 0
        )
        out.charge(org=org, agent=agent, cost=cost)

    # Images made on a platform key. One on the workspace's key is theirs.
    no_cost = case((GeneratedImageModel.cost_source == "none", 1), else_=0)
    for org, agent, images, cost, unpriced in (
        await session.execute(
            select(
                GeneratedImageModel.organization_id,
                GeneratedImageModel.workflow_id,
                func.count(GeneratedImageModel.id),
                func.coalesce(func.sum(GeneratedImageModel.vendor_cost_paise), 0),
                func.coalesce(func.sum(no_cost), 0),
            )
            .where(
                GeneratedImageModel.created_at >= out.start,
                GeneratedImageModel.created_at < out.end,
                GeneratedImageModel.kind == "generated",
                GeneratedImageModel.key_source != "byok",
            )
            .group_by(
                GeneratedImageModel.organization_id, GeneratedImageModel.workflow_id
            )
        )
    ).all():
        out.line(CostComponent.IMAGE.value).add(
            units=images, cost=cost, unpriced=unpriced
        )
        out.charge(org=org, agent=agent, cost=cost)

    # Number rental periods charged in the window, at the carrier's cost.
    for org, charge_type, periods, cost in (
        await session.execute(
            select(
                RecurringChargePeriodModel.organization_id,
                RecurringChargeModel.charge_type,
                func.count(RecurringChargePeriodModel.id),
                func.coalesce(func.sum(RecurringChargePeriodModel.cost_paise), 0),
            )
            .join(
                RecurringChargeModel,
                RecurringChargeModel.id
                == RecurringChargePeriodModel.recurring_charge_id,
            )
            .where(
                RecurringChargePeriodModel.charged_at >= out.start,
                RecurringChargePeriodModel.charged_at < out.end,
            )
            .group_by(
                RecurringChargePeriodModel.organization_id,
                RecurringChargeModel.charge_type,
            )
        )
    ).all():
        key = (
            "numbers" if charge_type == "number_rental" else f"recurring:{charge_type}"
        )
        out.line(key).add(units=periods, cost=cost, unpriced=periods if not cost else 0)
        out.charge(org=org, agent=None, cost=cost)


async def meter(session: AsyncSession, start: datetime, end: datetime) -> Metered:
    """Provider spend in ``[start, end)``, by component, workspace and agent."""
    out = Metered(start=start, end=end)
    await _run_receipts(session, out)
    await _direct_model_calls(session, out)
    await _stored_costs(session, out)
    return out


async def names(
    session: AsyncSession, *, orgs: list[int], agents: list[int]
) -> tuple[dict[int, str], dict[int, tuple[str, int | None]]]:
    """Display names for workspaces, and for agents with their workspace."""
    org_names: dict[int, str] = {}
    if orgs:
        for oid, billing, provider_id in (
            await session.execute(
                select(
                    OrganizationModel.id,
                    OrganizationModel.billing_name,
                    OrganizationModel.provider_id,
                ).where(OrganizationModel.id.in_(orgs))
            )
        ).all():
            org_names[int(oid)] = billing or provider_id or f"Workspace {oid}"
    agent_names: dict[int, tuple[str, int | None]] = {}
    if agents:
        for wid, name, org in (
            await session.execute(
                select(
                    WorkflowModel.id, WorkflowModel.name, WorkflowModel.organization_id
                ).where(WorkflowModel.id.in_(agents))
            )
        ).all():
            agent_names[int(wid)] = (name or f"Agent {wid}", org)
    return org_names, agent_names

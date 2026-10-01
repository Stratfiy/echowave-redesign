"""How many tokens we use, vendor by vendor, and on what.

The question this answers comes before the one about how credits charge for
models: *what does a reply, a routine, a call, a Decibyl turn actually use?*
Nothing here charges anyone or writes anything.

Two sources, because model calls reach us through two doors:

* **Runs** -- calls, text replies, routines, triggers, tasks -- write their
  tokens onto the run's receipt (``call_cost_items``), split by the metering
  work of 21 September into input, cached input, cache write and output, each
  priced at what the vendor charges us (``provider_cost_paise``).
* **Direct calls** -- Decibyl's assistant, the builder, trigger compiling,
  document fields, the acceptable-use check, the graph reviews -- go through
  the builder client and land in ``model_usage`` as the vendor reported them.
  They carry no price, so they are split with the same vendor rule
  (``usage.llm_split_items``) and priced against the rate book here. A model
  with no rate on file is reported as unpriced, never as free.

**Known not metered**, listed on the report rather than left for somebody to
notice: the channel-context fold logs its tokens and writes them nowhere.
Embeddings are metered in their own table and are not tokens a reply spends,
so they are out of this report by design.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    CallCostItemModel,
    ModelUsageModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent
from api.services.billing.money import MPAISE_PER_PAISE
from api.services.billing.usage import llm_split_items

#: The four lines a model call is priced in, in report order.
TOKEN_COMPONENTS: tuple[str, ...] = (
    CostComponent.LLM_INPUT.value,
    CostComponent.LLM_CACHED.value,
    CostComponent.LLM_CACHE_WRITE.value,
    CostComponent.LLM_OUTPUT.value,
)

#: Paths known to spend tokens without writing them anywhere. Named so the
#: report says so; removing an entry is how one gets fixed.
NOT_METERED: tuple[str, ...] = (
    "channel context fold (services/workflow/channel_context.py): logged, not stored",
)

_LABEL = {
    CostComponent.LLM_INPUT.value: "input",
    CostComponent.LLM_CACHED.value: "cached",
    CostComponent.LLM_CACHE_WRITE.value: "cache_write",
    CostComponent.LLM_OUTPUT.value: "output",
}

#: What a run's ``mode`` means as a kind of work.
_VOICE_MODES = frozenset(
    {
        "plivo",
        "twilio",
        "vonage",
        "telnyx",
        "vobiz",
        "cloudonix",
        "ari",
        "smallwebrtc",
        "webrtc",
        "stasis",
    }
)


#: Features whose model_usage rows are transcription (seconds, not tokens).
AUDIO_FEATURES = ("recording_transcription", "dialer_import")


def work_kind(mode: str | None) -> str:
    mode = (mode or "").lower()
    if mode in ("textchat", "text_chat"):
        return "text"
    if mode in _VOICE_MODES:
        return "voice"
    return mode or "other"


@dataclass
class ModelLine:
    source: str  # "run" or "direct"
    provider: str
    model: str
    tokens: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_LABEL.values(), 0)
    )
    vendor_cost_paise: float = 0.0
    calls: int = 0
    unpriced: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())

    @property
    def cached_share(self) -> float:
        prompt = (
            self.tokens["input"] + self.tokens["cached"] + self.tokens["cache_write"]
        )
        return round(self.tokens["cached"] / prompt, 4) if prompt else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            **{f"{k}_tokens": v for k, v in self.tokens.items()},
            "total_tokens": self.total_tokens,
            "cached_share": self.cached_share,
            "vendor_cost_paise": round(self.vendor_cost_paise, 2),
            "unpriced": self.unpriced,
        }


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "median": 0, "p90": 0}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return {
        "count": len(values),
        "median": round(statistics.median(ordered), 2),
        "p90": round(p90, 2),
    }


def _cost_paise(units: int, rate: Any) -> float:
    """Tokens at a per-1k rate in millipaise, in paise."""
    return units / 1000 * rate.rate_mpaise / MPAISE_PER_PAISE


def summarise(
    *,
    run_rows: Iterable[tuple[int, str | None, str, str, str, int, int]],
    direct_rows: Iterable[tuple[str, str, str, int, int, int, int]],
    rates: dict[tuple[str, str, str], Any],
) -> dict[str, Any]:
    """Pure: the report from rows already read.

    ``run_rows``: ``(run_id, mode, provider, model, component, units,
    provider_cost_paise)``, one per receipt line.
    ``direct_rows``: ``(feature, provider, model, prompt_tokens,
    completion_tokens, cache_read_input_tokens, cache_creation_input_tokens)``,
    one per call.
    ``rates``: ``(component, provider, model) -> resolved rate`` for direct
    calls, the exact model first and ``""`` for the provider fallback.
    """
    lines: dict[tuple[str, str, str], ModelLine] = {}
    run_models: dict[tuple[str, str], set[int]] = defaultdict(set)
    per_run_tokens: dict[int, int] = defaultdict(int)
    per_run_cost: dict[int, float] = defaultdict(float)
    run_kind: dict[int, str] = {}

    for run_id, mode, provider, model, component, units, cost in run_rows:
        if component not in _LABEL:
            continue
        key = ("run", provider or "", model or "")
        line = lines.setdefault(key, ModelLine("run", key[1], key[2]))
        line.tokens[_LABEL[component]] += int(units or 0)
        line.vendor_cost_paise += float(cost or 0)
        run_models[(key[1], key[2])].add(run_id)
        per_run_tokens[run_id] += int(units or 0)
        per_run_cost[run_id] += float(cost or 0)
        run_kind[run_id] = work_kind(mode)
    for (provider, model), runs in run_models.items():
        lines[("run", provider, model)].calls = len(runs)

    per_feature_tokens: dict[str, list[float]] = defaultdict(list)
    per_feature_cost: dict[str, list[float]] = defaultdict(list)
    unattributed = 0
    direct_total = 0
    for (
        feature,
        provider,
        model,
        prompt,
        completion,
        cache_read,
        cache_write,
    ) in direct_rows:
        direct_total += 1
        if feature == "unattributed":
            unattributed += 1
        key = ("direct", provider or "", model or "")
        line = lines.setdefault(key, ModelLine("direct", key[1], key[2]))
        line.calls += 1
        items = llm_split_items(
            {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
            provider=provider,
            model=model,
        )
        call_tokens, call_cost = 0, 0.0
        # A turn on the account's own key (BYOK-1, feature "...:byok") used
        # tokens but cost the platform nothing: counted, never priced.
        own_key = str(feature or "").endswith(":byok")
        for item in items:
            component = getattr(item.component, "value", item.component)
            line.tokens[_LABEL[component]] += item.quantity
            call_tokens += item.quantity
            if own_key:
                continue
            rate = rates.get((component, provider, model)) or rates.get(
                (component, provider, "")
            )
            if rate is None:
                if component not in line.unpriced:
                    line.unpriced.append(component)
                continue
            cost = _cost_paise(item.quantity, rate)
            line.vendor_cost_paise += cost
            call_cost += cost
        per_feature_tokens[feature].append(call_tokens)
        per_feature_cost[feature].append(call_cost)

    by_kind_tokens: dict[str, list[float]] = defaultdict(list)
    by_kind_cost: dict[str, list[float]] = defaultdict(list)
    for run_id, kind in run_kind.items():
        by_kind_tokens[kind].append(per_run_tokens[run_id])
        by_kind_cost[kind].append(per_run_cost[run_id])

    work = [
        {
            "source": "run",
            "work": kind,
            "tokens_per_run": _spread(by_kind_tokens[kind]),
            "vendor_paise_per_run": _spread(by_kind_cost[kind]),
        }
        for kind in sorted(by_kind_tokens)
    ] + [
        {
            "source": "direct",
            "work": feature,
            "tokens_per_call": _spread(per_feature_tokens[feature]),
            "vendor_paise_per_call": _spread(per_feature_cost[feature]),
        }
        for feature in sorted(per_feature_tokens)
    ]

    ordered = sorted(
        lines.values(), key=lambda l: (-l.vendor_cost_paise, -l.total_tokens)
    )
    return {
        "by_model": [line.as_dict() for line in ordered],
        "by_work": work,
        "totals": {
            "vendor_cost_paise": round(sum(l.vendor_cost_paise for l in ordered), 2),
            "tokens": sum(l.total_tokens for l in ordered),
            "direct_calls": direct_total,
            "unattributed_calls": unattributed,
        },
        "not_metered": list(NOT_METERED),
    }


async def build(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Read both sources for ``[start, end)`` and summarise."""
    from api.services.billing.rates import resolve_provider_rate

    run_q = (
        select(
            CallCostItemModel.workflow_run_id,
            WorkflowRunModel.mode,
            CallCostItemModel.provider,
            CallCostItemModel.model,
            CallCostItemModel.component,
            CallCostItemModel.units,
            CallCostItemModel.provider_cost_paise,
        )
        .join(
            WorkflowRunModel, WorkflowRunModel.id == CallCostItemModel.workflow_run_id
        )
        .where(
            WorkflowRunModel.created_at >= start,
            WorkflowRunModel.created_at < end,
            CallCostItemModel.component.in_(TOKEN_COMPONENTS),
        )
    )
    direct_q = select(
        ModelUsageModel.feature,
        ModelUsageModel.provider,
        ModelUsageModel.model,
        ModelUsageModel.prompt_tokens,
        ModelUsageModel.completion_tokens,
        ModelUsageModel.cache_read_input_tokens,
        ModelUsageModel.cache_creation_input_tokens,
    ).where(
        ModelUsageModel.created_at >= start,
        ModelUsageModel.created_at < end,
        # A transcription row carries seconds, not tokens; it is reported
        # below on its own, never as a token call with nothing in it.
        ModelUsageModel.audio_seconds == 0,
        ModelUsageModel.quantity == 0,
        ~ModelUsageModel.feature.in_(AUDIO_FEATURES),
    )
    units_q = (
        select(
            ModelUsageModel.feature,
            ModelUsageModel.provider,
            ModelUsageModel.model,
            ModelUsageModel.unit,
            func.count(ModelUsageModel.id),
            func.coalesce(func.sum(ModelUsageModel.quantity), 0),
        )
        .where(
            ModelUsageModel.created_at >= start,
            ModelUsageModel.created_at < end,
            ModelUsageModel.quantity > 0,
        )
        .group_by(
            ModelUsageModel.feature,
            ModelUsageModel.provider,
            ModelUsageModel.model,
            ModelUsageModel.unit,
        )
    )
    audio_q = (
        select(
            ModelUsageModel.feature,
            ModelUsageModel.provider,
            ModelUsageModel.model,
            func.count(ModelUsageModel.id),
            func.coalesce(func.sum(ModelUsageModel.audio_seconds), 0),
            func.count(ModelUsageModel.id).filter(ModelUsageModel.audio_seconds == 0),
        )
        .where(
            ModelUsageModel.created_at >= start,
            ModelUsageModel.created_at < end,
            (ModelUsageModel.audio_seconds > 0)
            | ModelUsageModel.feature.in_(AUDIO_FEATURES),
        )
        .group_by(
            ModelUsageModel.feature, ModelUsageModel.provider, ModelUsageModel.model
        )
    )
    if organization_id is not None:
        # A run has no organisation of its own; its workflow does.
        run_q = run_q.join(
            WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id
        ).where(WorkflowModel.organization_id == organization_id)
        direct_q = direct_q.where(ModelUsageModel.organization_id == organization_id)
        audio_q = audio_q.where(ModelUsageModel.organization_id == organization_id)
        units_q = units_q.where(ModelUsageModel.organization_id == organization_id)

    run_rows = (await session.execute(run_q)).all()
    direct_rows = (await session.execute(direct_q)).all()

    rates: dict[tuple[str, str, str], Any] = {}
    for provider, model in {(r[1], r[2]) for r in direct_rows}:
        for component in TOKEN_COMPONENTS:
            for m in (model, ""):
                if (component, provider, m) in rates:
                    continue
                rate = await resolve_provider_rate(
                    session, provider=provider, component=component, at=end, model=m
                )
                if rate is not None and (m == "" or rate.model == m):
                    rates[(component, provider, m)] = rate

    report = summarise(run_rows=run_rows, direct_rows=direct_rows, rates=rates)
    report["audio"] = [
        {
            "feature": feature,
            "provider": provider,
            "model": model,
            "calls": int(calls),
            "audio_seconds": round(float(seconds or 0), 1),
            "calls_without_length": int(unknown),
        }
        for feature, provider, model, calls, seconds, unknown in (
            await session.execute(audio_q)
        ).all()
    ]
    # Vendor units that are not tokens: tool calls, characters, messages.
    # Counted here; priced by the costing report against each vendor's unit.
    report["units"] = [
        {
            "feature": feature,
            "provider": provider,
            "model": model,
            "unit": unit,
            "rows": int(rows),
            "quantity": round(float(quantity or 0), 1),
        }
        for feature, provider, model, unit, rows, quantity in (
            await session.execute(units_q)
        ).all()
    ]
    report["window"] = {"start": start.isoformat(), "end": end.isoformat()}
    report["organization_id"] = organization_id
    return report


def as_csv_rows(report: dict[str, Any]) -> list[list[Any]]:
    """The by-model table as rows, header first."""
    header = [
        "source",
        "provider",
        "model",
        "calls",
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
        "total_tokens",
        "cached_share",
        "vendor_cost_paise",
        "unpriced",
    ]
    rows: list[list[Any]] = [header]
    for line in report["by_model"]:
        rows.append([line[h] if h != "unpriced" else ";".join(line[h]) for h in header])
    return rows


__all__ = ["NOT_METERED", "as_csv_rows", "build", "summarise", "work_kind"]

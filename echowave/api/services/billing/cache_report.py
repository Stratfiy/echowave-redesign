"""Is the prompt cache working, and what does a finished task cost?

Read from ``llm_call_usage`` (``billing.cache_metrics``): one row per model
call, through either door, with its usage in one shape and the hashes of the
prompt it sent. Staff-only; it measures and charges nothing.

How to read each number -- see ``docs/plans/caching-and-compression.md``:

* **Hit rate** is cache-read tokens over *cacheable* input: the whole prompt
  of every call long enough for its vendor to cache at all
  (``cache_capabilities.min_cacheable_tokens``; a vendor whose minimum is not
  known counts every call). The raw rate over all input is given beside it.
* **Cold / warm.** A warm call read something from the cache; a cold one did
  not. Split again by position: the first call of a conversation is expected
  to be cold, a later one is not.
* **Cost** is what the vendor charges us, priced against the rate book for
  the window with the same vendor rule as every other report
  (``usage.llm_split_items``) -- cache writes included. A model with no rate
  is reported as unpriced, never as free; a call on the account's own key is
  counted and not priced.
* **Cost per successful outcome** exists only where an outcome does: a call
  that completed, a routine that delivered. Every call of the task counts --
  retries, rounds, the background summary -- and the cost of the tasks that
  failed is carried by the ones that succeeded, because that is what a
  success costs.
* **Prefix breakers.** Within one conversation the prefix (system prompt and
  tools as sent) should change only when it has to. Each change between two
  consecutive calls is a break, labelled by which half changed. Across
  conversations, one prompt version (fingerprint) sent with many different
  prefixes means something per-call sits in the prefix.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AgentEventModel, LlmCallUsageModel, WorkflowRunModel
from api.enums import AgentEventKind, CostComponent, WorkflowRunState
from api.services.billing import cache_capabilities
from api.services.billing.llm_usage import NormalisedUsage
from api.services.billing.money import MPAISE_PER_PAISE
from api.services.billing.usage import _CACHE_OUTSIDE_PROMPT_PROVIDERS, llm_split_items

#: The default window, in days.
WINDOW_DAYS = 7

#: How many rows each "top" list carries.
TOP = 20

#: A side call: in the task's cost, never in its prefix churn.
SIDE_FEATURES = frozenset({"summary"})

TOKEN_COMPONENTS: tuple[str, ...] = (
    CostComponent.LLM_INPUT.value,
    CostComponent.LLM_CACHED.value,
    CostComponent.LLM_CACHE_WRITE.value,
    CostComponent.LLM_OUTPUT.value,
)

#: The columns a call row carries, in the order :func:`build` reads them.
COLUMNS: tuple[str, ...] = (
    "created_at",
    "source",
    "feature",
    "provider",
    "model",
    "conversation_key",
    "workflow_run_id",
    "prompt_fingerprint",
    "prefix_hash",
    "system_hash",
    "tools_hash",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "is_retry",
    "byok",
)


def _when(call: dict[str, Any]) -> float:
    at = call.get("created_at")
    return at.timestamp() if isinstance(at, datetime) else 0.0


def _base_feature(feature: str | None) -> str:
    """``decibyl:byok`` and ``decibyl`` are the same kind of work."""
    return (feature or "unattributed").split(":", 1)[0]


def _usage(call: dict[str, Any]) -> NormalisedUsage:
    return NormalisedUsage(
        input_tokens=int(call.get("input_tokens") or 0),
        output_tokens=int(call.get("output_tokens") or 0),
        cache_read_tokens=int(call.get("cache_read_tokens") or 0),
        cache_write_tokens=int(call.get("cache_write_tokens") or 0),
        reasoning_tokens=int(call.get("reasoning_tokens") or 0),
        cache_outside_prompt=(call.get("provider") or "")
        in _CACHE_OUTSIDE_PROMPT_PROVIDERS,
    )


def _price(
    call: dict[str, Any], rates: dict[tuple[str, str, str], Any]
) -> tuple[float, list[str]]:
    """What the vendor charges for one call, in paise, and the components it
    has no rate for. A BYOK call is counted and not priced."""
    if call.get("byok"):
        return 0.0, []
    provider = call.get("provider") or ""
    model = call.get("model") or ""
    cost = 0.0
    unpriced: list[str] = []
    for item in llm_split_items(
        _usage(call).as_usage_fields(), provider=provider, model=model
    ):
        component = getattr(item.component, "value", item.component)
        rate = rates.get((component, provider, model)) or rates.get(
            (component, provider, "")
        )
        if rate is None:
            unpriced.append(component)
            continue
        cost += item.quantity / 1000 * rate.rate_mpaise / MPAISE_PER_PAISE
    return cost, unpriced


@dataclass
class _Bucket:
    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cacheable_input_tokens: int = 0
    cacheable_read_tokens: int = 0
    warm_calls: int = 0
    cost_paise: float = 0.0
    unpriced: set[str] = field(default_factory=set)

    def add(self, call: dict[str, Any], cost: float, unpriced: list[str]) -> None:
        usage = _usage(call)
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        if usage.cache_read_tokens:
            self.warm_calls += 1
        floor = cache_capabilities.min_cacheable_tokens(call.get("provider") or "")
        if floor is None or usage.total_input_tokens >= floor:
            self.cacheable_input_tokens += usage.total_input_tokens
            self.cacheable_read_tokens += usage.cache_read_tokens
        self.cost_paise += cost
        self.unpriced.update(unpriced)

    def as_dict(self) -> dict[str, Any]:
        total_input = (
            self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        )
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cacheable_input_tokens": self.cacheable_input_tokens,
            "hit_rate": _ratio(self.cacheable_read_tokens, self.cacheable_input_tokens),
            "raw_hit_rate": _ratio(self.cache_read_tokens, total_input),
            "warm_calls": self.warm_calls,
            "cold_calls": self.calls - self.warm_calls,
            "cost_paise": round(self.cost_paise, 2),
            "unpriced": sorted(self.unpriced),
        }


def _ratio(numerator: float, denominator: float) -> float | None:
    """None, not 0, when there is nothing to divide: no cacheable input is
    not the same fact as a cache that never hit."""
    return round(numerator / denominator, 4) if denominator else None


def prefix_breaks(calls: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every change of prefix between consecutive calls of a conversation.

    ``calls`` in any order; they are grouped by ``conversation_key`` and
    ordered by ``created_at``. Side calls (a summary sends its own prompt)
    and calls with no prefix recorded are skipped, as are calls with no
    conversation. Each break says which half changed: ``system``, ``tools``
    or ``both``.
    """
    by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        key = call.get("conversation_key")
        if not key or not call.get("prefix_hash"):
            continue
        if _base_feature(call.get("feature")) in SIDE_FEATURES:
            continue
        by_conversation[key].append(call)
    breaks: list[dict[str, Any]] = []
    for key, rows in by_conversation.items():
        rows.sort(key=_when)
        for before, after in zip(rows, rows[1:]):
            if before["prefix_hash"] == after["prefix_hash"]:
                continue
            system = before.get("system_hash") != after.get("system_hash")
            tools = before.get("tools_hash") != after.get("tools_hash")
            kind = "both" if system and tools else "system" if system else "tools"
            breaks.append(
                {
                    "conversation_key": key,
                    "feature": _base_feature(after.get("feature")),
                    "kind": kind,
                    "from_fingerprint": before.get("prompt_fingerprint"),
                    "to_fingerprint": after.get("prompt_fingerprint"),
                    "at": after.get("created_at"),
                }
            )
    return breaks


def summarise(
    calls: list[dict[str, Any]],
    *,
    rates: dict[tuple[str, str, str], Any],
    outcomes: dict[str, bool | None] | None = None,
) -> dict[str, Any]:
    """Pure: the report from rows already read.

    ``calls`` -- dicts with :data:`COLUMNS`. ``rates`` -- ``(component,
    provider, model) -> rate`` with ``rate_mpaise`` per 1k tokens, the exact
    model first and ``""`` for the provider's fallback. ``outcomes`` --
    ``conversation_key -> True`` (succeeded), ``False`` (did not), or absent /
    ``None`` where the work has no outcome to count.
    """
    outcomes = outcomes or {}
    totals = _Bucket()
    by_feature: dict[str, _Bucket] = defaultdict(_Bucket)
    by_model: dict[tuple[str, str], _Bucket] = defaultdict(_Bucket)
    by_fingerprint: dict[str, _Bucket] = defaultdict(_Bucket)
    fingerprint_feature: dict[str, str] = {}
    by_position: dict[tuple[str, str], _Bucket] = defaultdict(_Bucket)
    conversations: dict[str, dict[str, Any]] = {}

    ordered = sorted(calls, key=_when)
    seen_conversations: set[str] = set()
    first_prefixes: dict[str, set[str]] = defaultdict(set)
    fingerprint_conversations: dict[str, set[str]] = defaultdict(set)
    for call in ordered:
        cost, unpriced = _price(call, rates)
        feature = _base_feature(call.get("feature"))
        totals.add(call, cost, unpriced)
        by_feature[feature].add(call, cost, unpriced)
        by_model[(call.get("provider") or "", call.get("model") or "")].add(
            call, cost, unpriced
        )
        fingerprint = call.get("prompt_fingerprint")
        if fingerprint:
            by_fingerprint[fingerprint].add(call, cost, unpriced)
            fingerprint_feature.setdefault(fingerprint, feature)

        key = call.get("conversation_key")
        side = feature in SIDE_FEATURES
        if key and fingerprint:
            fingerprint_conversations[fingerprint].add(key)
        if key and not side:
            position = "first" if key not in seen_conversations else "later"
            seen_conversations.add(key)
            by_position[(feature, position)].add(call, cost, unpriced)
            if position == "first" and fingerprint and call.get("prefix_hash"):
                first_prefixes[fingerprint].add(call["prefix_hash"])
        if key:
            task = conversations.setdefault(
                key,
                {
                    "feature": None,
                    "calls": 0,
                    "retries": 0,
                    "side_calls": 0,
                    "cost_paise": 0.0,
                },
            )
            if not side and task["feature"] is None:
                task["feature"] = feature
            task["calls"] += 1
            task["retries"] += int(bool(call.get("is_retry")))
            task["side_calls"] += int(side)
            task["cost_paise"] += cost

    # Cost per successful outcome, by the kind of work the task was.
    tasks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tasks": 0,
            "with_outcome": 0,
            "succeeded": 0,
            "cost_paise": 0.0,
            "retry_calls": 0,
            "side_calls": 0,
        }
    )
    for key, task in conversations.items():
        group = tasks[task["feature"] or "summary_only"]
        group["tasks"] += 1
        group["retry_calls"] += task["retries"]
        group["side_calls"] += task["side_calls"]
        outcome = outcomes.get(key)
        if outcome is None:
            continue
        group["with_outcome"] += 1
        group["cost_paise"] += task["cost_paise"]
        group["succeeded"] += int(bool(outcome))
    cost_per_outcome = [
        {
            "feature": feature,
            "tasks": g["tasks"],
            "tasks_with_outcome": g["with_outcome"],
            "succeeded": g["succeeded"],
            "cost_paise_of_tasks_with_outcome": round(g["cost_paise"], 2),
            "cost_paise_per_success": (
                round(g["cost_paise"] / g["succeeded"], 2) if g["succeeded"] else None
            ),
            "retry_calls": g["retry_calls"],
            "side_calls": g["side_calls"],
        }
        for feature, g in sorted(tasks.items())
    ]

    breaks = prefix_breaks(calls)
    churn: dict[str, dict[str, Any]] = {}
    for b in breaks:
        entry = churn.setdefault(
            b["conversation_key"],
            {
                "conversation_key": b["conversation_key"],
                "feature": b["feature"],
                "breaks": 0,
                "kinds": defaultdict(int),
            },
        )
        entry["breaks"] += 1
        entry["kinds"][b["kind"]] += 1
    breaks_by_feature: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "conversations": 0,
            "conversations_with_breaks": 0,
            "breaks": 0,
            "system": 0,
            "tools": 0,
            "both": 0,
        }
    )
    for key, task in conversations.items():
        if task["feature"] is not None:
            breaks_by_feature[task["feature"]]["conversations"] += 1
    for entry in churn.values():
        row = breaks_by_feature[entry["feature"]]
        row["conversations_with_breaks"] += 1
        row["breaks"] += entry["breaks"]
        for kind, n in entry["kinds"].items():
            row[kind] += n

    top_churn = sorted(
        churn.values(), key=lambda e: (-e["breaks"], e["conversation_key"])
    )
    volatile = sorted(
        (
            {
                "prompt_fingerprint": fp,
                "feature": fingerprint_feature.get(fp),
                "conversations": len(fingerprint_conversations[fp]),
                "distinct_first_prefixes": len(prefixes),
            }
            for fp, prefixes in first_prefixes.items()
            if len(prefixes) > 1
        ),
        key=lambda r: -r["distinct_first_prefixes"],
    )

    def _top(buckets: dict[Any, _Bucket], label) -> list[dict[str, Any]]:
        rows = [{**label(k), **b.as_dict()} for k, b in buckets.items()]
        return sorted(rows, key=lambda r: (-r["cost_paise"], -r["calls"]))

    return {
        "totals": {
            **totals.as_dict(),
            "conversations": len(conversations),
            "retry_calls": sum(int(bool(c.get("is_retry"))) for c in calls),
            "side_calls": sum(
                int(_base_feature(c.get("feature")) in SIDE_FEATURES) for c in calls
            ),
        },
        "by_feature": _top(by_feature, lambda k: {"feature": k}),
        "by_model": _top(by_model, lambda k: {"provider": k[0], "model": k[1]}),
        "top_fingerprints": _top(
            by_fingerprint,
            lambda k: {"prompt_fingerprint": k, "feature": fingerprint_feature.get(k)},
        )[:TOP],
        "cold_warm": [
            {"feature": feature, "position": position, **b.as_dict()}
            for (feature, position), b in sorted(by_position.items())
        ],
        "cost_per_outcome": cost_per_outcome,
        "prefix_breaks": {
            "by_feature": [
                {"feature": feature, **row}
                for feature, row in sorted(breaks_by_feature.items())
            ],
            "top_conversations": [
                {**e, "kinds": dict(e["kinds"])} for e in top_churn[:TOP]
            ],
            "volatile_prefixes": volatile[:TOP],
        },
    }


def outcome_of(feature: str, run: dict[str, Any]) -> bool | None:
    """Whether a run's task succeeded, where that question has an answer.

    ``agent_call``: the call completed. ``routine``: the run delivered
    something. Anything else -- an agent's text chat, which never "ends" --
    has no outcome to count and is left out of cost per outcome rather than
    counted as a failure."""
    if feature == "agent_call":
        return bool(run.get("is_completed")) and (
            run.get("state") == WorkflowRunState.COMPLETED.value
        )
    if feature == "routine":
        return bool(run.get("delivered"))
    return None


async def _rates(
    session: AsyncSession, pairs: set[tuple[str, str]], at: datetime
) -> dict[tuple[str, str, str], Any]:
    from api.services.billing.rates import resolve_provider_rate

    rates: dict[tuple[str, str, str], Any] = {}
    for provider, model in pairs:
        for component in TOKEN_COMPONENTS:
            for m in (model, ""):
                if (component, provider, m) in rates:
                    continue
                rate = await resolve_provider_rate(
                    session, provider=provider, component=component, at=at, model=m
                )
                if rate is not None and (m == "" or rate.model == m):
                    rates[(component, provider, m)] = rate
    return rates


async def build(
    session: AsyncSession,
    *,
    end: datetime,
    days: int = WINDOW_DAYS,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Read ``[end - days, end)`` and summarise."""
    start = end - timedelta(days=days)
    query = select(*(getattr(LlmCallUsageModel, c) for c in COLUMNS)).where(
        LlmCallUsageModel.created_at >= start, LlmCallUsageModel.created_at < end
    )
    if organization_id is not None:
        query = query.where(LlmCallUsageModel.organization_id == organization_id)
    calls = [dict(zip(COLUMNS, row)) for row in (await session.execute(query)).all()]

    # Outcomes of the runs these calls belonged to.
    run_feature: dict[int, str] = {}
    for call in calls:
        run_id = call.get("workflow_run_id")
        feature = _base_feature(call.get("feature"))
        if run_id is not None and feature not in SIDE_FEATURES:
            run_feature.setdefault(int(run_id), feature)
    outcomes: dict[str, bool | None] = {}
    if run_feature:
        ids = list(run_feature)
        runs = {
            rid: {"is_completed": done, "state": state}
            for rid, done, state in (
                await session.execute(
                    select(
                        WorkflowRunModel.id,
                        WorkflowRunModel.is_completed,
                        WorkflowRunModel.state,
                    ).where(WorkflowRunModel.id.in_(ids))
                )
            ).all()
        }
        delivered = {
            rid
            for (rid,) in (
                await session.execute(
                    select(AgentEventModel.workflow_run_id).where(
                        AgentEventModel.workflow_run_id.in_(ids),
                        AgentEventModel.kind == AgentEventKind.DELIVERABLE.value,
                    )
                )
            ).all()
        }
        for rid, feature in run_feature.items():
            run = runs.get(rid)
            if run is None:
                continue
            outcomes[f"run:{rid}"] = outcome_of(
                feature, {**run, "delivered": rid in delivered}
            )

    rates = await _rates(
        session,
        {(c.get("provider") or "", c.get("model") or "") for c in calls},
        end,
    )
    report = summarise(calls, rates=rates, outcomes=outcomes)
    report["window"] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
    }
    report["organization_id"] = organization_id
    report["capabilities"] = cache_capabilities.matrix()
    return report


__all__ = [
    "COLUMNS",
    "WINDOW_DAYS",
    "build",
    "outcome_of",
    "prefix_breaks",
    "summarise",
]

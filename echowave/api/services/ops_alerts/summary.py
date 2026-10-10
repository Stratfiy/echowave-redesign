"""The morning mail: what yesterday cost, where it went, and how calls went.

Built from ``metering.meter`` over one IST day -- the same IST day the
billing rollups use -- so the figures are what billing metered, priced the
way billing prices them. Internal operations copy: provider costs, never
customer prices or plan names.

A component with usage and no rate says "no rate" beside its usage, rather
than reading as free. Spend nobody was attributed to is its own line under
the top workspaces, not left out of them.

Mailed once per day (a Redis claim on the day, so a re-run or a second
worker sends nothing). The staff page shows the last seven, as mailed when
there was a mail, built from billing otherwise.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ops_alerts import calls, metering, notify, store
from api.services.ops_alerts import thresholds as t
from api.services.ops_alerts.detectors import rupees

#: Shown on every summary, so what is left out is said rather than absent.
NOT_COUNTED = (
    "Not counted as spend: Decibyl's own platform fee and add-on lines (revenue, "
    "not cost), and usage on a workspace's own provider keys (billed to them)."
)


async def build(session: AsyncSession, day: date) -> dict[str, Any]:
    start, end = metering.day_window(day)
    metered = await metering.meter(session, start, end)
    base_start, _ = metering.day_window(day - timedelta(days=t.SPEND_BASELINE_DAYS))
    baseline = await metering.meter(session, base_start, start)
    average = baseline.total_paise / t.SPEND_BASELINE_DAYS

    ranked_orgs = sorted(
        ((org, paise) for org, paise in metered.by_org.items() if org is not None),
        key=lambda kv: -kv[1],
    )[: t.DAILY_SUMMARY_TOP_N]
    ranked_agents = sorted(metered.by_agent.items(), key=lambda kv: -kv[1])[
        : t.DAILY_SUMMARY_TOP_N
    ]
    org_names, agent_names = await metering.names(
        session,
        orgs=[org for org, _ in ranked_orgs],
        agents=[agent for agent, _ in ranked_agents],
    )
    agent_orgs = [org for _, org in agent_names.values() if org is not None]
    more_orgs, _ = await metering.names(
        session, orgs=[o for o in agent_orgs if o not in org_names], agents=[]
    )
    org_names.update(more_orgs)

    outcomes = await calls.outcomes(session, start, end)
    total = metered.total_paise
    lines = sorted(
        (line.as_dict() for line in metered.lines.values()),
        key=lambda line: (-line["cost_paise"], line["label"]),
    )
    return {
        "day": day.isoformat(),
        "total_paise": total,
        "average_paise": int(round(average)),
        "delta_paise": int(round(total - average)),
        "delta_pct": round((total - average) / average * 100, 1) if average else None,
        "lines": lines,
        "top_organizations": [
            {
                "organization_id": org,
                "name": org_names.get(org, f"Workspace {org}"),
                "cost_paise": int(round(paise)),
            }
            for org, paise in ranked_orgs
        ],
        "unattributed_paise": int(round(metered.by_org.get(None, 0.0))),
        "top_agents": [
            {
                "agent_id": agent,
                "name": agent_names.get(agent, (f"Agent {agent}", None))[0],
                "organization_id": agent_names.get(agent, ("", None))[1],
                "organization": org_names.get(
                    agent_names.get(agent, ("", None))[1] or -1
                ),
                "cost_paise": int(round(paise)),
            }
            for agent, paise in ranked_agents
        ],
        "calls": outcomes.as_dict(),
        "runs": await calls.run_counts(session, start, end),
        "active_organizations": await calls.active_organizations(session, start, end),
        "not_counted": NOT_COUNTED,
    }


def _usage(units: float, unit: str) -> str:
    if unit == "seconds":
        return f"{units / 60:,.1f} min"
    return f"{units:,.0f} {unit}"


def _line_text(line: dict[str, Any]) -> str:
    usage = _usage(line["units"], line["unit"])
    if not line["no_rate"]:
        return f"{rupees(line['cost_paise'])}   {usage}"
    unpriced = _usage(line["unpriced_units"], line["unit"])
    if line["cost_paise"]:
        return f"{rupees(line['cost_paise'])}   {usage}, of which {unpriced} no rate"
    return f"no rate   {usage}"


def compose(summary: dict[str, Any]) -> tuple[str, str]:
    day = date.fromisoformat(summary["day"])
    label = day.strftime("%a %d %b %Y")
    total = summary["total_paise"]
    if summary["delta_pct"] is None:
        versus = "no spend in the previous seven days to compare with"
    else:
        sign = "+" if summary["delta_paise"] >= 0 else ""
        versus = (
            f"7-day average {rupees(summary['average_paise'])}, "
            f"{sign}{summary['delta_pct']:.0f}%"
        )
    out = [
        f"Decibyl daily cost summary for {label} (IST).",
        "",
        f"Metered provider spend: {rupees(total)} ({versus}).",
        "",
        "By component",
    ]
    if not summary["lines"]:
        out.append("  Nothing was metered.")
    for line in summary["lines"]:
        out.append(f"  {line['label']}: {_line_text(line)}")

    out += ["", f"Top {t.DAILY_SUMMARY_TOP_N} workspaces"]
    if not summary["top_organizations"]:
        out.append("  None.")
    for n, org in enumerate(summary["top_organizations"], 1):
        out.append(
            f"  {n}. {org['name']} (id {org['organization_id']}): {rupees(org['cost_paise'])}"
        )
    if summary["unattributed_paise"]:
        out.append(
            f"  Not attributed to a workspace: {rupees(summary['unattributed_paise'])}"
        )

    out += ["", f"Top {t.DAILY_SUMMARY_TOP_N} agents"]
    if not summary["top_agents"]:
        out.append("  None.")
    for n, agent in enumerate(summary["top_agents"], 1):
        where = f", {agent['organization']}" if agent.get("organization") else ""
        out.append(
            f"  {n}. {agent['name']} (id {agent['agent_id']}{where}): "
            f"{rupees(agent['cost_paise'])}"
        )

    c, runs = summary["calls"], summary["runs"]
    out += [
        "",
        "Calls through a carrier",
        f"  {runs['carrier_calls']:,} placed: {c['answered']:,} answered, "
        f"{c['not_connected']:,} not picked up, {c['carrier_failed']:,} failed at the "
        f"carrier, {c['unknown']:,} with no outcome, {c['in_progress']:,} not finished",
        f"  Browser calls {runs['browser_calls']:,}; text runs {runs['text']:,}",
        "",
        f"Active workspaces: {summary['active_organizations']:,}",
        "",
        summary["not_counted"],
        "Internal operations mail: provider costs as billing metered them, not prices.",
    ]
    return f"[Decibyl ops] Cost summary {label}: {rupees(total)}", "\n".join(out) + "\n"


def _cache_key(day: date) -> str:
    return store.key("daily_summary", day.isoformat())


async def send_for(session: AsyncSession, day: date, *, client=None) -> dict[str, Any]:
    """Build and mail ``day``'s summary, once. Returns what happened."""
    claim = store.key("daily_sent", day.isoformat())
    async with store.connection(client) as redis:
        if not await redis.set(
            claim, datetime.now(UTC).isoformat(), nx=True, ex=3 * 86_400
        ):
            return {"day": day.isoformat(), "sent": False, "skipped": "already_sent"}
        try:
            summary = await build(session, day)
        except Exception:
            await redis.delete(claim)
            raise
        summary["mailed_at"] = datetime.now(UTC).isoformat()
        await redis.set(
            _cache_key(day),
            json.dumps(summary),
            ex=(t.DAILY_SUMMARY_HISTORY_DAYS + 2) * 86_400,
        )
        subject, body = compose(summary)
        sent = await notify.send(subject, body)
        if not sent:
            await redis.delete(claim)
            logger.warning("Daily cost summary for {} was not mailed", day.isoformat())
    return {"day": day.isoformat(), "sent": sent, "total_paise": summary["total_paise"]}


async def recent(
    session: AsyncSession,
    *,
    today: date,
    days: int = t.DAILY_SUMMARY_HISTORY_DAYS,
    client=None,
) -> list[dict[str, Any]]:
    """The last ``days`` summaries before ``today``, newest first: as mailed
    where one was, built from billing where not."""
    out = []
    async with store.connection(client) as redis:
        for back in range(1, days + 1):
            day = today - timedelta(days=back)
            cached = store.text(await redis.get(_cache_key(day)))
            if cached:
                out.append(json.loads(cached))
                continue
            summary = await build(session, day)
            summary["mailed_at"] = None
            out.append(summary)
    return out

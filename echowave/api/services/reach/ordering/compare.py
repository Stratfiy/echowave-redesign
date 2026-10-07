"""Prices and coupons compared across the apps a person has connected.

Only apps the person signed in to through their official server are
compared -- never a site read some other way -- and the answer always says
which apps were compared, which were not and why, and when the prices were
read. Prices from a search are what the app lists; the order card's total
(with delivery, taxes and the coupon applied) is the figure that counts,
and the result says so.

The comparison is also put on the thread as a small table
(``reach_comparison``), so the "which apps, and when" is on screen and not
only in the model's sentence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.enums import AgentEventActor, AgentEventKind
from api.services.reach import connections, safety, wire
from api.services.reach.ordering import normalise, providers
from api.services.workflow import agent_timeline

KIND = AgentEventKind.REACH_COMPARISON.value
MAX_ITEMS = 10


def _best(results: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    words = {w for w in wanted.lower().split() if len(w) > 2}
    priced = [
        r
        for r in results
        if r.get("price_paise") is not None
        and r.get("available")
        and (not words or words & set(r["name"].lower().split()))
    ]
    return min(priced, key=lambda r: r["price_paise"]) if priced else None


async def compare(
    *, organization_id: int, user_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    wanted = [
        normalise.text(item, 80)
        for item in (arguments.get("items") or [])
        if isinstance(item, str) and item.strip()
    ][:MAX_ITEMS]
    if not wanted:
        return {"status": "not_available", "reason": "Say which items to compare."}

    compared: list[dict[str, Any]] = []
    not_compared: list[dict[str, Any]] = []
    for provider in providers.PROVIDERS.values():
        row = await connections.live(
            organization_id, user_id, connections.ORDERING, provider.key
        )
        state = providers.describe(provider, row)
        if state["state"] != providers.CONNECTED:
            not_compared.append(
                {
                    "app": provider.name,
                    "why": state["reason"]
                    or "Not connected by you, so it was not compared.",
                }
            )
            continue
        lines = []
        failed = None
        for item in wanted:
            try:
                data = await connections.call(
                    row,
                    provider.tool_for(
                        providers.SEARCH, {t.get("name") for t in row.tools or []}
                    ),
                    {"query": item},
                )
            except (wire.WireError, wire.ToolRefused, wire.NeedsSignIn) as exc:
                failed = f"{provider.name} did not answer ({type(exc).__name__})."
                break
            best = _best(normalise.search_results(data), item)
            lines.append(
                {
                    "asked": item,
                    "found": best["name"] if best else None,
                    "store": best["store_name"] if best else None,
                    "price_paise": best["price_paise"] if best else None,
                }
            )
        if failed:
            not_compared.append({"app": provider.name, "why": failed})
            continue
        coupons: list[dict[str, Any]] = []
        offers_tool = provider.tool_for(
            providers.OFFERS, {t.get("name") for t in row.tools or []}
        )
        if offers_tool:
            try:
                coupons = normalise.offers(await connections.call(row, offers_tool, {}))
            except (wire.WireError, wire.ToolRefused, wire.NeedsSignIn):
                coupons = []
        found = [line for line in lines if line["price_paise"] is not None]
        compared.append(
            {
                "app": provider.name,
                "lines": lines,
                "listed_total_paise": sum(line["price_paise"] for line in found),
                "missing": [
                    line["asked"] for line in lines if line["price_paise"] is None
                ],
                "coupons": coupons,
            }
        )

    at = datetime.now(UTC)
    if compared:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=KIND,
            actor=AgentEventActor.AGENT.value,
            summary=(
                f"Compared {', '.join(c['app'] for c in compared)} for "
                f"{len(wanted)} item{'s' if len(wanted) != 1 else ''}"
            )[:500],
            payload={
                "items": wanted,
                "compared": compared,
                "not_compared": not_compared,
                "at": at.isoformat(),
                "for_user_id": user_id,
            },
            in_channel=False,
        )
    names = ", ".join(c["app"] for c in compared) or "no app"
    say = (
        f"Compared {names} at {at.strftime('%H:%M')} UTC on {at.strftime('%d %b')}. "
        + (
            "Not compared: "
            + "; ".join(f"{n['app']} ({n['why']})" for n in not_compared)
            + ". "
            if not_compared
            else ""
        )
        + "These are listed prices; the order card's total, with delivery, "
        "taxes and any coupon, is the figure that counts."
    )
    if not compared:
        return {
            "status": "not_available",
            "reason": (
                "No ordering app you have connected could be compared. "
                + "; ".join(f"{n['app']}: {n['why']}" for n in not_compared)
            ),
        }
    wrapped = safety.as_data(
        source="ordering apps you connected",
        data={"compared": compared, "not_compared": not_compared, "at": at.isoformat()},
    )
    wrapped["say"] = say
    if len(compared) == 1:
        wrapped["say"] += (
            f" Only {compared[0]['app']} is connected, so there is nothing to compare it against."
        )
    return wrapped


__all__ = ["KIND", "compare"]

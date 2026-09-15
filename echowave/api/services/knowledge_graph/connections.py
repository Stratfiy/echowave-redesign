"""Connections surfaced (Family B, B4).

A background job that looks for the patterns worth one line and says
nothing otherwise: the same counterpart in repeated complaints, an expiry
date that overlaps a travel date, a person who links two others the
person never linked. One WhatsApp line, only when a threshold is
crossed, only to a person who turned it on, never twice for the same
connection, and never more than once a day for the account (the shared
cap). Thresholds are conservative on purpose and live at the top.

Not here: a spend category beyond its usual range. The record holds no
spend by category yet, so the detector would have nothing to read; it
is the first thing to add when it does.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.billing import events as billing_events
from api.services.knowledge_graph import quiet
from api.services.knowledge_graph.client import Fact, recent_facts
from api.services.workflow import agent_timeline

KIND = "connection"
LOOKBACK_DAYS = 30
#: The same counterpart in this many complaints in the window.
REPEATED_COMPLAINTS = 3
#: An expiry within this many days of a trip.
OVERLAP_DAYS = 3
#: A person named alongside this many distinct others.
BRIDGE_OTHERS = 2
#: A connection said once is not said again for this long.
DEDUPE_DAYS = 60

COMPLAINT = re.compile(
    r"\b(complain\w*|late|delay\w*|wrong|missing|damaged|not (working|delivered|paid)|"
    r"refund|unhappy|poor|broken|defect\w*)\b",
    re.IGNORECASE,
)
TRAVEL = re.compile(
    r"\b(trip|travel|flight|visa|journey|tour|holiday|vacation)\b", re.IGNORECASE
)
EXPIRY = re.compile(
    r"\b(expir\w*|renew\w*|premium|due|valid till|validity)\b", re.IGNORECASE
)
NAME = re.compile(r"\b([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})?)\b")
NOT_NAMES = {
    "The",
    "This",
    "That",
    "They",
    "There",
    "Then",
    "When",
    "Person",
    "Decibyl",
    "Agent",
    "Caller",
}


@dataclass(frozen=True)
class Connection:
    line: str
    key: str  # what makes it the same connection twice


def _names(text: str) -> set[str]:
    return {n for n in NAME.findall(text or "") if n.split()[0] not in NOT_NAMES}


def repeated_counterpart(facts: list[Fact]) -> list[Connection]:
    counts: dict[str, int] = {}
    for fact in facts:
        if not COMPLAINT.search(fact.fact):
            continue
        for name in _names(fact.fact):
            counts[name] = counts.get(name, 0) + 1
    return [
        Connection(
            line=f"{name} has come up in {n} complaints in the last {LOOKBACK_DAYS} days.",
            key=f"complaints:{name.lower()}",
        )
        for name, n in sorted(counts.items())
        if n >= REPEATED_COMPLAINTS
    ]


def expiry_meets_travel(tasks: list[Any]) -> list[Connection]:
    trips = [t for t in tasks if TRAVEL.search(getattr(t, "title", "") or "")]
    expiries = [t for t in tasks if EXPIRY.search(getattr(t, "title", "") or "")]
    out: list[Connection] = []
    for trip in trips:
        for expiry in expiries:
            if trip is expiry or not trip.due_at or not expiry.due_at:
                continue
            if abs((trip.due_at - expiry.due_at).days) <= OVERLAP_DAYS:
                out.append(
                    Connection(
                        line=(
                            f"{expiry.title} ({expiry.due_at:%d %b}) falls within "
                            f"{OVERLAP_DAYS} days of {trip.title} ({trip.due_at:%d %b})."
                        ),
                        key=f"overlap:{expiry.id}:{trip.id}",
                    )
                )
    return out


def bridge(facts: list[Fact]) -> list[Connection]:
    with_whom: dict[str, set[str]] = {}
    for fact in facts:
        names = _names(fact.fact)
        for name in names:
            with_whom.setdefault(name, set()).update(names - {name})
    return [
        Connection(
            line=f"{name} is connected to {', '.join(sorted(others))} in what memory holds.",
            key=f"bridge:{name.lower()}:{':'.join(sorted(o.lower() for o in others))}",
        )
        for name, others in sorted(with_whom.items())
        if len(others) >= BRIDGE_OTHERS
    ]


def find(facts: list[Fact], tasks: list[Any]) -> list[Connection]:
    return repeated_counterpart(facts) + expiry_meets_travel(tasks) + bridge(facts)


async def _first_unsaid(
    organization_id: int, found: list[Connection]
) -> Connection | None:
    """The first connection this account has not been told, claiming it."""
    import redis.asyncio as aioredis

    from api import constants

    try:
        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 - no Redis: say the first, once
        logger.warning("Connection dedupe unavailable: {}", exc)
        return found[0] if found else None
    for connection in found:
        digest = hashlib.sha1(connection.key.encode()).hexdigest()[:16]
        try:
            if await client.set(
                f"memory:connection:{organization_id}:{digest}",
                "1",
                ex=DEDUPE_DAYS * 86400,
                nx=True,
            ):
                return connection
        except Exception as exc:  # noqa: BLE001
            logger.warning("Connection dedupe failed: {}", exc)
            return connection
    return None


async def gather(
    organization_id: int, *, now: datetime
) -> tuple[list[Fact] | None, list[Any]]:
    facts: list[Fact] | None = None
    tasks: list[Any] = []
    try:
        facts = await recent_facts(
            organization_id, since=now - timedelta(days=LOOKBACK_DAYS), limit=200
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Connections could not read the graph for {}: {}", organization_id, exc
        )
    try:
        tasks = [
            t
            for t in await db_client.tasks_due_between(now, now + timedelta(days=60))
            if int(getattr(t, "organization_id", organization_id))
            == int(organization_id)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Connections could not read the board for {}: {}", organization_id, exc
        )
    return facts, tasks


async def deliver(organization_id: int, connection: Connection) -> None:
    body = f"One thing memory noticed: {connection.line}"
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=body[:500],
        payload={"body": body, "from": "Decibyl", "kind": KIND},
        in_channel=False,
    )
    try:
        from api.services.messaging import whatsapp_inbound
        from api.services.workflow import documents

        numbers, _ = await documents.own_channels(organization_id)
        if numbers:
            await whatsapp_inbound.reply(
                organization_id=organization_id, to=sorted(numbers)[0], body=body
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Connection could not go on WhatsApp for {}: {}", organization_id, exc
        )


async def notice(now: datetime | None = None) -> dict[str, int]:
    """Every account with somebody opted in: look, and say one line only
    if a threshold is crossed and the day's slot is free."""
    now = now or datetime.now(UTC)
    counters = {"considered": 0, "said": 0, "quiet": 0, "capped": 0, "failed": 0}
    for organization_id in (await quiet.people_opted_in(quiet.CONNECTIONS)).keys():
        counters["considered"] += 1
        try:
            facts, tasks = await gather(organization_id, now=now)
            if facts is not None:
                await billing_events.charge_in_own_session(
                    organization_id=organization_id,
                    event=billing_events.KNOWLEDGE_ANSWER,
                    ref_id=f"{KIND}:{organization_id}:{now.date().isoformat()}",
                    note="connections from memory",
                )
            found = find(facts or [], tasks)
            if not found:
                counters["quiet"] += 1
                continue
            # The slot before the dedupe: an account that has heard from
            # memory today keeps its unsaid connection for tomorrow.
            if not await quiet.claim_daily_slot(organization_id, now=now):
                counters["capped"] += 1
                continue
            connection = await _first_unsaid(organization_id, found)
            if connection is None:
                counters["quiet"] += 1
                continue
            await deliver(organization_id, connection)
            counters["said"] += 1
        except Exception:
            logger.exception("Connections failed for organization {}", organization_id)
            counters["failed"] += 1
    logger.info("Connections: {}", counters)
    return counters


__all__ = [
    "BRIDGE_OTHERS",
    "Connection",
    "OVERLAP_DAYS",
    "REPEATED_COMPLAINTS",
    "bridge",
    "deliver",
    "expiry_meets_travel",
    "find",
    "gather",
    "notice",
    "repeated_counterpart",
]

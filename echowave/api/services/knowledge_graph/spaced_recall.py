"""Spaced recall (Family B, B6).

Something the person asked memory about once and never again comes back
only when a related event approaches -- the warranty when the appliance
is mentioned on the thread, the tailor's name when the festival's task
is a week away -- and at growing intervals, so the third mention is a
month after the second. Never a standalone "did you know".

Two doors, both quiet:

- On the thread, a line that mentions a subject asked about before gets
  a short "Asked before" block in Decibyl's context, and the prompt says
  to mention it only if it helps the line. No message is sent.
- Once a day, a board task due within a week whose title names such a
  subject earns one WhatsApp line, for a person who turned it on, under
  the account's daily cap.

What was asked is a row in the record (``subject_type="asked"``), written
by recall; asked once means first and last seen are the same moment.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.knowledge_graph import quiet
from api.services.knowledge_graph.teach import subject_key
from api.services.workflow import agent_timeline

SUBJECT_ASKED = "asked"
KIND = "spaced_recall"
#: Days between resurfacings: the first after a day, then three, a week …
INTERVALS = (1, 3, 7, 14, 30, 60)
LOOKAHEAD_DAYS = 7
STOP = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "what",
    "who",
    "when",
    "did",
    "about",
    "from",
    "how",
    "was",
    "our",
    "your",
    "you",
    "any",
    "does",
    "have",
}


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in STOP
    }


async def remember_asked(organization_id: int, *, about: str, question: str) -> None:
    """Recall calls this: what was asked, keyed by its subject. A second
    ask of the same subject moves last_seen and drops it from spaced
    recall (asked twice is remembered already)."""
    key = subject_key(about or question[:60])
    if not key:
        return
    try:
        await db_client.remember_organisation_facts(
            organization_id=organization_id,
            facts={"question": (question or about)[:300]},
            status="learned",
            subject_type=SUBJECT_ASKED,
            subject_key=key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not note what was asked for org {}: {}", organization_id, exc
        )


async def asked_once(organization_id: int) -> list[Any]:
    rows = await db_client.subject_facts(
        organization_id=organization_id, subject_type=SUBJECT_ASKED, limit=200
    )
    return [
        r
        for r in rows
        if getattr(r, "first_seen_at", None)
        and r.first_seen_at == getattr(r, "last_seen_at", None)
    ]


async def _due(organization_id: int, key: str, *, now: datetime) -> bool:
    """Whether the interval since the last resurfacing has passed, and if
    so, count this one. Without Redis: once, and then never (no way to
    space it, so the safe side is silence)."""
    import redis.asyncio as aioredis

    from api import constants

    try:
        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
        rkey = f"memory:resurfaced:{organization_id}:{key}"
        stored = await client.get(rkey)
        count, last = 0, None
        if stored:
            n, _, when = stored.partition("|")
            count, last = int(n), datetime.fromisoformat(when)
        if last and now - last < timedelta(
            days=INTERVALS[min(count, len(INTERVALS) - 1)]
        ):
            return False
        await client.set(rkey, f"{count + 1}|{now.isoformat()}", ex=400 * 86400)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Spaced recall could not check its interval: {}", exc)
        return False


async def related_context(
    organization_id: int, line: str, *, now: datetime | None = None
) -> str:
    """The "Asked before" block for a thread line, or empty. Never raises."""
    now = now or datetime.now(UTC)
    words = _tokens(line)
    if not words:
        return ""
    try:
        rows = await asked_once(organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Spaced recall could not read what was asked: {}", exc)
        return ""
    lines: list[str] = []
    for row in rows:
        if not (words & _tokens(f"{row.subject_key} {row.value}")):
            continue
        if not await _due(organization_id, row.subject_key, now=now):
            continue
        when = getattr(row, "first_seen_at", None)
        stamp = f" on {when:%d %b}" if when else ""
        lines.append(
            f'- You asked memory about {row.subject_key}{stamp}: "{row.value}"'
        )
        if len(lines) >= 2:
            break
    if not lines:
        return ""
    return "## Asked before (mention only if it helps this line)\n" + "\n".join(lines)


async def deliver(organization_id: int, body: str) -> None:
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
            "Spaced recall could not go on WhatsApp for {}: {}", organization_id, exc
        )


def match(rows: list[Any], tasks: list[Any]) -> list[tuple[Any, Any]]:
    """(asked row, task) pairs whose words overlap."""
    out = []
    for task in tasks:
        task_words = _tokens(getattr(task, "title", "") or "")
        for row in rows:
            if task_words & _tokens(f"{row.subject_key} {row.value}"):
                out.append((row, task))
    return out


async def resurface(now: datetime | None = None) -> dict[str, int]:
    """Once a day: a task due within a week that names something asked
    about once earns one line, for accounts with somebody opted in."""
    now = now or datetime.now(UTC)
    counters = {"considered": 0, "said": 0, "quiet": 0, "capped": 0, "failed": 0}
    for organization_id in (await quiet.people_opted_in(quiet.SPACED_RECALL)).keys():
        counters["considered"] += 1
        try:
            rows = await asked_once(organization_id)
            tasks = [
                t
                for t in await db_client.tasks_due_between(
                    now, now + timedelta(days=LOOKAHEAD_DAYS)
                )
                if int(getattr(t, "organization_id", organization_id))
                == int(organization_id)
            ]
            pairs = match(rows, tasks)
            said = False
            for row, task in pairs:
                if not await _due(organization_id, row.subject_key, now=now):
                    continue
                if not await quiet.claim_daily_slot(organization_id, now=now):
                    counters["capped"] += 1
                    said = True
                    break
                when = getattr(row, "first_seen_at", None)
                stamp = f" on {when:%d %b}" if when else ""
                body = (
                    f"{task.title} is due {task.due_at:%d %b}. You asked memory about "
                    f"{row.subject_key}{stamp}; want me to look it up again?"
                )
                await deliver(organization_id, body)
                counters["said"] += 1
                said = True
                break
            if not said:
                counters["quiet"] += 1
        except Exception:
            logger.exception(
                "Spaced recall failed for organization {}", organization_id
            )
            counters["failed"] += 1
    logger.info("Spaced recall: {}", counters)
    return counters


__all__ = [
    "INTERVALS",
    "SUBJECT_ASKED",
    "asked_once",
    "deliver",
    "match",
    "related_context",
    "remember_asked",
    "resurface",
]

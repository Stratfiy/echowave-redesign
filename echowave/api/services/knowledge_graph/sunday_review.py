"""The Sunday review (Family B, B3).

One message a week, to the people who asked for it: what memory learned
this week, promises made and kept or not, dates in the next fortnight,
one connection worth a line. Under fifteen lines. Quiet by rule: a week
in which nothing happened is one line saying so, and no model is asked
to dress it up.

Where it goes: the Home thread once per account, WhatsApp on the
account's verified number when it has one, and email to each person who
turned it on. It counts against the account's one-message-a-day cap
(:mod:`quiet`), and the graph read behind it is billed once as a
knowledge answer, like any other graph read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.billing import events as billing_events
from api.services.knowledge_graph import quiet
from api.services.knowledge_graph.client import Fact, recent_facts
from api.services.workflow import agent_timeline

KIND = "sunday_review"
MAX_LINES = 15
LOOKAHEAD_DAYS = 14
QUIET_LINE = "Quiet week: nothing new in memory and nothing due in the next two weeks."

SYSTEM = (
    "You write a short weekly review for one person from the material "
    "given, in plain words, in at most 15 lines including headings, and "
    "nothing else. Sections, each only if it has something: 'Added this "
    "week' (the facts, one line each, at most five); 'Promises' (made and "
    "kept, made and not kept -- only if the material shows a promise); "
    "'Coming up' (dates in the next two weeks); 'One connection' (one line "
    "linking two things in the material, only if it is really there). A "
    "fact marked inferred is something that was said or implied: write it "
    "as such ('Ravi said …'), never as settled. A fact marked confirmed may "
    "be stated as fact. Never invent, never pad, no greeting, no sign-off."
)


@dataclass
class Material:
    """What the week gave us, before it is written up."""

    facts: list[Fact] | None = None  # None: no graph on this deployment
    confirmed: list[Any] = field(default_factory=list)
    due: list[Any] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.facts and not self.confirmed and not self.due


def week_of(now: datetime) -> tuple[datetime, datetime]:
    """The seven days ending now."""
    end = now.astimezone(UTC)
    return end - timedelta(days=7), end


async def gather(organization_id: int, *, now: datetime) -> Material:
    """Each reading fails alone; a week with one broken source still gets
    its other two."""
    start, end = week_of(now)
    material = Material()
    try:
        material.facts = await recent_facts(organization_id, since=start, limit=40)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Sunday review could not read the graph for {}: {}", organization_id, exc
        )
        material.facts = None
    try:
        rows = await db_client.subject_facts(
            organization_id=organization_id, status="confirmed", limit=100
        )
        material.confirmed = [
            r for r in rows if (getattr(r, "confirmed_at", None) or start) >= start
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Sunday review could not read the record for {}: {}", organization_id, exc
        )
    try:
        material.due = [
            t
            for t in await db_client.tasks_due_between(
                end, end + timedelta(days=LOOKAHEAD_DAYS)
            )
            if int(getattr(t, "organization_id", organization_id))
            == int(organization_id)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Sunday review could not read the board for {}: {}", organization_id, exc
        )
    return material


def material_text(material: Material) -> str:
    lines: list[str] = []
    for fact in material.facts or []:
        when = fact.valid_at or fact.created_at
        stamp = f" ({when:%d %b})" if when else ""
        lines.append(f"- [{fact.status}] {fact.fact}{stamp}")
    for row in material.confirmed:
        lines.append(f"- [confirmed] {row.subject_key} — {row.key}: {row.value}")
    for task in material.due:
        due = getattr(task, "due_at", None)
        stamp = f" ({due:%d %b})" if due else ""
        lines.append(f"- [coming up] {getattr(task, 'title', 'something due')}{stamp}")
    return "\n".join(lines)


def trim(text: str) -> str:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    return "\n".join(lines[:MAX_LINES])


def plain_review(material: Material) -> str:
    """Written without a model: what the material says, no more."""
    lines: list[str] = []
    facts = material.facts or []
    if facts:
        lines.append("Added this week:")
        for fact in facts[:5]:
            said = "" if fact.status == "confirmed" else " (said, not confirmed)"
            lines.append(f"- {fact.fact}{said}")
    if material.confirmed:
        lines.append("Confirmed this week:")
        for row in material.confirmed[:4]:
            lines.append(f"- {row.subject_key} — {row.key}: {row.value}")
    if material.due:
        lines.append("Coming up:")
        for task in material.due[:4]:
            due = getattr(task, "due_at", None)
            lines.append(
                f"- {getattr(task, 'title', 'something due')}"
                + (f" ({due:%d %b})" if due else "")
            )
    return trim("\n".join(lines))


async def compose(organization_id: int, material: Material) -> str:
    """The review. One line when the week was quiet; otherwise the
    account's own model writes it, and if it cannot, the plain version."""
    if material.empty:
        return QUIET_LINE
    from api.services.agent_builder import client, settings

    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_model(session)
        conversation = client.Conversation()
        conversation.add_user("Material for the week:\n" + material_text(material))
        reply = await client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[],
        )
        text = trim(reply.text or "")
        return text or plain_review(material)
    except Exception as exc:  # noqa: BLE001 - the review still goes out
        logger.warning(
            "Sunday review could not be written for {}: {}", organization_id, exc
        )
        return plain_review(material)


async def deliver(
    organization_id: int, body: str, *, people: list[Any], week_end: datetime
) -> None:
    """Thread, WhatsApp on the account's number, email to each person."""
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary="Your Sunday review",
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
            "Sunday review could not go on WhatsApp for {}: {}", organization_id, exc
        )
    emails = sorted(
        {
            str(p.email).strip().lower()
            for p in people
            if getattr(p, "email", None) and getattr(p, "email_verified_at", None)
        }
    )
    if emails:
        try:
            from api.services.messaging import announce

            await announce.announce(
                organization_id=organization_id,
                kind=KIND,
                notice=announce.Notice(
                    subject="Your Sunday review",
                    body=body,
                    dedupe_key=f"{organization_id}:{week_end.date().isoformat()}",
                ),
                to=emails,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Sunday review could not be mailed for {}: {}", organization_id, exc
            )


async def send_reviews(now: datetime | None = None) -> dict[str, int]:
    """Every account with somebody opted in, once, under the daily cap."""
    now = now or datetime.now(UTC)
    _, week_end = week_of(now)
    counters = {"considered": 0, "sent": 0, "capped": 0, "failed": 0}
    for organization_id, people in (
        await quiet.people_opted_in(quiet.SUNDAY_REVIEW)
    ).items():
        counters["considered"] += 1
        try:
            if not await quiet.claim_daily_slot(organization_id, now=now):
                counters["capped"] += 1
                continue
            material = await gather(organization_id, now=now)
            body = await compose(organization_id, material)
            if material.facts is not None:
                # The graph was read for this account: a knowledge answer.
                await billing_events.charge_in_own_session(
                    organization_id=organization_id,
                    event=billing_events.KNOWLEDGE_ANSWER,
                    ref_id=f"{KIND}:{organization_id}:{week_end.date().isoformat()}",
                    note="Sunday review from memory",
                )
            await deliver(organization_id, body, people=people, week_end=week_end)
            counters["sent"] += 1
        except Exception:
            logger.exception(
                "Sunday review failed for organization {}", organization_id
            )
            counters["failed"] += 1
    logger.info("Sunday review: {}", counters)
    return counters


__all__ = [
    "KIND",
    "MAX_LINES",
    "QUIET_LINE",
    "Material",
    "compose",
    "deliver",
    "gather",
    "plain_review",
    "send_reviews",
    "trim",
    "week_of",
]

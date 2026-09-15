"""The question cards on Home, built from this account's own life.

Two fixed questions -- "What happened this week?", "What needs my attention
today?" -- were the same for a clinic that has run a bot for a month and a
logistics firm that signed up an hour ago. ChatGPT's opening suggestions are
not: they come from what you asked before and what you are working on. So
do these.

Every card is a sentence sent to Decibyl as a message when pressed, the way
the two fixed ones were, and every card is true of this account right now:

* what the person asked Decibyl last, offered again, because the question
  somebody asks on Monday is the question they ask on Tuesday;
* the bot that did the most this week, by name;
* callers nobody rang back;
* the task board, when something on it is stuck;
* the time: last week on a Monday morning, since yesterday otherwise, and
  what needs closing in the evening.

A brand-new account has none of that, and gets the first jobs for the
business it named at the door instead of five generic ones. The door's
answers are the only thing here that is not observed behaviour, and they
are what the person told us about themselves.

Pure at the core: ``build`` takes facts and returns cards, so the choice is
testable without a database. ``gather`` does the reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind

#: Four cards, like the chips: a fifth reads as a menu.
MAX_OPENERS = 4

#: How many characters a remembered question keeps on a card.
CARD_CHARS = 72

#: How far back the person's own questions are read.
QUESTIONS_READ = 40

#: The facts row the door writes: subject_type "door", keys role/business.
DOOR_SUBJECT = "door"

#: The first jobs by business, as things you would say to a colleague. Each
#: is a message Decibyl answers by naming a template and starting the bot.
#: ``services`` and ``software`` fall to the default list on purpose: the
#: word covers too much to guess a job from.
FIRST_JOBS_BY_BUSINESS: dict[str, tuple[str, ...]] = {
    "clinic": (
        "Answer my clinic's phone and book appointments",
        "Remind patients on WhatsApp the day before",
        "Answer staff questions from our documents",
        "Call back everyone who rang while we were closed",
    ),
    "real_estate": (
        "Call every new property enquiry within minutes",
        "Qualify buyers on budget and location, then book a site visit",
        "Reply to enquiries on WhatsApp",
        "Send me a summary of new leads every morning",
    ),
    "education": (
        "Follow up every course enquiry and book a counselling session",
        "Answer parents' questions from our prospectus",
        "Remind students about fees before the due date",
        "Send me a summary every morning",
    ),
    "retail": (
        "Confirm COD orders before we ship them",
        "Reply to customers on WhatsApp",
        "Tell customers where their order is",
        "Chase overdue payments",
    ),
    "logistics": (
        "Quote shipments from our rate card",
        "Tell customers where their shipment is",
        "Chase overdue invoices",
        "Send me a summary every morning",
    ),
    "finance": (
        "Remind customers before their due date",
        "Chase overdue payments within the rules",
        "Answer staff questions from our policies",
        "Send me a summary every morning",
    ),
    "hospitality": (
        "Take table bookings on the phone",
        "Reply to guests on WhatsApp",
        "Answer questions about timings and location",
        "Send me a summary every morning",
    ),
}

DEFAULT_FIRST_JOBS: tuple[str, ...] = (
    "Answer my phone and book appointments",
    "Reply to customers on WhatsApp",
    "Chase overdue payments",
    "Answer staff questions from our documents",
)


def _opener(kind: str, text: str) -> dict[str, Any]:
    return {"kind": kind, "text": text}


def _clip(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= CARD_CHARS:
        return cleaned
    return cleaned[: CARD_CHARS - 1].rstrip() + "…"


def _time_question(now: datetime) -> str:
    # Monday morning reads back over the weekend; an evening asks what is
    # left to close; the rest of the time, since yesterday.
    if now.weekday() == 0 and now.hour < 12:
        return "What happened last week?"
    if now.hour >= 17:
        return "What needs closing before tomorrow?"
    return "What happened since yesterday?"


def build(
    *,
    members: list[dict[str, Any]],
    recent_questions: list[str],
    unreturned_missed_calls: int = 0,
    stuck_tasks: int = 0,
    business: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """The cards, most personal first, capped at four.

    ``recent_questions`` are the person's own lines to Decibyl, newest
    first. ``members`` are the team rows the home endpoint already has.
    """
    now = now or datetime.now()

    if not members:
        jobs = FIRST_JOBS_BY_BUSINESS.get((business or "").strip().lower())
        return [_opener("first_job", text) for text in (jobs or DEFAULT_FIRST_JOBS)][
            :MAX_OPENERS
        ]

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, text: str) -> None:
        key = text.casefold().rstrip("?.! ")
        if key in seen or not text:
            return
        seen.add(key)
        cards.append(_opener(kind, text))

    # What they asked last: the one thing most likely to be asked again.
    # Two at most, so the cards are not only an echo.
    for question in recent_questions:
        if len([c for c in cards if c["kind"] == "asked_before"]) >= 2:
            break
        if question and not question.lstrip().startswith(("@", "#")):
            add("asked_before", _clip(question))

    if unreturned_missed_calls > 0:
        people = "person" if unreturned_missed_calls == 1 else "people"
        add(
            "missed_calls",
            f"Call back the {unreturned_missed_calls} {people} who rang and got nobody",
        )

    if stuck_tasks > 0:
        add("stuck_tasks", "What is stuck on the task board?")

    busiest = max(members, key=lambda m: int(m.get("calls") or 0), default=None)
    if busiest and int(busiest.get("calls") or 0) > 0:
        add("busiest_bot", f"How did {busiest['name']} do this week?")

    add("time", _time_question(now))
    add("attention", "What needs my attention today?")

    return cards[:MAX_OPENERS]


async def recent_questions(organization_id: int) -> list[str]:
    """The person's own lines to Decibyl, newest first, distinct."""
    try:
        rows = await db_client.agent_events(
            organization_id=organization_id,
            assistant_thread=True,
            kinds=[AgentEventKind.MESSAGE.value],
            limit=QUESTIONS_READ,
        )
    except Exception as exc:  # noqa: BLE001 - a card is a nicety, not a dependency
        logger.warning(
            "Could not read what org {} asked before: {}", organization_id, exc
        )
        return []
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.actor != AgentEventActor.HUMAN.value:
            continue
        body = str((row.payload or {}).get("body") or row.summary or "").strip()
        key = body.casefold()
        if body and key not in seen:
            seen.add(key)
            out.append(body)
    return out


async def stuck_tasks(organization_id: int) -> int:
    """Tasks a person has to do something about: waiting, or a bot could not."""
    try:
        rows = await db_client.tasks_for_organization(organization_id, limit=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read the task board for org {}: {}", organization_id, exc
        )
        return 0
    return sum(1 for t in rows if getattr(t, "status", "") in ("waiting", "could_not"))


async def door_answers(organization_id: int) -> dict[str, str]:
    """What the account said at the door: role and business, if recorded."""
    try:
        rows = await db_client.subject_facts(
            organization_id=organization_id, subject_type=DOOR_SUBJECT, limit=10
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read the door answers for org {}: {}", organization_id, exc
        )
        return {}
    return {str(r.key): str(r.value) for r in rows if getattr(r, "value", None)}


async def remember_door(organization_id: int, *, role: str, business: str) -> None:
    """Keep the door's answers on the account, so Home and Decibyl know
    who they are talking to. Overwrites: the answer is a fact, not a log."""
    facts = {k: v for k, v in (("role", role), ("business", business)) if v}
    if not facts:
        return
    await db_client.remember_organisation_facts(
        organization_id=organization_id,
        facts=facts,
        status="confirmed",
        subject_type=DOOR_SUBJECT,
        subject_key="self",
    )


async def gather(
    organization_id: int,
    *,
    members: list[dict[str, Any]],
    unreturned_missed_calls: int,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """The cards for one account: the reads, then ``build``."""
    if not members:
        door = await door_answers(organization_id)
        return build(
            members=members,
            recent_questions=[],
            business=door.get("business"),
            now=now,
        )
    return build(
        members=members,
        recent_questions=await recent_questions(organization_id),
        unreturned_missed_calls=unreturned_missed_calls,
        stuck_tasks=await stuck_tasks(organization_id),
        now=now,
    )

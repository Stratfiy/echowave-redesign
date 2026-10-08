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

from dataclasses import dataclass
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

#: The card kind of a life-stage starter.
LIFE_STAGE_KIND = "life_stage"

#: The door's roles that name a life stage outright. Small business is read
#: from an owner whose business has no list of its own above.
LIFE_STAGE_BY_ROLE: dict[str, str] = {
    "student": "college_students",
    "senior": "seniors",
    "creator": "creators",
}
SMALL_BUSINESS = "small_business"
SMALL_BUSINESS_ROLES = frozenset({"owner"})


@dataclass(frozen=True)
class Starter:
    """A first card for a life stage: the words sent, the shelf role it is
    for (``agent_templates.life_stages``), and the helper that does that
    role's job in Chat -- None for Automatic, which holds every tool."""

    text: str
    template: str
    helper: Optional[str] = None


#: The life stages the founder chose to come first (8 Oct 2026), four
#: starters each. Every text is something a person would say; every
#: template is on the shelf under the same stage, and every helper holds
#: the tools that template's job is done with (test_life_stage_shelf.py).
LIFE_STAGE_STARTERS: dict[str, tuple[Starter, ...]] = {
    SMALL_BUSINESS: (
        Starter(
            "Who owes me money? Help me chase it politely",
            "money_chaser",
            "follow_up",
        ),
        Starter("Remind me before my GST and TDS dates", "compliance_clock"),
        Starter("Find new customers for my business", "find_customers"),
        Starter(
            "Answer my phone and book appointments",
            "clinic_appointment",
            "call_appointment",
        ),
    ),
    "seniors": (
        Starter("Is this message a scam?", "scam_shield"),
        Starter("Remind me to take my medicine", "medicine_caller"),
        Starter("Help me with my phone, one step at a time", "phone_helper"),
        Starter("Check in with me every morning", "daily_checkin"),
    ),
    "college_students": (
        Starter(
            "Plan my revision back from my exam date",
            "exam_planner",
            "learning_guide",
        ),
        Starter(
            "Explain a doubt from my notes, step by step",
            "doubt_desk",
            "learning_guide",
        ),
        Starter(
            "Quiz me every day on what I studied", "revision_coach", "learning_guide"
        ),
        Starter("Give me a mock interview", "interview_coach", "learning_guide"),
    ),
    "creators": (
        Starter("Plan my content for this week", "content_planner"),
        Starter("Write captions and hooks for my next post", "caption_hook_writer"),
        Starter("Track my brand deals and draft follow-ups", "brand_deal_desk"),
        Starter("Summarise my analytics for this week", "performance_digest"),
    ),
}


def life_stage_for(role: Optional[str], business: Optional[str]) -> Optional[str]:
    """The life stage the door's answers put somebody in, or None.

    A role that names a stage decides it. An owner whose business has its
    own first jobs keeps them -- a clinic owner wants the clinic's jobs, not
    a general list -- and any other owner is a small business.
    """
    role = (role or "").strip().lower()
    business = (business or "").strip().lower()
    if role in LIFE_STAGE_BY_ROLE:
        return LIFE_STAGE_BY_ROLE[role]
    if role in SMALL_BUSINESS_ROLES and business not in FIRST_JOBS_BY_BUSINESS:
        return SMALL_BUSINESS
    return None


def _opener(
    kind: str,
    text: str,
    *,
    helper: Optional[str] = None,
    template: Optional[str] = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {"kind": kind, "text": text}
    if helper:
        card["helper"] = helper
    if template:
        card["template"] = template
    return card


def starters(stage: str) -> list[dict[str, Any]]:
    """The cards for one life stage."""
    return [
        _opener(LIFE_STAGE_KIND, s.text, helper=s.helper, template=s.template)
        for s in LIFE_STAGE_STARTERS.get(stage, ())
    ][:MAX_OPENERS]


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
    role: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """The cards, most personal first, capped at four.

    ``recent_questions`` are the person's own lines to Decibyl, newest
    first. ``members`` are the team rows the home endpoint already has.
    ``role`` and ``business`` are the door's answers; a role that names a
    life stage gets that stage's starters (``life_stage_for``).
    """
    now = now or datetime.now()

    if not members:
        stage = life_stage_for(role, business)
        if stage:
            return starters(stage)
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


async def recent_questions(
    organization_id: int, *, viewer_id: Optional[int] = None
) -> list[str]:
    """The person's own lines to Decibyl, newest first, distinct.

    From the conversation being spoken in, when there is one: inside a turn
    the thread is set and these are the questions asked in that chat, and
    outside one -- the home screen -- it is the original thread, exactly as
    before threads existed.

    With private threads on, a line is offered back only to the person who
    wrote it (``viewer_id``): the original conversation is its author's, and
    a member was being offered the owner's own words as a start card.
    """
    from api import constants
    from api.services.workflow import agent_timeline

    only_author = viewer_id if constants.DECIBYL_PRIVATE_THREADS_ENABLED else None

    try:
        rows = await db_client.agent_events(
            organization_id=organization_id,
            assistant_thread=True,
            thread_id=agent_timeline.current_thread(),
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
        if only_author is not None and str((row.payload or {}).get("author_id")) != str(
            only_author
        ):
            continue
        body = str((row.payload or {}).get("body") or row.summary or "").strip()
        key = body.casefold()
        if body and key not in seen:
            seen.add(key)
            out.append(body)
    return out


async def stuck_tasks(organization_id: int) -> int:
    """Tasks a person has to do something about: blocked, or waiting for review."""
    try:
        rows = await db_client.tasks_for_organization(organization_id, limit=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read the task board for org {}: {}", organization_id, exc
        )
        return 0
    return sum(1 for t in rows if getattr(t, "status", "") in ("blocked", "in_review"))


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
    viewer_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """The cards for one account: the reads, then ``build``. ``viewer_id`` is
    who is looking; with private threads on, only their own lines return."""
    if not members:
        door = await door_answers(organization_id)
        return await route_helpers(
            organization_id,
            build(
                members=members,
                recent_questions=[],
                business=door.get("business"),
                role=door.get("role"),
                now=now,
            ),
        )
    return build(
        members=members,
        recent_questions=await recent_questions(organization_id, viewer_id=viewer_id),
        unreturned_missed_calls=unreturned_missed_calls,
        stuck_tasks=await stuck_tasks(organization_id),
        now=now,
    )


async def route_helpers(
    organization_id: int, cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep a card's helper only where that helper can answer here.

    A turn sent to a helper that is switched off, or that needs setting up,
    is refused (``helpers.states.assert_usable``), and a starter that is
    refused is worse than one answered by Automatic -- which holds every
    tool a helper does. So an unusable helper is dropped from the card, not
    the card from the screen; a usable one carries its name for the chip.
    """
    wanted = {c["helper"] for c in cards if c.get("helper")}
    if not wanted:
        return cards
    usable: dict[str, str] = {}
    try:
        from api.services.helpers import catalogue, states

        if states.enabled(organization_id):
            readings = await states.read(organization_id)
            for key in wanted:
                helper = catalogue.BY_KEY.get(key)
                if (
                    helper is not None
                    and states.evaluate(helper, readings).state == states.AVAILABLE
                ):
                    usable[key] = helper.name
    except Exception as exc:  # noqa: BLE001 - Automatic answers instead
        logger.warning(
            "Could not check helpers for org {}'s starters: {}", organization_id, exc
        )
    out: list[dict[str, Any]] = []
    for card in cards:
        key = card.get("helper")
        if not key:
            out.append(card)
        elif key in usable:
            out.append({**card, "helper_name": usable[key]})
        else:
            out.append({k: v for k, v in card.items() if k != "helper"})
    return out

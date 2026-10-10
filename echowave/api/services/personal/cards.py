"""The person's own memory, as cards on their own thread.

Three views of one row kind (``personal_memory``):

* ``about_me`` -- "What do you know about me?": every preference they
  stated and what Decibyl learned from their own conversations, each with
  where it came from and when, and Correct and Forget on each, in the
  thread (AGENTS.md: never send anybody to another screen).
* ``saved`` -- a preference just saved from their line, with the same two
  buttons, so a misread is put right where it happened.
* ``proposal`` -- a preference Decibyl offers (from their feedback on a
  reply, or suggested in a turn). Nothing is kept until they press Save;
  "No thanks" keeps nothing. Never silent (skills-and-context: "Feedback
  should improve remembered preferences ... Avoid promising that every
  rating automatically trains the model").

**Private.** Every card is written with ``private_to`` the person, so the
timeline returns it to nobody else; and the card carries ids, not values --
it reads the values from the person's own store when it draws, through
routes that check the owner. A proposal carries its one proposed value,
private to its owner the same way.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import delete, select

from api.db import db_client
from api.db.models import OrganisationFactModel
from api.db.settings_models import MemoryFactRevisionModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import personal
from api.services.personal import capture, preferences
from api.services.workflow import agent_timeline

KIND = AgentEventKind.PERSONAL_MEMORY.value
ABOUT_ME = "about_me"
SAVED = "saved"
PROPOSAL = "proposal"

OPEN = "open"
KEPT = "saved"
DISMISSED = "dismissed"

NAME = "Decibyl"

SHOW_TOOL = "show_what_you_know"
PROPOSE_TOOL = "propose_preference"


class CardNotFound(LookupError):
    pass


class CardSettled(ValueError):
    pass


def _payload(view: str, user_id: int, **more: Any) -> dict[str, Any]:
    return {
        "view": view,
        "from": NAME,
        # The timeline returns this row to this person alone
        # (agent_event_client.agent_events, ``private_to``).
        "private_to": user_id,
        "owner_user_id": user_id,
        **more,
    }


async def show_about_me(
    *, organization_id: int, user_id: int, thread_id: str | None
) -> int | None:
    return await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.AGENT.value,
        # The summary lands in the workspace's activity reads: never a
        # preference's own words there.
        summary="What Decibyl keeps about you",
        payload=_payload(ABOUT_ME, user_id),
        in_channel=False,
        thread_id=thread_id,
    )


async def show_saved(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    ids: list[int],
    refused: list[str],
) -> int | None:
    return await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.AGENT.value,
        summary=("Saved to your preferences" if ids else "A preference was not saved"),
        payload=_payload(SAVED, user_id, ids=list(ids), refused=list(refused)),
        in_channel=False,
        thread_id=thread_id,
    )


async def propose(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    candidate: capture.Candidate,
    why: str,
    source_kind: str,
) -> int | None:
    if source_kind not in (preferences.FROM_FEEDBACK, preferences.FROM_SUGGESTION):
        raise ValueError(source_kind)
    return await agent_timeline.record(
        organization_id=organization_id,
        kind=KIND,
        actor=AgentEventActor.AGENT.value,
        summary="A preference to keep, if you want it",
        payload=_payload(
            PROPOSAL,
            user_id,
            state=OPEN,
            source_kind=source_kind,
            why=why[:200],
            proposal={
                "kind": candidate.kind,
                "topic": candidate.topic,
                "value": candidate.value,
                "label": candidate.label,
            },
        ),
        in_channel=False,
        thread_id=thread_id,
    )


async def _own_card(organization_id: int, user_id: int, event_id: int) -> Any:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != KIND:
        raise CardNotFound
    payload = dict(event.payload or {})
    if payload.get("owner_user_id") != user_id:
        raise CardNotFound
    return event


async def settle_proposal(
    *, organization_id: int, user_id: int, event_id: int, accept: bool
) -> dict[str, Any]:
    """Save, or No thanks, on a proposal card -- by its owner only, once."""
    event = await _own_card(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    if payload.get("view") != PROPOSAL:
        raise CardNotFound
    if payload.get("state") != OPEN:
        raise CardSettled("This one is already settled.")
    proposal = payload.get("proposal") or {}
    new = dict(payload, state=KEPT if accept else DISMISSED)
    if accept:
        candidate = capture.Candidate(
            str(proposal.get("kind")),
            str(proposal.get("topic")),
            str(proposal.get("value")),
            str(proposal.get("label")),
            str(payload.get("why") or ""),
        )
        if candidate.kind not in capture.KINDS or candidate.topic not in capture.TOPICS:
            raise CardNotFound
    moved = await db_client.transition_agent_event_payload(
        event_id, organization_id=organization_id, from_state=OPEN, payload=new
    )
    if not moved:
        raise CardSettled("This one is already settled.")
    if accept:
        saved = await preferences.save(
            user_id=user_id,
            organization_id=organization_id,
            candidate=candidate,
            source_kind=str(payload.get("source_kind") or preferences.FROM_SUGGESTION),
            source_event_id=int(event.id),
            source_thread_id=getattr(event, "thread_id", None),
        )
        new["preference_id"] = saved.preference["id"]
        await db_client.set_agent_event_payload(
            event_id, organization_id=organization_id, payload=new
        )
    return {
        "event_id": int(event.id),
        "state": new["state"],
        "preference_id": new.get("preference_id"),
    }


async def card(*, organization_id: int, user_id: int, event_id: int) -> dict[str, Any]:
    """What one card draws, read now from the person's own store."""
    event = await _own_card(organization_id, user_id, event_id)
    payload = dict(event.payload or {})
    view = payload.get("view")
    out: dict[str, Any] = {"event_id": int(event.id), "view": view}
    if view == ABOUT_ME:
        out.update(await about_me(organization_id=organization_id, user_id=user_id))
    elif view == SAVED:
        wanted = [int(i) for i in payload.get("ids") or []]
        rows = []
        for preference_id in wanted:
            try:
                row = await preferences.get(
                    user_id=user_id, preference_id=preference_id
                )
            except preferences.NotFound:
                # Forgotten since: said, never silently dropped.
                rows.append({"id": preference_id, "forgotten": True})
                continue
            rows.append(await _with_history(user_id, row))
        out["preferences"] = rows
        out["refused"] = list(payload.get("refused") or [])
    elif view == PROPOSAL:
        out["proposal"] = payload.get("proposal")
        out["why"] = payload.get("why")
        out["state"] = payload.get("state")
        out["preference_id"] = payload.get("preference_id")
    return out


async def _with_history(user_id: int, row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    if row.get("status") == preferences.SUPERSEDED:
        row["replaced_by_later"] = True
    try:
        row["history"] = await preferences.history(
            user_id=user_id, preference_id=int(row["id"])
        )
    except preferences.NotFound:
        row["history"] = []
    return row


async def about_me(*, organization_id: int, user_id: int) -> dict[str, Any]:
    """Everything kept about this person: their preferences, and what was
    learned from their own conversations (their personal memory in this
    workspace). Nothing of the workspace's and nothing of anybody else's."""
    rows = [await _with_history(user_id, r) for r in await preferences.mine(user_id)]
    async with db_client.async_session() as session:
        facts = list(
            (
                await session.execute(
                    select(OrganisationFactModel)
                    .where(
                        OrganisationFactModel.organization_id == organization_id,
                        OrganisationFactModel.user_id == user_id,
                        OrganisationFactModel.kind == "fact",
                        OrganisationFactModel.status != "rejected",
                    )
                    .order_by(OrganisationFactModel.last_seen_at.desc())
                    .limit(50)
                )
            ).scalars()
        )
    learned = [
        {
            "id": int(f.id),
            "label": f"{f.key}: {f.value}",
            "key": f.key,
            "value": f.value,
            "status": f.status,
            "source": {
                "kind": "call" if f.source_run_id else "conversation",
                "line": (
                    "Learned on a call"
                    if f.source_run_id
                    else "From your conversations with Decibyl"
                ),
            },
            "observed_at": (f.first_seen_at.isoformat() if f.first_seen_at else None),
        }
        for f in facts
    ]
    return {"preferences": rows, "learned": learned}


# --- personal memory facts: correct and forget, owner only --------------------


async def _own_fact(organization_id: int, user_id: int, fact_id: int) -> Any:
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(OrganisationFactModel).where(
                OrganisationFactModel.id == fact_id,
                OrganisationFactModel.organization_id == organization_id,
                # The person's own, never the workspace's: a member cannot
                # rewrite or delete what the workspace has confirmed from here.
                OrganisationFactModel.user_id == user_id,
            )
        )
    if row is None:
        raise CardNotFound
    return row


async def correct_fact(
    *, organization_id: int, user_id: int, fact_id: int, value: str
) -> dict[str, Any]:
    """A correction of something learned about the person. The old value
    is kept in the fact's history (memory_fact_revisions)."""
    from api.services.settings import memory

    row = await _own_fact(organization_id, user_id, fact_id)
    return await memory.edit(
        organization_id=organization_id,
        user_id=user_id,
        fact_id=int(row.id),
        value=value,
        expected_value=row.value,
    )


async def forget_fact(*, organization_id: int, user_id: int, fact_id: int) -> None:
    """Delete something learned about the person, and its history."""
    row = await _own_fact(organization_id, user_id, fact_id)
    async with db_client.async_session() as session:
        await session.execute(
            delete(MemoryFactRevisionModel).where(
                MemoryFactRevisionModel.organization_id == organization_id,
                MemoryFactRevisionModel.fact_id == int(row.id),
            )
        )
        await session.execute(
            delete(OrganisationFactModel).where(
                OrganisationFactModel.id == int(row.id),
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.user_id == user_id,
            )
        )
        await session.commit()


# --- feedback -> a preference, offered ------------------------------------------

#: Unicode blocks of the scripts people here write in -> the language tag.
_SCRIPTS = (
    (0x0B80, 0x0BFF, "ta-IN"),
    (0x0900, 0x097F, "hi-IN"),
    (0x0980, 0x09FF, "bn-IN"),
    (0x0C00, 0x0C7F, "te-IN"),
    (0x0C80, 0x0CFF, "kn-IN"),
    (0x0D00, 0x0D7F, "ml-IN"),
    (0x0A80, 0x0AFF, "gu-IN"),
    (0x0A00, 0x0A7F, "pa-IN"),
    (0x0B00, 0x0B7F, "od-IN"),
)


def written_in(text: str) -> str | None:
    """The language a line is written in, by its script: a tag, or None
    when it has too few letters to say."""
    counts: dict[str, int] = {}
    latin = 0
    for ch in text or "":
        code = ord(ch)
        if ch.isascii() and ch.isalpha():
            latin += 1
            continue
        for low, high, tag in _SCRIPTS:
            if low <= code <= high:
                counts[tag] = counts.get(tag, 0) + 1
                break
    if counts:
        tag, count = max(counts.items(), key=lambda kv: kv[1])
        if count >= 3:
            return tag
    return "en-IN" if latin >= 6 else None


async def _asked_before(organization_id: int, reply: Any) -> str:
    """The person's line the reply answered: the newest of theirs before it
    on the same thread."""
    try:
        rows = await db_client.agent_events(
            organization_id=organization_id,
            assistant_thread=True,
            thread_id=getattr(reply, "thread_id", None),
            kinds=[AgentEventKind.MESSAGE.value],
            limit=20,
            before_at=reply.at,
            before_id=reply.id,
        )
    except Exception as exc:  # noqa: BLE001 - a proposal is optional
        logger.warning("Could not read the line a reply answered: {}", exc)
        return ""
    for row in rows:
        if row.actor == AgentEventActor.HUMAN.value:
            return str((row.payload or {}).get("body") or row.summary or "")
    return ""


async def from_feedback(
    *,
    organization_id: int,
    user_id: int,
    reply_event_id: int,
    reasons: list[str],
) -> int | None:
    """Offer a preference a "Not quite" points at, as a card under the
    reply's thread. Only for reasons that are about the person's taste --
    too long, wrong language -- never for "wrong" or "not relevant", which
    say nothing about what the person prefers. Nothing is kept until they
    press Save. Returns the card's id, or None when there is nothing to
    offer (flag off, no such reason, or it is already what they have)."""
    if not personal.enabled(organization_id):
        return None
    reply = await db_client.get_agent_event(
        reply_event_id, organization_id=organization_id
    )
    if reply is None:
        return None
    candidate: capture.Candidate | None = None
    if "wrong_language" in reasons:
        tag = written_in(await _asked_before(organization_id, reply))
        name = capture.language_name(tag) if tag else None
        if tag and name:
            candidate = capture.Candidate(
                capture.LANGUAGE, capture.CHAT, tag, f"Replies in {name}"
            )
    if candidate is None and "too_long" in reasons:
        candidate = capture.Candidate(
            capture.LENGTH, capture.CHAT, "short", "Short replies"
        )
    if candidate is None:
        return None
    live = await preferences.mine(user_id)
    if any(
        r["kind"] == candidate.kind
        and r["topic"] == candidate.topic
        and r["value"] == candidate.value
        for r in live
    ):
        return None
    return await propose(
        organization_id=organization_id,
        user_id=user_id,
        thread_id=getattr(reply, "thread_id", None),
        candidate=candidate,
        why="From your feedback on a reply",
        source_kind=preferences.FROM_FEEDBACK,
    )


# --- the model's two tools ----------------------------------------------------


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": SHOW_TOOL,
            "description": (
                "Show the person, on a card in this conversation, every "
                "preference they have told you and what was learned from "
                "their own conversations, with where each came from and "
                "Correct and Forget on each. Use when they ask what you "
                "know or remember about them."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": PROPOSE_TOOL,
            "description": (
                "Offer the person a preference to keep, on a card they "
                "accept or not. Nothing is kept unless they press Save. "
                "Only for how they like things done: a language for "
                "calls, email, chat or reminders; when calls may ring; "
                "the channel for reminders; how often to report numbers; "
                "short or detailed replies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            capture.LANGUAGE,
                            capture.CALL_WINDOW,
                            capture.CHANNEL,
                            capture.CADENCE,
                            capture.LENGTH,
                        ],
                    },
                    "topic": {"type": "string", "enum": list(capture.TOPICS)},
                    "value": {
                        "type": "string",
                        "description": (
                            "In plain words: a language name, a time like "
                            "10:30, whatsapp/app/notification, "
                            "daily/weekly/monthly, short/detailed."
                        ),
                    },
                    "why": {
                        "type": "string",
                        "description": "One short line on why you offer it.",
                    },
                },
                "required": ["kind", "topic", "value"],
            },
        },
    ]


async def for_thread(
    organization_id: int,
    name: str,
    arguments: dict[str, Any],
    *,
    user_id: int | None,
    thread_id: str | None,
) -> dict[str, Any]:
    """Run one of the two tools in a Decibyl turn."""
    if not user_id or not personal.enabled(organization_id):
        return {"error": "Not available here."}
    if name == SHOW_TOOL:
        await show_about_me(
            organization_id=organization_id, user_id=user_id, thread_id=thread_id
        )
        return {
            "shown": True,
            "note": "The card is on screen with Correct and Forget on each item. "
            "Say so in one line; do not list the items again.",
        }
    kind = str(arguments.get("kind") or "")
    topic = str(arguments.get("topic") or "")
    if kind not in capture.KINDS or kind == capture.NOTE or topic not in capture.TOPICS:
        return {"error": "That is not a preference that can be kept."}
    got = capture.read_for(kind, topic, str(arguments.get("value") or ""))
    if isinstance(got, str):
        return {"error": got}
    if got is None or got.topic != topic:
        return {"error": "That value could not be read as that preference."}
    event_id = await propose(
        organization_id=organization_id,
        user_id=user_id,
        thread_id=thread_id,
        candidate=got,
        why=str(arguments.get("why") or "Suggested in this conversation")[:200],
        source_kind=preferences.FROM_SUGGESTION,
    )
    return {
        "proposed": got.label,
        "card": event_id,
        "note": "It is a card the person accepts or not. Nothing is saved yet.",
    }

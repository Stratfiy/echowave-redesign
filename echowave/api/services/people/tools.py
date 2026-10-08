"""People in Chat: Decibyl can look someone up by name.

"What did I last discuss with Ravi?", "draft a reply to Priya": the turn
reads the asking person's **own** contacts -- never a colleague's, and never
when nobody is signed in to the turn. Two ways in, both through ``store``:

* ``context_block`` -- before the model answers, any of the person's
  contacts named in the question is put in the context with its brief and
  last few interactions, so the common question needs no tool call.
* ``lookup_person`` -- a read the model can call with a name. Numbers and
  addresses come back masked unless it asks for them to draft or send.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.people_models import PersonModel
from api.services.people import enabled, normalise, store

TOOL_NAME = "lookup_person"
NAMES = frozenset({TOOL_NAME})
READS = NAMES
MAX_MATCHES = 3
MAX_CONTEXT_PEOPLE = 3

RULES = (
    "\nPeople (the asking person's own contacts, private to them):\n"
    f"- {TOOL_NAME}: when they ask about someone by name -- what they last "
    "discussed, what is open, or to draft a message or call them -- look the "
    "person up first and answer from the brief and the dated interactions. "
    "Say when you are not sure it is the right person. Ask for the full "
    "number or address (include_contact_details) only to draft or send. "
    "These are the person's own notes: do not repeat them to anyone else.\n"
)


def rules(organization_id: int | None) -> str:
    return RULES if enabled(organization_id) else ""


def schemas(organization_id: int | None) -> list[dict[str, Any]]:
    if not enabled(organization_id):
        return []
    return [
        {
            "name": TOOL_NAME,
            "description": (
                "Look up one of the asking person's own contacts by name. Returns "
                "who they are to them (the brief), company and the last few "
                "interactions (channel, date, one line)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name as the person said it.",
                    },
                    "include_contact_details": {
                        "type": "boolean",
                        "description": "Full numbers and addresses, only to draft or send.",
                    },
                },
                "required": ["name"],
            },
        }
    ]


async def describe(
    person: PersonModel, *, details: bool, recent: int = 5
) -> dict[str, Any]:
    items = await store.interactions(person, limit=recent)
    phones = list(person.phones or [])
    emails = list(person.emails or [])
    return {
        "name": person.name,
        "company": person.company,
        "relation": person.relation,
        "brief": person.brief,
        "phones": phones if details else [normalise.mask(p) for p in phones],
        "emails": emails if details else [normalise.mask(e) for e in emails],
        "last_interactions": [
            {
                "channel": i.channel,
                "direction": i.direction,
                "when": i.at.isoformat() if i.at else None,
                "line": i.line,
            }
            for i in items
        ],
    }


async def run(
    organization_id: int, *, user_id: int | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    if not enabled(organization_id):
        return {"status": "unavailable", "reason": "People is switched off here."}
    if not user_id:
        return {
            "status": "unavailable",
            "reason": "Contacts are a person's own; nobody is signed in to this turn.",
        }
    name = normalise.text(arguments.get("name"), 120) or ""
    matches = await store.find_by_name(
        organization_id, int(user_id), name, limit=MAX_MATCHES
    )
    if not matches:
        return {
            "status": "not_found",
            "reason": f"No contact called {name!r} in their People.",
        }
    details = bool(arguments.get("include_contact_details"))
    return {
        "status": "ok",
        "private": "These are the asking person's own contacts and notes.",
        "people": [await describe(p, details=details) for p in matches],
    }


async def _named_in(
    organization_id: int, user_id: int, question: str
) -> list[PersonModel]:
    """The person's contacts named in the question: a full name, or a first
    name only one contact has."""
    words = set(re.findall(r"[^\W\d_]{3,}", question.casefold()))
    if not words:
        return []
    text = " " + re.sub(r"\s+", " ", question.casefold()) + " "
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(PersonModel.id, PersonModel.name)
                .where(
                    PersonModel.organization_id == organization_id,
                    PersonModel.owner_user_id == user_id,
                )
                .limit(5000)
            )
        ).all()
    full: list[int] = []
    by_first: dict[str, list[int]] = {}
    for person_id, name in rows:
        key = normalise.name_key(name)
        if len(key) >= 3 and f" {key} " in text:
            full.append(person_id)
            continue
        first = key.split(" ")[0] if key else ""
        if first in words:
            by_first.setdefault(first, []).append(person_id)
    ids = full + [ids[0] for ids in by_first.values() if len(ids) == 1]
    if not ids:
        return []
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(PersonModel).where(
                        PersonModel.id.in_(ids[:MAX_CONTEXT_PEOPLE]),
                        PersonModel.organization_id == organization_id,
                        PersonModel.owner_user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )


async def context_block(
    organization_id: int, user_id: int | None, question: str
) -> str:
    """A section for the turn's context, or "" when nobody is named."""
    if not user_id or not enabled(organization_id) or not question:
        return ""
    try:
        people = await _named_in(organization_id, int(user_id), question)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("People could not read contacts for the turn: {}", exc)
        return ""
    if not people:
        return ""
    lines = ["## People they named (their own contacts; private to them)"]
    for person in people:
        about = await describe(person, details=False, recent=3)
        head = person.name + (f", {person.company}" if person.company else "")
        lines.append(f"- {head}: {about['brief'] or 'no brief yet.'}")
        for item in about["last_interactions"]:
            lines.append(
                f"  - {(item['when'] or '')[:10]} {item['channel']}: {item['line']}"
            )
    return "\n".join(lines)


async def brief_for_agent(
    organization_id: int,
    user_id: int | None,
    *,
    phone: str | None = None,
    email: str | None = None,
) -> str | None:
    """The brief an outreach or voice agent may read before it calls or
    writes to someone for this person -- only when they allowed it in People
    settings, only their own contact, and only the brief."""
    if not user_id or not enabled(organization_id):
        return None
    try:
        if not await store.agents_may_read(organization_id, int(user_id)):
            return None
        person = None
        number = normalise.phone(phone)
        if number:
            person = await store.find_by_handle(
                organization_id, int(user_id), "phone", number
            )
        address = normalise.email(email)
        if person is None and address:
            person = await store.find_by_handle(
                organization_id, int(user_id), "email", address
            )
        return person.brief if person and person.brief else None
    except Exception as exc:  # noqa: BLE001 - a call goes ahead without it
        logger.warning("Could not read a brief for an agent: {}", exc)
        return None

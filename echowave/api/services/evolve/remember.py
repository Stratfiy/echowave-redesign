"""'Remember this as my way of doing it': a conversation becomes a skill draft.

skills-and-context.md: "Turning a successful process into a reusable skill
should produce an editable draft for approval." The draft has the fields a
skill card explains -- what it is for and an example, when to use it, the
steps, what it needs, what it produces, what it will not do -- and it is the
person's own until they publish it: nobody else sees the card, and nothing
reaches an agent until they press Save.

The draft comes from the conversation the person is in, read through the
same timeline filters they read it through (their own thread, nobody
else's private rows). Anything the model drafts about who may be contacted,
spending, calling hours or permissions is taken out and listed on the card
(``guard.strip``): those are Decibyl's own rules, not part of a skill.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services import evolve
from api.services.evolve import guard, model, versions

#: How much of the conversation the draft is written from.
MAX_TRANSCRIPT_CHARS = 8_000
MAX_MESSAGES = 40

SYSTEM = (
    "A person wants the way they just did something remembered as a "
    "reusable skill. From the conversation, write it as JSON only: "
    '{"title": "short name", "description": "what it helps accomplish", '
    '"example": "one sentence example of using it", "when_to_use": "...", '
    '"steps": ["..."], "inputs": ["what information or app access it needs"], '
    '"outputs": ["what it produces"], "wont_do": ["what it will not do"]}. '
    "Use the person's own way of doing it, not a generic one. Leave out who "
    "may be contacted, spending, calling hours and permissions: those are "
    "set elsewhere."
)


def _line(row: Any) -> str:
    body = (row.payload or {}).get("body") or row.summary or ""
    who = "Person" if row.actor == AgentEventActor.HUMAN.value else "Assistant"
    return f"{who}: {str(body).strip()}"


async def transcript(
    organization_id: int,
    user_id: int,
    *,
    workflow_id: int | None = None,
    thread_id: str | None = None,
) -> str:
    rows = await db_client.agent_events(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kinds=[AgentEventKind.MESSAGE.value],
        assistant_thread=workflow_id is None,
        thread_id=thread_id,
        viewer_id=user_id,
        limit=MAX_MESSAGES,
    )
    lines = [_line(r) for r in reversed(rows)]
    text = "\n".join(lines)
    return text[-MAX_TRANSCRIPT_CHARS:]


async def draft(
    *,
    organization_id: int,
    user_id: int,
    workflow_id: int | None = None,
    thread_id: str | None = None,
    title_hint: str = "",
) -> dict[str, Any]:
    """Write the draft and post its card. Returns what the model is told."""
    if not evolve.enabled(organization_id):
        return {"status": "unavailable", "reason": "not switched on here"}
    if workflow_id is not None:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=organization_id
        )
        if workflow is None:
            return {"status": "not_proposed", "reason": "That agent is not here."}
    said = await transcript(
        organization_id, user_id, workflow_id=workflow_id, thread_id=thread_id
    )
    if said.count("\n") < 1:
        return {
            "status": "not_proposed",
            "reason": "There is not enough in this conversation to remember yet.",
        }
    spend = model.Spend()
    try:
        parsed = await model.ask_json(
            organization_id,
            system=SYSTEM,
            user=(f"Call it: {title_hint}\n\n" if title_hint else "") + said,
            spend=spend,
        )
    except model.ModelUnavailable:
        return {
            "status": "not_proposed",
            "reason": "I could not write the draft just now.",
        }
    if title_hint and not parsed.get("title"):
        parsed["title"] = title_hint
    content, removed = guard.strip(
        {k: parsed.get(k) for k in guard.FIELDS if k != guard.LESSONS and k in parsed}
    )
    if not content.get("steps") or not content.get("title"):
        return {
            "status": "not_proposed",
            "reason": "I could not find the steps in this conversation. Say them and ask again.",
        }
    row = await versions.create(
        organization_id=organization_id,
        slug=versions.own_slug(content["title"]),
        content=content,
        origin=evolve.ORIGIN_REMEMBERED,
        status=evolve.DRAFT,
        cost=spend.as_dict(),
        workflow_id=workflow_id,
        thread_id=thread_id if workflow_id is None else None,
        author_user_id=user_id,
        owner_user_id=user_id,
    )
    await versions.post_card(
        organization_id,
        row,
        versions.CARD_REMEMBERED,
        extra={"left_out": [v.sentence() for v in removed]},
    )
    return {
        "status": "proposed",
        "version_id": row.id,
        "note": (
            f"The draft '{content['title']}' is on a card in this thread. The "
            "person can edit it and save it as a skill. Tell them, then end "
            "your reply."
        ),
    }


__all__ = ["SYSTEM", "draft", "transcript"]

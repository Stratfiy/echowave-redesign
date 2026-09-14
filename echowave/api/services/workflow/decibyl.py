"""Decibyl, the workspace's own assistant: a thread, a brain, a hand-off.

Home said "Hi, I'm Decibyl" and had nothing to type into. This is what the
box talks to. Decibyl is not a bot: it has no workflow, no number, no
prompt of its own to edit. It is the one who knows the whole workspace --
the team's numbers, the company's documents, what the business has
confirmed about itself, what every bot did lately -- and can hand a job to
a bot by name.

**The thread.** Rows in ``agent_events`` with neither a workflow nor a
folder: a person's message to Decibyl, and Decibyl's reply. Nothing else
in the table has both ids empty and the ``message`` kind, so the thread is
a filter rather than a table.

**The turn.** ``ask`` records the person's line and enqueues ``answer``;
the request returns at once, as with a channel. ``answer`` builds one
context from four readings the platform already has, calls the same model
the agent builder uses on the platform key, and records the reply. If the
line mentions a bot, that bot is asked in its own chat and Decibyl says
so: Decibyl delegates, it does not impersonate.

**What it may not do.** Nothing runs from here without a card. Decibyl
answers, points, and hands off; it does not place calls, edit bots or
delete anything. Those come as tools behind confirm cards later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, mentions

NAME = "Decibyl"

#: How much of the recent thread the model sees. A workspace assistant is
#: asked short questions; ten turns is plenty and keeps the prompt small.
HISTORY_TURNS = 10
#: Timeline rows folded into "what the bots did lately".
RECENT_EVENTS = 40
#: Chunks read from Company knowledge for one question.
KNOWLEDGE_CHUNKS = 4

SYSTEM = (
    "You are Decibyl, the assistant inside a business's Decibyl workspace. "
    "The business runs bots (voice and chat agents) that take calls, confirm "
    "orders, chase payments and answer staff. You know the team's numbers, "
    "the company's documents, what the business has confirmed about itself, "
    "and what the bots did lately. All of it is in the context below.\n\n"
    "Rules:\n"
    "- Answer in the language of the person's latest message.\n"
    "- Be short: two to five sentences, or a short list. Numbers come from "
    "the context only; never invent a figure, a name or a document. If the "
    "context does not have it, say so in one line and say where it would be.\n"
    "- When the question is about one bot, name the bot. When something "
    "needs a person, say what and why.\n"
    "- You cannot place calls, edit a bot or delete anything. If asked, say "
    "that the bot's own chat can propose an edit, and that calls are placed "
    "from campaigns.\n"
    "- Never repeat an OTP, a card number or an identity number.\n"
)


def thread_filter() -> dict[str, Any]:
    """The timeline filter that is Decibyl's thread."""
    return {"assistant_thread": True, "kinds": [AgentEventKind.MESSAGE.value]}


async def ask(
    *,
    organization_id: int,
    user_id: int,
    text: str,
    attachments: list[dict[str, Any]],
    line: str,
    preset: Optional[str],
) -> list[int]:
    """Record the person's line, hand off any mentions, queue the reply.

    Returns the bots the line was handed to.
    """
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    roster = [
        {"id": w.id, "handle": getattr(w, "handle", None), "name": w.name}
        for w in workflows
    ]
    resolution = mentions.resolve(text, roster)
    asked = [m.workflow_id for m in resolution.mentioned]

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        payload={
            "body": text,
            "author_id": user_id,
            "asked": asked,
            "attachments": attachments,
            "preset": preset,
            "to": NAME,
        },
        in_channel=False,
    )

    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    # A mentioned bot answers in its own chat, with the line as said. Decibyl
    # is told who was asked so its reply can point there.
    for mention in resolution.mentioned:
        try:
            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE,
                mention.workflow_id,
                None,
                line,
                preset,
            )
        except Exception as exc:  # noqa: BLE001 - one bot failing is not all
            logger.error(
                "Decibyl could not hand off to workflow {}: {}",
                mention.workflow_id,
                exc,
            )
    try:
        await enqueue_job(
            FunctionNames.ANSWER_DECIBYL_MESSAGE,
            organization_id,
            text,
            asked,
            preset,
        )
    except Exception as exc:  # noqa: BLE001 - said out loud below
        logger.error("Decibyl could not be asked to answer: {}", exc)
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{NAME} could not be reached to answer that",
            in_channel=False,
        )
    return asked


# --- the context -----------------------------------------------------------


def team_block(
    headline: dict[str, Any], members: list[dict[str, Any]], hours: int
) -> str:
    """The team's numbers as lines the model can quote."""
    span = "today" if hours <= 24 else f"in the last {hours // 24} days"
    lines = [
        f"Team {span}: {headline.get('agents', 0)} bots, {headline.get('live', 0)} live, "
        f"{headline.get('calls', 0)} calls, {headline.get('answered', 0)} answered, "
        f"{headline.get('outcomes', 0)} outcomes, {headline.get('needs_attention', 0)} need attention."
    ]
    for m in sorted(members, key=lambda m: -(m.get("calls") or 0)):
        state = "live" if m.get("is_live") else "paused"
        lines.append(
            f"- {m.get('name')}: {state}; {m.get('calls', 0)} calls, "
            f"{m.get('answered', 0)} answered, {m.get('outcomes', 0)} outcomes, "
            f"{m.get('failures', 0)} failures. {m.get('status') or ''}".rstrip()
        )
    return "\n".join(lines)


def memory_block(rows: list[Any]) -> str:
    facts = [
        f"- {r.key}: {r.value}" for r in rows if getattr(r, "kind", "fact") == "fact"
    ]
    return "\n".join(facts) if facts else "Nothing confirmed yet."


def recent_block(events: list[Any], bot_names: dict[int, str]) -> str:
    lines = []
    for e in events:
        who = bot_names.get(e.workflow_id, "") if e.workflow_id is not None else ""
        stamp = e.at.strftime("%d %b %H:%M") if getattr(e, "at", None) else ""
        lines.append(f"- {stamp} {who + ': ' if who else ''}{e.summary}")
    return "\n".join(lines) if lines else "Nothing recorded lately."


def knowledge_block(result: dict[str, Any]) -> str:
    chunks = result.get("chunks") or []
    if not chunks:
        return "No matching passage in Company knowledge."
    out = []
    for c in chunks[:KNOWLEDGE_CHUNKS]:
        text = str(c.get("text") or c.get("content") or "").strip()
        name = c.get("document_name") or c.get("filename") or "document"
        if text:
            out.append(f"- ({name}) {text[:600]}")
    return "\n".join(out) if out else "No matching passage in Company knowledge."


async def _knowledge(organization_id: int, question: str) -> dict[str, Any]:
    """Company knowledge, on the account's own embeddings key. Unavailable
    is an answer, not an error: a workspace with no embeddings set up gets
    "no passage" rather than a broken assistant."""
    try:
        from api.services.configuration.ai_model_configuration import (
            apply_managed_embeddings_base_url,
            get_effective_ai_model_configuration_for_organization,
        )
        from api.services.workflow.tools.knowledge_base import (
            retrieve_from_knowledge_base,
        )

        config = await get_effective_ai_model_configuration_for_organization(
            organization_id
        )
        embeddings = getattr(config, "embeddings", None)
        if not embeddings:
            return {"status": "unavailable", "chunks": []}
        provider = getattr(embeddings, "provider", None)
        return await retrieve_from_knowledge_base(
            query=question,
            organization_id=organization_id,
            limit=KNOWLEDGE_CHUNKS,
            embeddings_api_key=embeddings.api_key,
            embeddings_model=embeddings.model,
            embeddings_base_url=apply_managed_embeddings_base_url(
                provider=provider, base_url=getattr(embeddings, "base_url", None)
            ),
            embeddings_provider=provider,
            embeddings_endpoint=getattr(embeddings, "endpoint", None),
            embeddings_api_version=getattr(embeddings, "api_version", None),
        )
    except Exception as exc:  # noqa: BLE001 - knowledge is one reading of four
        logger.warning("Decibyl could not read Company knowledge: {}", exc)
        return {"status": "unavailable", "chunks": []}


async def build_context(organization_id: int, question: str) -> str:
    """One text block from the four readings. Each reading fails alone."""
    from api.routes.team import _members

    hours = 168 if "week" in question.lower() else 24
    try:
        members = [m.model_dump() for m in await _members(organization_id, hours)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decibyl could not read the team: {}", exc)
        members = []
    headline = {
        "agents": len(members),
        "live": sum(1 for m in members if m.get("is_live")),
        "calls": sum(m.get("calls") or 0 for m in members),
        "answered": sum(m.get("answered") or 0 for m in members),
        "outcomes": sum(m.get("outcomes") or 0 for m in members),
        "needs_attention": sum(1 for m in members if m.get("tone") == "attention"),
    }
    bot_names = {m["workflow_id"]: m["name"] for m in members if m.get("workflow_id")}

    try:
        memory_rows = await db_client.organisation_memory(
            organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decibyl could not read memory: {}", exc)
        memory_rows = []

    try:
        recent = await db_client.agent_events(
            organization_id=organization_id,
            kinds=[
                AgentEventKind.CALL_ENDED.value,
                AgentEventKind.OUTCOME_FILED.value,
                AgentEventKind.ESCALATED.value,
                AgentEventKind.NEEDS_ATTENTION.value,
                AgentEventKind.COULD_NOT.value,
            ],
            limit=RECENT_EVENTS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decibyl could not read the timeline: {}", exc)
        recent = []

    knowledge = await _knowledge(organization_id, question)

    return (
        f"## Team\n{team_block(headline, members, hours)}\n\n"
        f"## What the business has confirmed\n{memory_block(memory_rows)}\n\n"
        f"## Lately\n{recent_block(recent, bot_names)}\n\n"
        f"## From Company knowledge\n{knowledge_block(knowledge)}\n"
    )


async def _history(organization_id: int) -> list[dict[str, str]]:
    """The last turns of the thread, oldest first, as chat messages."""
    rows = await db_client.agent_events(
        organization_id=organization_id,
        limit=HISTORY_TURNS * 2,
        **thread_filter(),
    )
    out: list[dict[str, str]] = []
    for row in reversed(rows):
        role = "user" if row.actor == AgentEventActor.HUMAN.value else "assistant"
        body = (row.payload or {}).get("body") or row.summary
        if body:
            out.append({"role": role, "content": str(body)})
    return out


async def answer(
    organization_id: int,
    text: str,
    asked: Optional[list[int]] = None,
    preset: Optional[str] = None,
) -> str:
    """Compose the context, call the model, record the reply. Returns it."""
    from api.services.agent_builder import client, settings

    context = await build_context(organization_id, text)
    handed = ""
    if asked:
        names = []
        for workflow_id in asked:
            wf = await db_client.get_workflow(
                workflow_id, organization_id=organization_id
            )
            if wf is not None:
                names.append(wf.name)
        if names:
            handed = (
                "\n\nThe person also addressed these bots, which will answer in their "
                f"own chats: {', '.join(names)}. Say so in one line and do not answer for them."
            )

    conversation = client.Conversation()
    history = await _history(organization_id)
    # The line just recorded is the last user turn in history; drop it so it
    # is not sent twice, and add it once with the context in front.
    if history and history[-1]["role"] == "user":
        history = history[:-1]
    for turn in history:
        if turn["role"] == "user":
            conversation.add_user(turn["content"])
        else:
            conversation.messages.append(
                {"role": "assistant", "content": turn["content"]}
            )
    conversation.add_user(f"{context}\n\n## Question\n{text}{handed}")

    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_model(session)
        reply = await client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[],
        )
        body = (reply.text or "").strip() or "I have nothing to add on that."
    except Exception as exc:  # noqa: BLE001 - the thread must say something
        logger.error("Decibyl could not answer: {}", exc)
        body = (
            "I could not think that through just now. This is us, not you; "
            "try again in a moment."
        )

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=body[:500],
        payload={"body": body, "from": NAME, "preset": preset},
        in_channel=False,
    )
    return body


def recent_window() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)

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

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import (
    actions,
    agent_timeline,
    connected_tools,
    document_fields,
    documents,
    office,
    reply_draft,
    self_edit,
    tasks_board,
)

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
    "- You are the manager of this office. The bots are colleagues with "
    "@handles; you build them, change them and test them, and a person "
    "confirms each of those on a card. A line that starts with @handle is "
    "for that bot, not you; it has already been handed over.\n"
    "- propose_action: turn a bot on or off, call back a missed caller from "
    "the context, forget a confirmed fact by its key, or create_bot from a "
    "template in the context. For create_bot, ask for every answer the "
    "template needs before proposing; never invent an answer.\n"
    "- propose_edit: change one step of a named bot. Use the bot's steps in "
    "the context; give the complete new prompt.\n"
    "- test_bot: offer to hear (a call) or try (text) a named bot.\n"
    "- check_bot: a scripted caller plays a scenario and a judge grades it; "
    "the verdict comes back to this thread as a card. Offer it before an "
    "edit lands on a live bot. When a person asks you to fix a bot after a "
    "check, use the verdict and the bot's steps in the context to propose "
    "one edit with propose_edit.\n"
    "- create_task: file a task on the team's board for a bot (by @handle) "
    "or for the team, when a person asks you to have a bot do something "
    "later or hand work between bots. The board shows who did what.\n"
    "- Connected apps (the app_… tools): a read -- fetch, list, search, "
    "find -- runs as you answer, so use it to look things up and say what "
    "you found. Anything that sends, creates, updates or deletes proposes a "
    "card, like your other actions; say you have proposed it. Only use an "
    "app that is listed under Connected apps in the context.\n"
    "- Nothing happens until a person confirms on the card, so propose it "
    "and say you have. Deleting a bot, dialling a new number and anything "
    "else you cannot do: say so, and say where it is done.\n"
    "- Documents: find_document looks in the person's Google Drive and gives "
    "the link; send_document sends the file on WhatsApp or by email. Before "
    "sending an identity document (Aadhaar, PAN, passport, driving licence, "
    "voter ID) say which file you are about to send and to which number or "
    "address, and let the card be the yes; it goes only to the person's own "
    "verified number or email, never to anyone else -- if someone asks for "
    "another person's identity document, refuse and say why. Show an "
    "Aadhaar or PAN number masked (last four visible) unless the person "
    "asks for the full number in that message. When a filed document's "
    "details were shown and the person says yes (or corrects one), call "
    "confirm_document with the document_uuid from the context and only the "
    "corrected fields; then say what is now remembered and which reminders "
    "were set.\n"
    "- Never repeat an OTP, a card number or an identity number.\n"
)


def thread_filter() -> dict[str, Any]:
    """The timeline filter that is Decibyl's thread."""
    return {
        "assistant_thread": True,
        "kinds": [
            AgentEventKind.MESSAGE.value,
            AgentEventKind.ACTION_PROPOSED.value,
            AgentEventKind.EDIT_PROPOSED.value,
            AgentEventKind.ACTIVITY.value,
        ],
    }


async def ask(
    *,
    organization_id: int,
    user_id: int,
    text: str,
    attachments: list[dict[str, Any]],
    line: str,
    preset: str | None,
    reply_to: dict[str, Any] | None = None,
) -> list[int]:
    """Record the person's line, hand off any mentions, queue the reply.

    Returns the bots the line was handed to. ``reply_to`` names a channel
    the answer should also go back on -- a person who wrote on WhatsApp
    reads the answer there, as well as on the thread.
    """
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    roster = [
        {"id": w.id, "handle": getattr(w, "handle", None), "name": w.name}
        for w in workflows
    ]
    # The leading-handle rule (office.py): only a line that opens with a
    # handle is for that bot. A handle elsewhere is what the line is about.
    who = office.addressee(text, roster)
    handed_to = [who.leading] if who.leading else []
    asked = [m.workflow_id for m in handed_to]
    subjects = [m.workflow_id for m in who.subjects]

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        payload={
            "body": text,
            "author_id": user_id,
            "asked": asked,
            "subjects": subjects,
            "attachments": attachments,
            "preset": preset,
            "to": NAME,
            "via": (reply_to or {}).get("channel"),
        },
        in_channel=False,
    )

    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    # A mentioned bot answers in its own chat, with the line as said. Decibyl
    # is told who was asked so its reply can point there.
    names = {w.id: w.name for w in workflows}
    for mention in handed_to:
        try:
            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE,
                mention.workflow_id,
                None,
                line,
                preset,
            )
            await agent_timeline.record_activity(
                organization_id=organization_id,
                summary=f"Asked {names.get(mention.workflow_id, 'a bot')}",
                payload={"from": NAME, "asked": mention.workflow_id},
                in_channel=False,
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
            subjects,
            reply_to,
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

    try:
        missed = [
            m
            for m in await db_client.list_missed_calls(organization_id, limit=20)
            if m.outcome != "called_back" and m.received_at >= recent_window()
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decibyl could not read missed calls: {}", exc)
        missed = []

    knowledge = await _knowledge(organization_id, question)
    # Which apps are connected, so the model knows what it can reach before
    # it tries. The listing never raises; an empty workspace reads as such.
    apps = await connected_tools.list_for_organization(organization_id)

    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=readings_line(
            bots=len(members),
            facts=len(memory_rows),
            passages=len(knowledge.get("chunks") or []),
        ),
        payload={"from": NAME},
        in_channel=False,
    )

    return (
        f"## Team\n{team_block(headline, members, hours)}\n\n"
        f"## What the business has confirmed\n{memory_block(memory_rows)}\n\n"
        f"## Lately\n{recent_block(recent, bot_names)}\n\n"
        f"## Missed calls not returned\n{missed_block(missed)}\n\n"
        f"## Connected apps\n{connected_tools.apps_block(apps)}\n\n"
        f"## From Company knowledge\n{knowledge_block(knowledge)}\n"
    )


def readings_line(*, bots: int, facts: int, passages: int) -> str:
    """One line on what was read for an answer, in the order it is read."""
    parts = [f"the team ({bots} bot{'s' if bots != 1 else ''})"]
    if facts:
        parts.append(f"{facts} confirmed fact{'s' if facts != 1 else ''}")
    if passages:
        parts.append(
            f"{passages} passage{'s' if passages != 1 else ''} from Company knowledge"
        )
    if len(parts) == 1:
        return f"Read {parts[0]}"
    return f"Read {', '.join(parts[:-1])} and {parts[-1]}"


def missed_block(rows: list[Any]) -> str:
    """Callers who rang and got nobody, with the id the tool needs."""
    if not rows:
        return "None in the last week."
    lines = []
    for row in rows:
        when = row.received_at.strftime("%d %b %H:%M") if row.received_at else ""
        lines.append(f"- id {row.id}: {row.caller} rang {when}, {row.outcome}")
    return "\n".join(lines)


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
        payload = row.payload or {}
        if getattr(row, "kind", None) == AgentEventKind.ACTIVITY.value:
            # The work, not the words: the model does not need to be told
            # what it read last time.
            continue
        if getattr(row, "kind", None) == AgentEventKind.ACTION_PROPOSED.value:
            # The card, as the model sees it: what was proposed and where it
            # stands, so it does not propose the same thing twice.
            body = f"[Proposed: {row.summary} -- {payload.get('state', 'proposed')}]"
        else:
            body = payload.get("body") or row.summary
        if body:
            out.append({"role": role, "content": str(body)})
    return out


async def answer(
    organization_id: int,
    text: str,
    asked: list[int] | None = None,
    preset: str | None = None,
    subjects: list[int] | None = None,
) -> str:
    """Compose the context, call the model, record the reply. Returns it.

    ``subjects`` are the bots the line named without addressing (KAN-140):
    their steps join the context so an edit can name a real step, and the
    templates join it so a build can name a real template.
    """
    from api.services.agent_builder import client, settings

    context = await build_context(organization_id, text)
    context = f"{context}\n\n{await office_context(organization_id, subjects)}"
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
        tools = await tools_for(organization_id)
        reply = await _speak(model, conversation, organization_id, tools=tools)
        # Up to MAX_TOOL_ROUNDS rounds, not one: a read of a connected app
        # feeds the answer, and a read may precede a proposed write ("find
        # the lead, then draft the mail"). On the last allowed round the
        # model is given no tools, so it answers with what it has and a
        # loop of reads cannot spend credit all afternoon. Anything it asks
        # for that is not one of its tools is answered as unavailable.
        #
        # Tools are offered again only after a round that was purely reads
        # of connected apps. A round that produced a card -- any of the
        # five, or an app write -- ends the tool phase: the model is given
        # no tools and answers, which is the "propose it, say so, end"
        # rule and what stops it proposing the same thing twice.
        rounds = 0
        while reply.wants_tools and rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            conversation.add_assistant(reply)
            reads_only = True
            for call in reply.tool_calls:
                result = await _tool(organization_id, call)
                conversation.add_tool_result(call, result)
                if not _was_a_read(call, result):
                    reads_only = False
            reply = await _speak(
                model,
                conversation,
                organization_id,
                tools=tools if (reads_only and rounds < MAX_TOOL_ROUNDS) else None,
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
    # After the row, so the screen swaps the forming text for the row rather
    # than showing a blank between them.
    await reply_draft.clear(organization_id)
    # And into the graph, with time, so what the person said on the thread
    # can be asked about later (Family B). No graph, nothing happens.
    try:
        from api.services.knowledge_graph import feed as graph_feed

        await graph_feed.remember_exchange(
            organization_id=organization_id, person_said=text, decibyl_said=body
        )
    except Exception as exc:  # noqa: BLE001 - the reply is already on the thread
        logger.warning("Graph could not remember the exchange: {}", exc)
    return body


#: Tool rounds a single reply may take. Four covers "look it up, then do
#: it" with room for a retry; more is a model going in circles on credit.
MAX_TOOL_ROUNDS = 4


def office_tools() -> list[dict[str, Any]]:
    """Decibyl's own: the five that end in a card, and the two that hand a
    person their own document (find runs now; send is a card for an
    identity document and runs now for anything else)."""
    return [
        actions.tool_schema(),
        office.edit_tool_schema(),
        office.test_tool_schema(),
        office.check_tool_schema(),
        tasks_board.tool_schema(),
        documents.find_tool_schema(),
        documents.send_tool_schema(),
        document_fields.tool_schema(),
    ]


#: The old name, kept for anything that imported it.
TOOLS = office_tools


async def tools_for(organization_id: int) -> list[dict[str, Any]]:
    """The five, plus the workspace's connected apps (see connected_tools).
    A read runs in the turn; a write becomes a run_tool card."""
    connected = await connected_tools.list_for_organization(organization_id)
    return office_tools() + connected_tools.schemas(connected)


def _was_a_read(call: Any, result: Any) -> bool:
    """Whether this call was a read that actually ran (or failed running),
    as opposed to anything that wrote a card: a connected-app read, or
    find_document, after which the model may still need to send."""
    name = str(getattr(call, "name", "") or "")
    return (
        (name.startswith(connected_tools.PREFIX) or name == documents.FIND_TOOL_NAME)
        and isinstance(result, dict)
        and result.get("status") in ("success", "error")
    )


async def _app_tool(organization_id: int, call: Any) -> dict[str, Any]:
    """A connected app was called: a read runs now, a write becomes a card."""
    available = connected_tools.by_function_name(
        await connected_tools.list_for_organization(organization_id)
    )
    tool = available.get(call.name)
    if tool is None:
        return {"status": "unavailable", "reason": "that app is not connected here"}
    arguments = dict(call.arguments or {})
    if connected_tools.is_read(tool):
        return await connected_tools.execute(
            organization_id=organization_id,
            tool=tool,
            arguments=arguments,
            # Keyed on the model's own call id so a retried turn charges once.
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    return await actions.propose(
        organization_id=organization_id,
        workflow_id=None,
        workflow_run_id=None,
        arguments={
            "action": actions.RUN_TOOL,
            "tool_uuid": tool.tool_uuid,
            "arguments": arguments,
            "why": f"Asked in the thread: {tool.name}",
        },
        in_channel=False,
    )


async def _tool(organization_id: int, call: Any) -> dict[str, Any]:
    if str(call.name or "").startswith(connected_tools.PREFIX):
        return await _app_tool(organization_id, call)
    arguments = dict(call.arguments or {})
    if call.name == actions.TOOL_NAME:
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments=arguments,
            in_channel=False,
        )
    if call.name == self_edit.TOOL_NAME:
        return await office.propose_edit(
            organization_id=organization_id, arguments=arguments
        )
    if call.name == office.TEST_TOOL_NAME:
        return await office.offer_test(
            organization_id=organization_id, arguments=arguments
        )
    if call.name == tasks_board.TOOL_NAME:
        return await tasks_board.create(
            organization_id=organization_id,
            from_workflow_id=None,
            workflow_run_id=None,
            arguments=arguments,
        )
    if call.name == office.CHECK_TOOL_NAME:
        return await office.check_bot(
            organization_id=organization_id, arguments=arguments
        )
    if call.name == documents.FIND_TOOL_NAME:
        return await documents.find_for_thread(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    if call.name == document_fields.TOOL_NAME:
        return await document_fields.confirm_for_thread(organization_id, arguments)
    if call.name == documents.SEND_TOOL_NAME:
        return await documents.send_for_thread(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    return {"status": "unavailable", "reason": "no such tool"}


async def office_context(organization_id: int, subjects: list[int] | None) -> str:
    """Templates, and the steps of any bot the line is about. Each reading
    fails alone, as the four in build_context do."""
    parts: list[str] = []
    try:
        parts.append(office.templates_block())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decibyl could not list templates: {}", exc)
    if subjects:
        try:
            workflows = await db_client.get_all_workflows_for_listing(
                organization_id=organization_id
            )
            by_id = {w.id: w for w in workflows}
            mentions_ = [
                office.mentions.Mention(
                    workflow_id=wid,
                    handle=getattr(by_id[wid], "handle", None)
                    or office.mentions.handle_for(by_id[wid].name),
                )
                for wid in subjects
                if wid in by_id
            ]
            block = await office.subjects_block(organization_id, mentions_)
            if block:
                parts.append(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Decibyl could not read the subject bots: {}", exc)
    return "\n\n".join(parts)


async def _speak(
    model: Any,
    conversation: Any,
    organization_id: int,
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """One turn, streamed into the draft as it forms. The text the screen
    shows growing is the text that becomes the row; a tool call, if any,
    arrives whole at the end and is acted on before the next turn."""
    from api.services.agent_builder import client

    last = {"at": 0.0}

    async def on_text(text: str) -> None:
        # A few writes a second is plenty for a screen polling at that rate,
        # and Redis is not the thing to make wait for a token.
        now = time.monotonic()
        if now - last["at"] < 0.25:
            return
        last["at"] = now
        await reply_draft.set_draft(organization_id, text)

    return await client.stream(
        provider=model.provider,
        model=model.model,
        api_key=model.api_key,
        system=SYSTEM,
        conversation=conversation,
        on_text=on_text,
        tools=tools,
    )


def recent_window() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)

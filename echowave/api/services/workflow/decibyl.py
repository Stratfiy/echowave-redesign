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
from api.services import acting, features, prompt_budget, reporting_window
from api.services.billing import model_usage
from api.services.browser import tool as browser_tool
from api.services.documents import tools as procurement
from api.services.knowledge_graph import personal as personal_memory
from api.services.knowledge_graph import quiet, recall, teach
from api.services.organization_preferences import get_organization_preferences
from api.services.skills import imports as skill_imports
from api.services.workflow import (
    actions,
    agent_timeline,
    bot_from_brief,
    chat_memory,
    connected_tools,
    connector_offer,
    contact_lookup,
    decibyl_tasks,
    document_fields,
    documents,
    draft_requests,
    filing,
    office,
    prospects,
    records,
    reply_draft,
    reply_stop,
    routines,
    self_edit,
    skill_context,
    tables,
    tasks_board,
    untrusted,
    visibility,
    web_tools,
)

NAME = "Decibyl"

#: How much of the thread the model sees is the plan's chat memory --
#: see _history and services/workflow/chat_memory.py.
#: Timeline rows folded into "what the bots did lately".
RECENT_EVENTS = 40
#: Chunks read from the knowledge base for one question.
KNOWLEDGE_CHUNKS = 4

SYSTEM = (
    "You are Decibyl, the assistant inside a business's Decibyl workspace. "
    "The business runs agents (voice and chat agents) that take calls, confirm "
    "orders, chase payments and answer staff. You know the team's numbers, "
    "the company's documents, what the business has confirmed about itself, "
    "and what the agents did lately. All of it is in the context below.\n\n"
    "Rules:\n"
    "- Answer in the language of the person's latest message.\n"
    "- Be short: two to five sentences, or a short list. Numbers come from "
    "the context only; never invent a figure, a name or a document. If the "
    "context does not have it, say so in one line and say where it would be.\n"
    "- When the question is about one agent, name the agent. When something "
    "needs a person, say what and why.\n"
    "- You are the manager of this office. The agents are colleagues with "
    "@handles; you build them, change them and test them, and a person "
    "confirms each of those on a card. A line that starts with @handle is "
    "for that agent, not you; it has already been handed over.\n"
    "- propose_action: turn an agent on or off, call back a missed caller from "
    "the context, forget a confirmed fact by its key, or create_bot from a "
    "template in the context. For create_bot, ask for every answer the "
    "template needs before proposing; never invent an answer.\n"
    "- propose_edit: change one step of a named agent. Use the agent's steps in "
    "the context; give the complete new prompt.\n"
    "- test_bot: offer to hear (a call) or try (text) a named agent.\n"
    "- check_bot: a scripted caller plays a scenario and a judge grades it; "
    "the verdict comes back to this thread as a card. Offer it before an "
    "edit lands on a live agent. When a person asks you to fix an agent after a "
    "check, use the verdict and the agent's steps in the context to propose "
    "one edit with propose_edit.\n"
    "- create_task: file a task on the team's board for an agent (by @handle) "
    "or for the team, when a person asks you to have an agent do something "
    "later or hand work between agents. The board shows who did what.\n"
    "- read_board: runs now. What is on the board -- open, blocked, in "
    "review, who holds what -- or one task with its comments. Read it before "
    "answering anything about the team's work; never guess the board.\n"
    "- schedule_routine: proposes a card. Something for you to do on a "
    "cadence ('every weekday at 9am summarise the board'). The person "
    "confirms, tests it once from Schedules, then switches it on. An "
    "agent's own routine is set on the agent, not here.\n"
    "- Connected apps (the app_… tools): the Connected apps block in the "
    "context names every one you have, split into the ones that run as you "
    "answer (fetch, list, search, find) and the ones that propose a card "
    "(send, create, update, delete). Use a read to look things up and say "
    "what you found; say you have proposed anything else. That block is "
    "what you can reach -- not what you remember from earlier in the "
    "thread, and not the tools you happen to be holding this round, because "
    "on some rounds you are given none. If a tool is named there you have "
    "it. Never offer a lesser version of what was asked for when the block "
    "names the real one: do not offer to write a draft when a send tool is "
    "named. If the block has nothing that fits, say so in one line and call "
    f"{connector_offer.TOOL_NAME}.\n"
    f"- {connector_offer.TOOL_NAME}: puts a connect card on the thread for "
    "an app this workspace has not connected. The card is how an app gets "
    "connected -- never send anybody to a screen to do it.\n"
    f"- {filing.TOOL_NAME}: file a document that arrived on WhatsApp or "
    "email into the workspace's Drive, once the person has said what it is "
    "or whose it is. For an identity document use the owner's name as the "
    "person gave it, never a name read off the document.\n"
    "- web_search and web_fetch: the web, on Decibyl's own key. A search "
    "costs a tool call plus the search; a page read costs a tool call. Use "
    "them for what is outside the workspace and say where a fact came "
    "from. The social networks are never read, by rule; do not offer to.\n"
    "- save_prospects: save people or businesses found on the web into the "
    "Prospects list, with the page each came from, or record how one "
    "answered (interested, unsubscribed, bounced...). Runs now; organising "
    "is not a send. Read the list with search_records first so nobody "
    "already written to or declined is saved as new.\n"
    "- search_records: the workspace's own contacts, documents, calls and "
    "outcomes, on demand and free. Use it for a list, a count or a date "
    "range the context does not already carry; the context's rows are "
    "only the ones the question named.\n"
    "- install_from_repository: skills from a GitHub repository the person "
    "names. Reads it now and proposes one card listing what it would "
    "install; a person confirms. Say what was found, including what was "
    "skipped and why, then end your reply.\n"
    "- run_script: a short Python script in the sandbox for a job over many "
    "rows or many records, with the connected apps reachable by name inside "
    "it. Four credits a run; offered only on plans that have it.\n"
    "- An identifier -- an id, a uuid, a page, a thread, an account -- is "
    "not something to infer. A true one comes from the context, from this "
    "thread, or from a read you just ran. Never carry one over from another "
    "app, or from another record, because it is the only one you have seen: "
    "asked for a page you passed the signed-in user's id, and asked for a "
    "LinkedIn organisation you passed a Facebook one. On a read that wastes "
    "a call. On anything that writes it is somebody else's record, and a "
    "card carrying a wrong id reads exactly like a card carrying a right "
    "one, so nobody confirming it can tell. If you do not have it, fetch it "
    "with a read, or say which one you need and stop.\n"
    "- What you can and cannot do is a fact about the tools and context in "
    "front of you, so read them before you say it. Claiming something you "
    "cannot do and denying something you can cost the same trust, and the "
    "denial is the one that gets made -- said with confidence about "
    "something the person watched work an hour ago. If you are unsure, say "
    "what you are about to try rather than guessing at the answer.\n"
    "- Nothing happens until a person confirms on the card, so propose it "
    "and say you have. Deleting an agent, dialling a new number and anything "
    "else you cannot do: say so, and say where it is done.\n"
    "- Never describe a card as safer than it is. A card you propose runs "
    "for real when it is confirmed, and most of them cannot be undone. Only "
    "a tool whose own name says draft writes a draft: GMAIL_CREATE_EMAIL_"
    "DRAFT drafts, GMAIL_REPLY_TO_THREAD and GMAIL_SEND_EMAIL and "
    "GMAIL_SEND_DRAFT all send to the other person. If you are asked to "
    "draft and the only tool you have sends, say that instead of proposing "
    "the send and calling it a draft.\n"
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
    "- Memory: recall asks what was said or done on calls, on this thread "
    "and in documents that arrived on a channel, by whom and when. Use it "
    "for a question about a person, a supplier, a promise, a decision or a "
    "reason, and pass a date range when the person gives one. A fact "
    "labelled inferred is something that was said or implied: say it that "
    'way ("on the 3rd Ravi said he would pay by Friday"), never as '
    "settled, and never set a reminder on it without asking. Only a fact "
    "labelled confirmed is stated as fact. If recall is unavailable, say "
    "memory is not switched on here and answer from the context.\n"
    '- Corrections: when the person corrects something memory had ("no, '
    'Arun is from college, not work"), call correct_memory with the '
    "subject, the point and what is true (and what memory had, if said), "
    "then say what changed in one line, as the tool returns it.\n"
    "- Memory's own messages (the Sunday review, connection notices, "
    "reminders of things asked about) are off until the person asks; "
    "memory_messages turns each on or off for the person asking. Say what "
    "is now on or off.\n"
    "- An 'Asked before' block in the context is something the person once "
    "asked memory about that this line touches: mention it in a clause "
    "only when it helps the line, never as a separate announcement.\n"
    "- A file attached to the line (under 'Attached to this line') is the "
    "material for the request. Read it before answering. A document that "
    "says what an agent should do -- a written flow, a process, a job "
    "description, a vendor spec -- is a brief, and a brief is built, not "
    "discussed: call build_bot_from_spec with the document's own text as "
    "`spec`, naming the agent from the document. Do not ask which template, "
    "and do not condense the document into a sentence first -- the steps "
    "are what the agent is built from. Use create_bot instead only when what "
    "they want plainly IS one of the templates in your context and the "
    "document is just the answers for it. Ask only for something neither "
    "the document nor the conversation gives you.\n"
    "- Never repeat an OTP, a card number or an identity number.\n"
    f"- {untrusted.RULE}\n"
)


def system_prompt(organization_id: int | None = None) -> str:
    """SYSTEM, plus the rules for the tools switched on for this deployment.

    The procurement rules are said only while those tools are offered: a
    rule for a tool the model is not holding is a tool it will describe and
    cannot call (``test_decibyl_knows_what_it_has``)."""
    return (
        SYSTEM
        + (procurement.RULES if procurement.enabled() else "")
        + (tables.RULES if tables.enabled(organization_id) else "")
        + (browser_tool.RULE if browser_tool.enabled(organization_id) else "")
    )


def thread_filter(organization_id: int | None = None) -> dict[str, Any]:
    """The timeline filter that is Decibyl's thread.

    An allowlist, and therefore the shape AGENTS.md warns about: a card
    written with a kind missing from this list is written correctly, read
    by nobody, and fails nowhere. Anything Decibyl's tools can put on the
    thread belongs here, and ``test_decibyl_thread_shows_what_it_writes``
    fails when one is added without it.
    """
    return {
        "assistant_thread": True,
        "kinds": [
            AgentEventKind.MESSAGE.value,
            AgentEventKind.ACTION_PROPOSED.value,
            AgentEventKind.EDIT_PROPOSED.value,
            AgentEventKind.CONNECTOR_OFFERED.value,
            AgentEventKind.ACTIVITY.value,
            # The private browser's panel: live view, then the receipt. Only
            # written while ``decibyl_browser`` is on; listed always, since a
            # kind missing here is a panel nobody ever sees.
            AgentEventKind.BROWSER_SESSION.value,
            # The files the document tools hand over (a drafted PO, a
            # cost-bid sheet), shown on the thread with their downloads.
            *(
                (AgentEventKind.DELIVERABLE.value,)
                if procurement.enabled() or tables.enabled(organization_id)
                else ()
            ),
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
    thread_id: str | None = None,
) -> list[int]:
    """Record the person's line, hand off any mentions, queue the reply.

    Returns the bots the line was handed to. ``reply_to`` names a channel
    the answer should also go back on -- a person who wrote on WhatsApp
    reads the answer there, as well as on the thread.

    ``thread_id`` is which conversation this was said in. None is the one
    the account has always had, so a client that knows nothing about threads
    keeps writing where it always wrote.
    """
    workflows = visibility.only_visible(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id),
        await visibility.role_of(user_id, organization_id),
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
        thread_id=thread_id,
    )

    # The person's daily allowance of turns (operational quotas): held even
    # in free mode. Over it, the answer is a line in the thread saying so,
    # and nothing is asked of any model or bot. No-op while switched off.
    if await turn_refused(
        organization_id=organization_id, user_id=user_id, thread_id=thread_id
    ):
        return []

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
                summary=f"Asked {names.get(mention.workflow_id, 'an agent')}",
                payload={"from": NAME, "asked": mention.workflow_id},
                in_channel=False,
                thread_id=thread_id,
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
            user_id,
            attachments,
            # By name, so the reply cannot land in the wrong chat if an
            # argument is ever added ahead of it.
            thread_id=thread_id,
        )
    except Exception as exc:  # noqa: BLE001 - said out loud below
        logger.error("Decibyl could not be asked to answer: {}", exc)
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.COULD_NOT.value,
            summary=f"{NAME} could not be reached to answer that",
            in_channel=False,
            thread_id=thread_id,
        )
    return asked


async def turn_refused(
    *,
    organization_id: int,
    user_id: int | None,
    thread_id: str | None = None,
    workflow_id: int | None = None,
    folder_id: int | None = None,
) -> bool:
    """Spend one of the person's turns, or say in the thread that today's are
    used and return True. A line nobody can be charged for (no person) is
    not refused: every person-facing entry passes the signed-in member."""
    from api.services import member_preferences, quotas

    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return False
    try:
        await quotas.consume(user_id, quotas.MODEL_TURNS)
        return False
    except quotas.QuotaExceeded as exc:
        usage = exc.usage
    line = quotas.message(usage, await member_preferences.timezone_of(user_id))
    on_thread = workflow_id is None and folder_id is None
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=line,
        workflow_id=workflow_id,
        folder_id=folder_id,
        payload={
            "body": line,
            **({"from": NAME} if on_thread else {}),
            "quota": usage.as_dict(),
        },
        # A bot's own chat is not its channel; a channel line is.
        in_channel=folder_id is not None,
        thread_id=thread_id if on_thread else None,
    )
    return True


# --- the context -----------------------------------------------------------


def team_block(
    headline: dict[str, Any], members: list[dict[str, Any]], span: str
) -> str:
    """The team's numbers as lines the model can quote.

    Two facts of different ages sit on every member line: whether the bot is
    live, which is true *now*, and what it did, which is true *over the span*.
    Printed side by side with nothing to separate them they read as one fact,
    and Decibyl told the founder a paused bot had taken "2 calls ... anyway"
    -- inventing a broken pause out of a bot that was answering earlier in the
    day and has since been switched off. So the line says which is which, and
    the note below says it once more in words, because the model is the reader
    that got it wrong.
    """
    lines = [
        f"Team {span}: {headline.get('agents', 0)} agents, {headline.get('live', 0)} live now, "
        f"{headline.get('calls', 0)} calls, {headline.get('answered', 0)} answered, "
        f"{headline.get('outcomes', 0)} outcomes, {headline.get('needs_attention', 0)} need attention."
    ]
    for m in sorted(members, key=lambda m: -(m.get("calls") or 0)):
        state = "live now" if m.get("is_live") else "paused now"
        lines.append(
            f"- {m.get('name')}: {state}; {span}: {m.get('calls', 0)} calls, "
            f"{m.get('answered', 0)} answered, {m.get('outcomes', 0)} outcomes, "
            f"{m.get('failures', 0)} failures. {m.get('status') or ''}".rstrip()
        )
    lines.append(
        "Live/paused is as of now; the counts cover the whole span. An agent "
        "that is paused now and has calls in the span was answering earlier "
        "in it -- that is not a pause failing to hold, and must not be "
        "reported as one."
    )
    return "\n".join(lines)


def door_block(door: dict[str, str]) -> str:
    """The door's answers as a line: role and business, when recorded."""
    role = (door.get("role") or "").replace("_", " ")
    business = (door.get("business") or "").replace("_", " ")
    if not role and not business:
        return "They have not said. Ask, if it matters to the answer."
    parts = []
    if role:
        parts.append(f"their role: {role}")
    if business:
        parts.append(f"their business: {business}")
    return "Signing up, they said " + "; ".join(parts) + "."


#: Scope names as somebody would say them, for the documents block.
_SCOPE_WORDS = {
    "org": "the knowledge base, read by every agent",
    "bot": "one agent's own",
    "channel": "a channel's",
    "library": "the library, read only by a step that names it",
}


def documents_block(rows: list[Any], names: dict[int, str]) -> str:
    """What the account has uploaded, and who reads it.

    Without this the model knew only whether a *search* of the knowledge base
    matched, so an account with three documents filed against bots was told
    its knowledge base was empty -- true of the search, false of the files,
    and the person is looking at them on the Knowledge base screen while
    reading it.
    """
    if not rows:
        return "Nothing uploaded yet."
    lines = []
    for row in rows[:20]:
        scope = str(getattr(row, "scope", "") or "library")
        where = _SCOPE_WORDS.get(scope, scope)
        if scope == "bot":
            bot = names.get(getattr(row, "workflow_id", None) or -1)
            if bot:
                where = f"{bot}'s own"
        state = str(getattr(row, "processing_status", "") or "")
        still = " (still being read)" if state and state != "completed" else ""
        lines.append(f"- {getattr(row, 'filename', 'a file')}: {where}{still}")
    more = len(rows) - len(lines)
    if more > 0:
        lines.append(f"- and {more} more")
    return "\n".join(lines)


def memory_block(rows: list[Any]) -> str:
    """What the business has confirmed, bounded the way a call bounds it.

    The voice path capped this at forty a long time ago and wrote down why.
    This reads the same table through a different function and had no cap at
    all -- two hundred confirmed facts measured 28,089 characters here
    against 5,842 on the voice side, in every turn of every thread.
    """
    facts = [
        f"- {r.key}: {prompt_budget.clip(str(r.value), prompt_budget.MAX_FACT_CHARS)}"
        for r in rows
        if getattr(r, "kind", "fact") == "fact"
    ]
    if not facts:
        return "Nothing confirmed yet."
    return "\n".join(
        prompt_budget.lines(facts, max_items=prompt_budget.MAX_FACTS, noun="fact")
    )


def recent_block(events: list[Any], bot_names: dict[int, str]) -> str:
    """The timeline, bounded on both axes.

    The query already limits the rows. That is half a bound: one event whose
    summary is a pasted transcript passes any row count, so each summary is
    clipped as well.
    """
    entries = []
    for e in events:
        who = bot_names.get(e.workflow_id, "") if e.workflow_id is not None else ""
        stamp = e.at.strftime("%d %b %H:%M") if getattr(e, "at", None) else ""
        summary = prompt_budget.clip(str(e.summary), prompt_budget.MAX_EVENT_CHARS)
        entries.append(f"- {stamp} {who + ': ' if who else ''}{summary}")
    if not entries:
        return "Nothing recorded lately."
    return "\n".join(
        prompt_budget.lines(
            entries,
            max_items=prompt_budget.MAX_EVENTS,
            noun="entry",
            plural="entries",
        )
    )


def knowledge_block(result: dict[str, Any], contacts: list[Any] | None = None) -> str:
    """What the account knows that bears on this question.

    Two sources, one heading, because they are one idea to the person
    asking: passages from the files they uploaded, and the people in their
    contact book that the question names. Contacts come first -- a question
    naming somebody is usually about them, and a passage that merely reads
    like them is the weaker answer.

    The document half needs embeddings configured; the contact half does
    not, so an account on the free tier with no embeddings still gets an
    answer about its own customers rather than silence.
    """
    out = []
    for contact in contacts or []:
        out.append(contact_lookup.block([contact]))
    chunks = result.get("chunks") or []
    for c in chunks[:KNOWLEDGE_CHUNKS]:
        text = str(c.get("text") or c.get("content") or "").strip()
        name = c.get("document_name") or c.get("filename") or "document"
        if text:
            out.append(f"- ({name}) {text[:600]}")
    return "\n".join(out) if out else "Nothing in the knowledge base matches that."


async def _knowledge(organization_id: int, question: str) -> dict[str, Any]:
    """The knowledge base, on the account's own embeddings key. Unavailable
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
        logger.warning("Decibyl could not read the knowledge base: {}", exc)
        return {"status": "unavailable", "chunks": []}


async def build_context(organization_id: int, question: str) -> str:
    """One text block from the four readings. Each reading fails alone."""
    from api.routes.team import _members

    # "Today" is the operator's calendar day, not the last 24 hours. The two
    # are different spans and reporting the second under the name of the first
    # is what had this function answer "7 calls today" and, minutes later,
    # "15 calls today" -- the window had slid, nothing else had happened.
    if "week" in question.lower():
        window = reporting_window.last_days(7)
    else:
        try:
            preferences = await get_organization_preferences(organization_id)
            zone = preferences.timezone
        except Exception as exc:  # noqa: BLE001 - one reading of several
            logger.warning("Decibyl could not read the account's timezone: {}", exc)
            zone = None
        window = reporting_window.day_so_far(zone)

    # Kept for the callers below that still think in hours; the window is what
    # the counts are actually taken over.
    hours = 168 if "week" in question.lower() else 24
    try:
        members = [
            m.model_dump()
            for m in await _members(organization_id, hours, since=window.since)
        ]
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
            organization_id=organization_id,
            # The workspace's memory, plus the asker's own (MEM-1).
            user_id=personal_memory.viewer(),
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
    # Contacts are part of what the account knows, and were reachable only
    # from a ringing phone. Matched against the question, never dumped.
    #
    # Guarded here as well as inside `matching`, like every other reading in
    # this function: the module's own guard is about a contact book that
    # cannot be read, and this one is about the reading itself failing. The
    # contract is that each reading fails alone, and a reading that can take
    # the other seven with it does not honour it.
    try:
        contacts = await contact_lookup.matching(organization_id, question)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Decibyl could not read the contacts: {}", exc)
        contacts = []
    # The procedures this account installed. Every one named; the one the
    # question invokes carried whole. The shelf has shown these as installed
    # since it shipped and no prompt has ever read them.
    try:
        skills = await skill_context.installed_for(organization_id)
        invoked = skill_context.named(question, skills)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Decibyl could not read the skills: {}", exc)
        skills, invoked = [], []
    # The files themselves, not just whether a search of them matched: see
    # documents_block.
    try:
        documents = await db_client.get_documents_for_organization(
            organization_id, limit=50
        )
    except Exception as exc:  # noqa: BLE001 - a list is a nicety, not a dependency
        logger.warning("Decibyl could not list the documents: {}", exc)
        documents = []
    # What they said at the door: a clinic owner and a logistics ops lead
    # want different first bots, and the same question means different
    # things from each.
    from api.services.workflow import home_openers

    door = await home_openers.door_answers(organization_id)
    # Which apps are connected, so the model knows what it can reach before
    # it tries. The listing never raises; an empty workspace reads as such.
    apps = await connected_tools.list_for_organization(organization_id)
    # Apps connected at the vendor whose tool rows have not been made yet.
    # Reading this here also queues them, so somebody who connects an app and
    # comes straight back to the chat gets them without opening any screen.
    try:
        awaiting = await connected_tools.awaiting_setup(organization_id, apps)
        # Apps connected through their own server. Decibyl cannot call them
        # and a bot can, so they are named rather than offered -- they were
        # invisible here, and it denied that a connected app was connected.
        mcp_servers = [
            str(t.name)
            for t in await connected_tools.mcp_for_organization(organization_id)
            if getattr(t, "name", None)
        ]
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Decibyl could not check for apps awaiting setup: {}", exc)
        awaiting = []
        mcp_servers = []

    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=readings_line(
            bots=len(members),
            facts=len(memory_rows),
            passages=len(knowledge.get("chunks") or []),
        ),
        payload={
            "from": NAME,
            "sources": sources_read(
                bots=len(members), facts=len(memory_rows), knowledge=knowledge
            ),
        },
        in_channel=False,
    )

    return (
        f"## Team\n{team_block(headline, members, window.label)}\n\n"
        f"## Who you are talking to\n{door_block(door)}\n\n"
        f"## What the business has confirmed\n{memory_block(memory_rows)}\n\n"
        f"## Lately\n{recent_block(recent, bot_names)}\n\n"
        f"## Missed calls not returned\n{missed_block(missed)}\n\n"
        f"## Connected apps\n"
        f"{connected_tools.apps_block(apps, awaiting, mcp_servers)}\n\n"
        f"## From the knowledge base\n{knowledge_block(knowledge, contacts)}\n\n"
        f"{skills_section(skills, invoked)}"
        f"## Files this account has uploaded\n{documents_block(documents, bot_names)}\n"
    )


def skills_section(installed: list[Any], invoked: list[Any]) -> str:
    """The skills heading, or nothing at all.

    An account with no skills installed gets no heading rather than an empty
    one: a section saying "none" spends prompt to report an absence, and the
    other blocks here that do say it are ones whose emptiness is itself an
    answer ("no matching passage" means the search ran).
    """
    block = skill_context.block(installed, invoked)
    if not block:
        return ""
    return f"## Skills this account installed\n{block}\n\n"


def sources_read(
    *, bots: int, facts: int, knowledge: dict[str, Any]
) -> list[dict[str, Any]]:
    """What an answer was read from, for the sources panel (screen 04).

    One entry per reading, including the one that could not run: "2 of 3
    sources checked" is the honest line when the knowledge base is not set
    up, and a panel that lists only what worked hides that it was missing.
    """
    chunks = knowledge.get("chunks") or []
    documents: list[str] = []
    for chunk in chunks[:KNOWLEDGE_CHUNKS]:
        name = chunk.get("document_name") or chunk.get("filename")
        if name and name not in documents:
            documents.append(str(name))
    knowledge_ok = knowledge.get("status") != "unavailable"
    return [
        {
            "kind": "team",
            "label": "Your team",
            "status": "read",
            "detail": f"{bots} agent{'s' if bots != 1 else ''}",
        },
        {
            "kind": "memory",
            "label": "Confirmed facts",
            "status": "read",
            "detail": f"{facts} fact{'s' if facts != 1 else ''}",
        },
        {
            "kind": "knowledge",
            "label": "Company knowledge",
            "status": "read" if knowledge_ok else "unavailable",
            "detail": (
                f"{len(chunks)} passage{'s' if len(chunks) != 1 else ''}"
                if knowledge_ok
                else "Not set up for this workspace"
            ),
            "documents": documents,
        },
    ]


def readings_line(*, bots: int, facts: int, passages: int) -> str:
    """One line on what was read for an answer, in the order it is read."""
    parts = [f"the team ({bots} agent{'s' if bots != 1 else ''})"]
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


async def _history(
    organization_id: int, thread_id: str | None = None
) -> list[dict[str, str]]:
    """The last turns of the thread, oldest first, as chat messages.

    As many as the account's memory holds (chat_memory): the window is
    filled from the newest row back until the plan's token budget is spent,
    so a bigger plan remembers further and a one-line "ok" costs one line.
    """
    plan = await chat_memory.budget(organization_id)
    rows = await db_client.agent_events(
        organization_id=organization_id,
        limit=chat_memory.MAX_ROWS,
        thread_id=thread_id,
        **thread_filter(organization_id),
    )
    newest_first: list[tuple[str, str]] = []
    for row in rows:
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
            newest_first.append((role, str(body)))
    kept, _ = chat_memory.window((body for _, body in newest_first), plan.tokens)
    return [
        {"role": role, "content": body} for role, body in reversed(newest_first[:kept])
    ]


async def answer(
    organization_id: int,
    text: str,
    asked: list[int] | None = None,
    preset: str | None = None,
    subjects: list[int] | None = None,
    author_id: int | None = None,
    attachments: list[dict[str, Any]] | None = None,
    last_try: bool = False,
    thread_id: str | None = None,
) -> str:
    """Compose the context, call the model, record the reply. Returns it.

    ``attachments`` are the files on the line (document uuid, filename):
    their text joins the context, because a file on a line is the material
    for the request -- "build a bot for this" with a document attached is
    a brief, not a question about templates.

    ``author_id`` is the signed-in person who wrote the line, when known:
    a switch that is theirs alone (memory_messages) needs it.

    ``subjects`` are the bots the line named without addressing (KAN-140):
    their steps join the context so an edit can name a real step, and the
    templates join it so a build can name a real template.
    """
    # Every row this turn writes -- the activity lines from the context, a
    # card a tool proposes, the reply itself -- belongs to the conversation
    # it was asked in. Set once here rather than passed down through each
    # writer; see agent_timeline.in_thread for why.
    # And it runs as the person who asked: their Gmail, not the
    # workspace's, when they have one (WS-1), and their own memory beside
    # the workspace's (MEM-1).
    with agent_timeline.in_thread(thread_id), acting.acting_as(author_id):
        return await _answer(
            organization_id,
            text,
            asked=asked,
            preset=preset,
            subjects=subjects,
            author_id=author_id,
            attachments=attachments,
            last_try=last_try,
            thread_id=thread_id,
        )


async def _answer(
    organization_id: int,
    text: str,
    *,
    asked: list[int] | None,
    preset: str | None,
    subjects: list[int] | None,
    author_id: int | None,
    attachments: list[dict[str, Any]] | None,
    last_try: bool,
    thread_id: str | None,
) -> str:
    """The turn itself, inside the thread its caller set."""
    from api.services.agent_builder import client, settings

    # Which model wrote the reply, for feedback stored against it; None when
    # the turn never reached one.
    model = None
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
                "\n\nThe person also addressed these agents, which will answer in their "
                f"own chats: {', '.join(names)}. Say so in one line and do not answer for them."
            )

    conversation = client.Conversation()
    history = await _history(organization_id, thread_id)
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
    # Something asked about before that this line touches (B6): a block,
    # not a message, and only at its interval.
    try:
        from api.services.knowledge_graph import spaced_recall

        asked_before = await spaced_recall.related_context(organization_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read what was asked before: {}", exc)
        asked_before = ""
    if asked_before:
        context = f"{context}\n\n{asked_before}"
    attached = await attached_block(organization_id, attachments, last_try=last_try)
    if attached:
        context = f"{context}\n\n{attached}"
    conversation.add_user(f"{context}\n\n## Question\n{text}{handed}")

    #: The backup model's id when it answered any part of this turn.
    backup_model = ""
    failed = False
    stopped = False
    # A Stop meant for an earlier reply must not end this one.
    await reply_stop.clear(organization_id, thread_id)
    try:
        if not preset:
            # Auto: no brain picked for this turn, so the turn's own work
            # picks one (services/routing/brain.py).
            from api.services.routing import brain

            routed = await brain.auto_route(
                organization_id, text, attachments=len(attachments or [])
            )
            if routed is not None:
                preset = routed.preset
                logger.info("Decibyl turn routed {} by {}", routed.kind, routed.source)
        async with db_client.async_session() as session:
            model = await settings.resolve_for_organization(
                session, preset, organization_id=organization_id
            )
        # Schemas the model has loaded this thread, by tool name. Starts
        # empty: every connected app is a name and a line until asked for.
        loaded: dict[str, dict[str, Any]] = {}
        tools = await tools_for(organization_id, loaded)
        reply = await _speak(model, conversation, organization_id, tools=tools)
        backup_model = reply.fallback_model
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
            asked_for_schema = False
            for call in reply.tool_calls:
                if call.name == connected_tools.LOAD_TOOL_NAME:
                    result = await _load_tool(organization_id, call, loaded)
                    asked_for_schema = True
                else:
                    result = await _tool(
                        organization_id,
                        call,
                        author_id,
                        request=text,
                        thread_id=thread_id,
                    )
                conversation.add_tool_result(call, result)
                if not _was_a_read(call, result):
                    reads_only = False
            if asked_for_schema:
                # The tool it asked about is now offered with its arguments.
                tools = await tools_for(organization_id, loaded)
            capped = reads_only and rounds >= MAX_TOOL_ROUNDS
            if capped and decibyl_tasks.enabled():
                # Mid-plan at the cap, and the board can carry on (D-1a):
                # hand it the transcript and tell the person where the
                # answer will land, instead of asking them to ask again.
                task = await decibyl_tasks.hand_off(
                    organization_id,
                    messages=conversation.messages,
                    loaded=loaded,
                    preset=preset,
                    thread_id=thread_id,
                    author_id=author_id,
                    request=text,
                    rounds=rounds,
                )
                reply = client.ModelReply(
                    text=decibyl_tasks.HANDED_OFF.format(rounds=rounds, task_id=task.id)
                )
                break
            if capped:
                # Tools are being taken away because of the cap, not because
                # a card ended the phase. Say so, or the model is handed
                # nothing mid-plan and returns nothing.
                conversation.add_user(LAST_STEP_NOTICE)
            reply = await _speak(
                model,
                conversation,
                organization_id,
                tools=tools if (reads_only and not capped) else None,
            )
            backup_model = backup_model or reply.fallback_model
        body = (reply.text or "").strip()
        if not body:
            body = (
                RAN_OUT_OF_STEPS
                if rounds >= MAX_TOOL_ROUNDS
                else "I have nothing to add on that."
            )
        if backup_model:
            # A backup model answered some of this turn because Claude could
            # not (services/aws_gateway/fallback.py). Said on the reply, never
            # passed off as Claude's.
            from api.services.aws_gateway import fallback

            body = fallback.with_note(body)
    except reply_stop.Stopped as exc:
        # The person pressed Stop: what had formed is the reply, marked so
        # the screen says it is partial rather than presenting it as whole.
        stopped = True
        body = (exc.text or "").strip() or "Stopped before I said anything."
    except settings.OwnKeyMissing as exc:
        # BYOK-1: the account runs on its own keys and none can answer. Said
        # plainly on the thread rather than charged to Decibyl's key.
        failed = True
        body = str(exc)
    except client.BuilderClientError as exc:
        # The vendor's own refusal, already said for a person to read: out
        # of credit, rate limited, a rejected key. "I could not think that
        # through" hid which of those it was, and each is fixed differently.
        logger.error("Decibyl could not answer: {}", exc)
        failed = True
        body = str(exc)
    except Exception as exc:  # noqa: BLE001 - the thread must say something
        logger.error("Decibyl could not answer: {}", exc)
        failed = True
        body = (
            "I could not think that through just now. This is us, not you; "
            "try again in a moment."
        )

    # ``failed`` and ``stopped`` are the turn's state for the screen (task
    # states on screen 04): a refusal must never read as a finished answer.
    outcome: dict[str, Any] = {}
    if failed:
        outcome["failed"] = True
    if stopped:
        outcome["stopped"] = True
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=body[:500],
        payload={
            "body": body,
            "from": NAME,
            "preset": preset,
            # The model that actually answered: the backup model's, on
            # Bedrock, when the fallback brain stood in for Claude.
            "model": (
                f"aws_bedrock:{backup_model}"
                if backup_model
                else f"{model.provider}:{model.model}"
                if model is not None
                else None
            ),
            **({"backup_model": backup_model} if backup_model else {}),
            **outcome,
        },
        in_channel=False,
    )
    # After the row, so the screen swaps the forming text for the row rather
    # than showing a blank between them.
    await reply_draft.clear(organization_id)
    if stopped:
        await reply_stop.clear(organization_id, thread_id)
    # And into the graph, with time, so what the person said on the thread
    # can be asked about later (Family B). No graph, nothing happens.
    try:
        from api.services.knowledge_graph import feed as graph_feed

        await graph_feed.remember_exchange(
            organization_id=organization_id, person_said=text, decibyl_said=body
        )
    except Exception as exc:  # noqa: BLE001 - the reply is already on the thread
        logger.warning("Graph could not remember the exchange: {}", exc)
    # A decision in the person's line goes into the journal (B2), inferred.
    try:
        from api.services.knowledge_graph import decisions

        await decisions.note(organization_id, text, source="thread")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not note decisions from the thread: {}", exc)
    return body


#: How much of an attached file the model is shown, per file and in all.
#:
#: A real vendor spec runs past twelve thousand characters -- the Elock
#: workflow a customer sent is 12,134 -- and clipping the tail of a spec
#: silently drops the closing steps, which is where escalation and
#: disposition live. A brief is built from, not skimmed, so it gets room.
ATTACHMENT_CHARS = 24_000
ATTACHMENTS_CHARS = 48_000
#: How long to wait for a file that arrived a moment ago to be read.
#:
#: Short on purpose. A real document takes longer than any wait worth
#: holding a reply for, so the wait covers only the small-file case and
#: everything else is handled by coming back (see ``unread`` and
#: ``answer_decibyl_message``) rather than by waiting longer.
ATTACHMENT_WAIT_SECONDS = 8


#: How many times Decibyl comes back for a file that was still being read,
#: and how long it leaves between tries. Three tries about a minute apart
#: covers the ordinary document; a file still unread after that is a
#: failed ingestion, and repeating past it would spend an account's credits
#: on the same unanswerable question.
UNREAD_RETRIES = 3
UNREAD_RETRY_SECONDS = 45


async def unread(
    organization_id: int, attachments: list[dict[str, Any]] | None
) -> list[str]:
    """The filenames on this line whose text is not readable yet.

    Decibyl used to answer "it is still being read, I'll let you know as
    soon as it's ready" and then never come back: nothing re-read the
    document and nothing re-ran the turn, so a person who attached a file
    and asked a question about it got a promise and silence. This is what
    makes that sentence true -- the caller asks again in a minute.
    """
    pending: list[str] = []
    for attachment in (attachments or [])[:10]:
        uuid = str(attachment.get("document_uuid") or "")
        if not uuid:
            continue
        try:
            document = await db_client.get_document_by_uuid(
                uuid, organization_id=organization_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not check attachment {}: {}", uuid, exc)
            continue
        # A document that is gone is not pending: it will never arrive, and
        # treating it as pending would retry until the cap for nothing.
        if document is None:
            continue
        # Nor is one whose ingestion failed. This is the whole of the second
        # half of the bug: "pending" meant "has no text", so a document the
        # pipeline had already given up on was indistinguishable from one
        # still in the queue. Decibyl said "still being read" about a file
        # that would never be read, every turn, forever.
        if str(getattr(document, "processing_status", "") or "") == FAILED:
            continue
        if not (getattr(document, "full_text", None) or "").strip():
            pending.append(str(attachment.get("filename") or "the file"))
    return pending


#: The ingestion status that means the text will never arrive. Named rather
#: than compared inline because three places have to agree about it, and a
#: typo in any one of them puts the promise back.
FAILED = "failed"


def _why_there_is_no_text(document: Any, last_try: bool) -> str:
    """What to say about an attached file whose text is not here.

    Three different situations, and saying the wrong one is how this went
    wrong: the block always said "still being read ... you will come back",
    so a failed ingestion produced that promise on every turn and a turn
    that had used up its retries produced it on the way out. The founder
    got the sentence twice about the same file and never got the answer.
    """
    state = str(getattr(document, "processing_status", "") or "")
    if document is None:
        return (
            "(this file is not in the workspace. Say so; do not say you "
            "are reading it.)"
        )
    if state == FAILED:
        reason = (getattr(document, "processing_error", None) or "").strip()
        detail = f" The reason given: {reason[:200]}" if reason else ""
        return (
            f"(could not be read.{detail} Say that plainly, and offer to try "
            "again if they re-upload it. Do NOT say you are still reading it "
            "and do NOT promise to come back -- nothing will.)"
        )
    if last_try:
        return (
            "(not read yet, and this turn will not run again. Say the file "
            "is taking longer than expected and ask them to come back to it, "
            "or offer to look again. Do NOT promise to come back by "
            "yourself -- this was the last try.)"
        )
    return (
        "(still being read. Say you are reading it and will come back with "
        "the answer -- you will: this turn runs again by itself once the "
        "text has landed. Answer whatever else was asked meanwhile.)"
    )


async def attached_block(
    organization_id: int,
    attachments: list[dict[str, Any]] | None,
    *,
    last_try: bool = False,
) -> str:
    """The text of the files on this line, as a context block, or empty.

    A file dropped on the thread is filed and read by the knowledge-base
    pipeline; its text is usually there within seconds. A short wait covers
    the usual case, and a file still being read is named as such so the
    model says so rather than asking what the person meant."""
    import asyncio

    if not attachments:
        return ""
    parts: list[str] = []
    budget = ATTACHMENTS_CHARS
    for attachment in attachments[:10]:
        uuid = str(attachment.get("document_uuid") or "")
        name = str(attachment.get("filename") or "file")
        if not uuid:
            continue
        text = ""
        waited = 0.0
        while waited <= ATTACHMENT_WAIT_SECONDS:
            try:
                document = await db_client.get_document_by_uuid(
                    uuid, organization_id=organization_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not read attachment {}: {}", uuid, exc)
                document = None
            text = (
                (getattr(document, "full_text", None) or "").strip() if document else ""
            )
            if text or document is None:
                break
            await asyncio.sleep(2)
            waited += 2
        if not text:
            parts.append(f"### {name}\n{_why_there_is_no_text(document, last_try)}")
            continue
        take = min(ATTACHMENT_CHARS, budget)
        clipped = text[:take] + (" …" if len(text) > take else "")
        budget -= len(clipped)
        parts.append(f"### {name}\n{clipped}")
        if budget <= 0:
            break
    if not parts:
        return ""
    # Said here as well as in the rules, because this is the block the
    # model is reading when it decides what to do with the file, and a
    # capability named three thousand tokens earlier is a capability that
    # gets forgotten.
    return (
        "## Attached to this line\n"
        + "\n\n".join(parts)
        + "\n\n(If this says what an agent should do, build it: "
        f"{bot_from_brief.TOOL_NAME} with the text above as the spec.)"
    )


#: Tool rounds a single reply may take. Four covers "look it up, then do
#: it" with room for a retry; more is a model going in circles on credit.
#: Six, not four. "Find the newest mail from a real person, skip the
#: automated ones, draft a reply" is a fetch, a look at one or two messages,
#: the draft, and the answer -- five rounds on a good day. At four the turn
#: hit the cap mid-plan and ended on the fallback sentence below, with the
#: inbox read and nothing drafted. Still a cap: a loop of reads cannot spend
#: credit all afternoon.
MAX_TOOL_ROUNDS = 6

#: Said to the model when the cap withdraws its tools. Without it the model
#: is handed nothing mid-plan and, as often as not, says nothing back.
LAST_STEP_NOTICE = (
    "That was your last tool call for this turn. Answer now with what you "
    "have. If you did not finish, say plainly what you read and what is "
    "still left to do, so the person can ask again from there."
)

#: What the person reads when the turn ran out of steps and the model still
#: said nothing. "I have nothing to add on that" was the sentence before,
#: and it was untrue: the model had read the inbox and stopped short of the
#: draft. A gap the person was told about is one they can act on.
RAN_OUT_OF_STEPS = (
    "I ran out of steps before I could finish that. I read what I needed "
    "but did not get to the last part -- ask again and I will start from "
    "there."
)


def office_tools(organization_id: int | None = None) -> list[dict[str, Any]]:
    """Decibyl's own, as opposed to the workspace's connected apps.

    Most end in a card a person confirms. The two document verbs are the
    exception: find runs now, and send is a card for an identity document
    and runs now for anything else.

    Every tool here is named in SYSTEM, and
    ``test_decibyl_knows_what_it_has`` fails when one is added without a
    rule -- a tool the model is handed and never told about is a tool it
    uses by guesswork.
    """
    return [
        actions.tool_schema(),
        office.edit_tool_schema(),
        office.test_tool_schema(),
        office.check_tool_schema(),
        tasks_board.tool_schema(),
        tasks_board.read_tool_schema(),
        routines.schedule_tool_schema(),
        connector_offer.tool_schema(),
        bot_from_brief.tool_schema(),
        documents.find_tool_schema(),
        documents.send_tool_schema(),
        document_fields.tool_schema(),
        filing.tool_schema(),
        recall.tool_schema(),
        teach.tool_schema(),
        quiet.tool_schema(),
        *(
            (
                web_tools.search_tool_schema(),
                web_tools.fetch_tool_schema(),
                prospects.tool_schema(),
                records.tool_schema(),
                skill_imports.tool_schema(),
            )
            if web_tools.enabled()
            else ()
        ),
        *(procurement.schemas() if procurement.enabled() else ()),
        *(tables.schemas() if tables.enabled(organization_id) else ()),
        *(
            (browser_tool.tool_schema(),)
            if browser_tool.enabled(organization_id)
            else ()
        ),
    ]


#: The old name, kept for anything that imported it.
TOOLS = office_tools


async def tools_for(
    organization_id: int, loaded: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The five, plus the workspace's connected apps (see connected_tools).
    A read runs in the turn; a write becomes a run_tool card.

    Connected apps are deferred: each is a name and a line until the model
    loads it, and ``loaded`` carries the schemas this thread has asked for
    so far, so a loaded tool is offered in full on every later round."""
    connected = await connected_tools.list_for_organization(organization_id)
    own = office_tools(organization_id)
    if web_tools.enabled():
        from api.services.sandbox import code_mode

        if await code_mode.allowed(organization_id):
            own = [*own, code_mode.tool_schema()]
    return own + connected_tools.schemas(connected, loaded)


def _was_a_read(call: Any, result: Any) -> bool:
    """Whether this call was a read that actually ran (or failed running),
    as opposed to anything that wrote a card: a connected-app read, or
    find_document, after which the model may still need to send.

    A refusal counts too, and that is the point of it. ``draft_requests``
    turns a send the person did not ask for into a correction naming the
    draft tool to use instead -- advice worth nothing if the refusal also
    ends the turn's tool access. It wrote no card and had no effect, so the
    model keeps its tools and can act on what it was just told. Without
    this, asked for a draft, the model was refused, offered nothing to
    retry with, and the turn ended on "I have nothing to add on that."
    """
    name = str(getattr(call, "name", "") or "")
    if isinstance(result, dict) and result.get("status") == "not_proposed":
        # A proposal turned back before any card was written ("ask for these
        # first", "which template"): the model must be able to ask or retry.
        # Counted as a card, it lost its tools and answered with nothing.
        return True
    if name in tables.NAMES and isinstance(result, dict):
        # Describe, then rank, then export is one answer: a table read keeps
        # the tools open. A handed-over workbook ends the round like a card.
        return name in tables.READS or result.get("status") != "success"
    if name in procurement.NAMES and isinstance(result, dict):
        # Reading a template, a quotation or the register is a read; so is a
        # draft that came back asking for what is missing -- nothing was
        # handed over and the model still has work to do with its tools. A
        # connect card is not: like offer_connector, it ends the round.
        if result.get("status") == "needs_connection":
            return False
        return name in procurement.READS or result.get("status") in (
            "missing",
            "invalid",
            "error",
        )
    return (
        (
            name.startswith(connected_tools.PREFIX)
            or name
            in (
                documents.FIND_TOOL_NAME,
                recall.TOOL_NAME,
                connected_tools.LOAD_TOOL_NAME,
                web_tools.SEARCH_TOOL_NAME,
                web_tools.FETCH_TOOL_NAME,
                prospects.TOOL_NAME,
                records.TOOL_NAME,
                "run_script",
            )
        )
        and isinstance(result, dict)
        and result.get("status") in ("success", "error", "refused")
    )


async def _load_tool(
    organization_id: int, call: Any, loaded: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The model asked for a connected tool's arguments. Answer with the
    schema and remember it, so the next round offers the tool in full."""
    name = str((call.arguments or {}).get("name") or "").strip()
    available = connected_tools.by_function_name(
        await connected_tools.list_for_organization(organization_id)
    )
    tool = available.get(name)
    if tool is None:
        return {
            "status": "unavailable",
            "reason": "no connected tool by that name; use a name from the list",
        }
    result = await connected_tools.load(tool)
    if isinstance(result.get("parameters"), dict):
        loaded[name] = result["parameters"]
    return result


async def _app_tool(
    organization_id: int, call: Any, request: str = ""
) -> dict[str, Any]:
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
    # Asked for a draft, or asked not to send: a send never reaches a card.
    # The card's own effect line (#365) tells the truth about what Confirm
    # does, and the SYSTEM rule beside it asks the model to choose the draft
    # tool -- tried against the live account, and it went on proposing the
    # send and calling it a draft. So this is a refusal rather than advice,
    # handed back as a tool result the model can act on in the same turn.
    refused = draft_requests.refusal(
        text=request, tool=tool, tools=list(available.values())
    )
    if refused is not None:
        return refused
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


async def _tool(
    organization_id: int,
    call: Any,
    author_id: int | None = None,
    request: str = "",
    thread_id: str | None = None,
) -> dict[str, Any]:
    if str(call.name or "").startswith(connected_tools.PREFIX):
        return await _app_tool(organization_id, call, request=request)
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
    if call.name == routines.SCHEDULE_TOOL_NAME:
        return await routines.propose_schedule(
            organization_id=organization_id, arguments=arguments
        )
    if call.name == tasks_board.READ_TOOL_NAME:
        return await tasks_board.read_board(
            organization_id=organization_id, arguments=arguments
        )
    if call.name == tasks_board.TOOL_NAME:
        return await tasks_board.create(
            organization_id=organization_id,
            from_workflow_id=None,
            workflow_run_id=None,
            arguments=arguments,
        )
    if call.name == bot_from_brief.TOOL_NAME:
        # Routed through propose_action so the card, the undo window and the
        # one row that records who confirmed are the same as every other
        # thing Decibyl does to the account.
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={**arguments, "action": actions.BUILD_FROM_SPEC},
            in_channel=False,
        )
    if call.name == connector_offer.TOOL_NAME:
        return await connector_offer.offer(
            organization_id=organization_id, arguments=arguments
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
    if call.name == filing.TOOL_NAME:
        return await filing.file_for_thread(organization_id, arguments)
    if call.name == recall.TOOL_NAME:
        return await recall.for_thread(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    if call.name == teach.TOOL_NAME:
        return await teach.correct(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    if call.name == quiet.TOOL_NAME:
        return await quiet.for_thread(organization_id, author_id, arguments)
    if call.name == web_tools.SEARCH_TOOL_NAME and web_tools.enabled():
        return await web_tools.search(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    if call.name == web_tools.FETCH_TOOL_NAME and web_tools.enabled():
        # One conversation is one run for the page cap (OP-2): a thread
        # that has read its pages for the day answers with what it has.
        return await web_tools.fetch(
            organization_id,
            arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
            run_key=f"thread:{organization_id}:{thread_id or author_id or 'main'}",
        )
    if call.name == prospects.TOOL_NAME and web_tools.enabled():
        return await prospects.save(organization_id, arguments)
    if call.name == records.TOOL_NAME and web_tools.enabled():
        return await records.for_thread(organization_id, arguments)
    if call.name == skill_imports.TOOL_NAME and web_tools.enabled():
        return await skill_imports.for_thread(organization_id, arguments)
    if call.name == "run_script" and web_tools.enabled():
        from api.services.sandbox import code_mode

        if not await code_mode.allowed(organization_id):
            return {"status": "unavailable", "reason": "scripts are not on this plan"}
        return await code_mode.run_for_bot(
            organization_id=organization_id,
            code=str(arguments.get("code") or ""),
            why=str(arguments.get("why") or ""),
            tools=await connected_tools.list_for_organization(organization_id),
            workflow_id=None,
            workflow_run_id=None,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
    if call.name == browser_tool.TOOL_NAME and browser_tool.enabled(organization_id):
        return await browser_tool.for_thread(
            organization_id,
            arguments,
            author_id=author_id,
            request=request,
            thread_id=thread_id,
        )
    if call.name in tables.NAMES and tables.enabled(organization_id):
        return await tables.run(
            call.name, organization_id=organization_id, arguments=arguments
        )
    if call.name in procurement.NAMES and procurement.enabled():
        return await procurement.run(
            call.name,
            organization_id=organization_id,
            arguments=arguments,
            ref_id=f"decibyl:{organization_id}:{call.id or call.name}",
        )
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
            logger.warning("Decibyl could not read the subject agents: {}", exc)
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
    # Stop (screen 04): read on the same beat as the draft is written, so a
    # person's Stop lands within a quarter of a second and the text so far
    # becomes the reply (see reply_stop).
    can_stop = features.is_on("chat_shell", organization_id)

    async def on_text(text: str) -> None:
        # A few writes a second is plenty for a screen polling at that rate,
        # and Redis is not the thing to make wait for a token.
        now = time.monotonic()
        if now - last["at"] < 0.25:
            return
        last["at"] = now
        if can_stop and await reply_stop.requested(
            organization_id, agent_timeline.current_thread()
        ):
            raise reply_stop.Stopped(text)
        await reply_draft.set_draft(organization_id, text)

    # A turn on the account's own key is recorded apart (BYOK-1): the usage
    # report prices "decibyl" at the platform's cost and must not price this.
    feature = (
        "decibyl:byok" if getattr(model, "key_source", "") == "byok" else "decibyl"
    )
    with model_usage.scope(organization_id=organization_id, feature=feature):
        return await client.stream(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=system_prompt(organization_id),
            conversation=conversation,
            on_text=on_text,
            tools=tools,
        )


def recent_window() -> datetime:
    return datetime.now(UTC) - timedelta(days=7)

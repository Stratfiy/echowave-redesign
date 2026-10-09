"""The channel's own conversation, as context for a bot answering in it.

Slack's argument for why its agents are useful is one sentence: *"conversational
data makes agents more contextually relevant."* Not a knowledge base bolted on
the side -- the talk in the room is the context, and being in the room is the
permission. That is a better fit here than a grant model with checkboxes at hire
time, and it is less to build: a bot filed in a channel may read that channel,
because somebody put it there.

What it gets is the same `agent_events` rows the channel screen renders, which
means three of the four context sources arrive at once and without a new table:

* what people said, including corrections -- "no, it is 500 not 400" is just
  the next message, and it is the most recent thing said, which is exactly the
  weight it should carry;
* what *other* bots did and said here, so a follow-up bot sees that another
  already recorded the shipment rather than chasing it again;
* this bot's own earlier turns in the room.

**The window compacts; it does not drop.** The first cut of this took the
newest thirty rows and discarded the rest, which is lossy in exactly the way a
teammate who joined last week is lossy: the correction from six weeks ago that
nobody has contradicted since is the one that matters, and it was the first to
go. So the shape is the one Claude Code's own context uses, and the one
``qa/analysis.py`` already uses per node -- a précis of everything before the
window, the window itself verbatim, and when the window overflows its oldest
end is *folded into the précis* rather than thrown away. Each fold's input is
one summary plus one batch, so the cost stays roughly constant however long the
channel has been running.

The fold is a model call, so it runs **after** a reply, as its own job, never on
the path to one. A person watching for an answer should not wait on
housekeeping. And it borrows the configuration of the run that just answered:
the bot that spoke pays for compacting the thread it spoke in, under its own
LLM, with the tokens recorded the way post-call QA's are.

Facts that are durable -- a price, an address, a policy -- do not depend on the
summary to survive. The same run flows through ``learn_from_run`` into
``organisation_facts``, which is the graph. The summary carries the *narrative*
between those facts; the facts table carries the facts.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.db import db_client
from api.enums import AgentEventActor

#: How many verbatim rows a bot is shown at most. Beyond this the oldest are
#: covered by the summary instead. Thirty is roughly a morning in a busy
#: channel and a week in a quiet one.
MAX_EVENTS = 30

#: The budget the rendered verbatim block has to fit. Tokens on every turn of
#: every reply, so this is a cost decision rather than a formatting one.
MAX_CHARS = 6_000

#: What one line of the thread may carry. A message longer than this is a
#: document somebody pasted, and the bot needs its gist rather than its full
#: text -- which would spend the whole budget on one line.
MAX_LINE = 400

#: How many rows may sit above the watermark before a fold is worth a model
#: call. Set above MAX_EVENTS so a fold happens *after* the window is full,
#: never while it still has room -- and so that a channel that ticks over one
#: message a day is not summarised every day for the sake of it.
COMPACT_AFTER = 40

#: How many of the oldest unsummarised rows one fold takes. Fewer than the
#: window, so the rows a bot was just shown verbatim are still verbatim on the
#: next question; the fold trails the window rather than racing it.
COMPACT_BATCH = 20

#: What the précis is allowed to grow to before the next fold is told to keep
#: it tight. A summary that grows without bound is the original problem wearing
#: a different hat.
MAX_SUMMARY_CHARS = 2_000

#: Said when the verbatim window did not hold everything and no summary exists
#: yet -- the one state in which something is genuinely not shown.
TRUNCATED_NOTE = "(earlier messages in this channel are not shown)"

#: The fold prompt. Told what the summary is for, because a summary written
#: for nobody drifts into a précis of tone; this one is read by a bot deciding
#: what has already been settled.
FOLD_SYSTEM_PROMPT = (
    "You maintain a running summary of a workplace channel where people and AI "
    "agents talk. You will be given the summary so far and the messages since. "
    "Write the new summary: fold the messages into it. Keep every decision, "
    "every correction (a later message overrides an earlier one), every task a "
    "agent reported doing or failing to do, and every open request nobody has "
    "answered. Drop pleasantries and repetition. Write plainly, in the past "
    "tense, in at most 12 short lines. Output only the summary."
)


#: A person whose name we have no right to use. Never their email: an
#: address is contact data, not a name, and a colleague's address in front of
#: a bot is one step from being in its reply.
UNNAMED_PERSON = "A teammate"

#: The person writing now, when they have not told us what to call them.
UNNAMED_ASKER = "The person writing to you"


def _author_of(event: Any) -> Optional[int]:
    """The signed-in person who wrote a row, from its payload, or None."""
    raw = (getattr(event, "payload", None) or {}).get("author_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _speaker(
    event: Any, names: Mapping[int, str], people: Optional[Mapping[int, str]] = None
) -> str:
    """Who said it, as the bot should read it.

    A person is named by the name they asked to be called (``people``, from
    ``_people_for``), never by email. This used to be "Someone" for everybody,
    which made an owner talking to their own agent read, to the agent, like a
    stranger -- and the agent wrote "Someone: Hi" back at them. A person with
    no name on file is "A teammate". A bot is named, because "another bot
    already confirmed the shipment" is useless without knowing which.
    """
    if event.actor == AgentEventActor.HUMAN.value:
        author = _author_of(event)
        if people and author is not None and author in people:
            return people[author]
        return UNNAMED_PERSON
    workflow_id = getattr(event, "workflow_id", None)
    if workflow_id is not None and workflow_id in names:
        return names[workflow_id]
    return "Another agent"


def _text_of(event: Any) -> str:
    # The words somebody chose are kept whole in the payload; `summary`
    # truncates at 500 and is the display line. For a person's message the
    # payload is the real text.
    body = (event.payload or {}).get("body")
    text = body if isinstance(body, str) and body.strip() else (event.summary or "")
    return " ".join(str(text).split())


def _line(
    event: Any, names: Mapping[int, str], people: Optional[Mapping[int, str]] = None
) -> Optional[str]:
    """One row as one line, or None if it carries nothing worth a line."""
    text = _text_of(event)
    if not text:
        return None
    if len(text) > MAX_LINE:
        text = text[: MAX_LINE - 1].rstrip() + "…"
    return f"{_speaker(event, names, people)}: {text}"


def _is_the_question(event: Any, answering: Optional[str]) -> bool:
    """Whether ``event`` is the message being answered right now.

    The route records a person's message before it enqueues the reply, so the
    newest row of the thread is the very message the bot is about to be handed
    on its own. Shown twice, the bot read it as the person repeating
    themselves ("Someone: Hi (repeat)") and answered the transcript instead.
    """
    if not answering or event.actor != AgentEventActor.HUMAN.value:
        return False
    return _text_of(event) == " ".join(answering.split()) or (
        " ".join(str(event.summary or "").split()) == " ".join(answering.split())
    )


def asker_of(events: list[Any], answering: Optional[str]) -> Optional[int]:
    """Who wrote the message being answered: the author of the newest row,
    when that row is the message. None for a hand-off from another bot."""
    if events and _is_the_question(events[0], answering):
        return _author_of(events[0])
    return None


def render(
    events: Iterable[Any],
    names: Mapping[int, str],
    *,
    summary: Optional[str] = None,
    max_chars: int = MAX_CHARS,
    max_events: int = MAX_EVENTS,
    people: Optional[Mapping[int, str]] = None,
    answering: Optional[str] = None,
    asker_id: Optional[int] = None,
    direct: bool = False,
) -> Optional[str]:
    """The thread as a block: the précis, then the window, newest-last.

    ``events`` arrive newest-first, the order `agent_events` returns. The block
    reads oldest-first, because a conversation does.

    ``max_chars`` and ``max_events`` are the window. The defaults are the
    floor; a plan's chat memory (chat_memory) widens both.

    ``answering`` is the message the bot is about to answer. It is handed to
    the bot on its own, after this block, so its row is left out here rather
    than shown twice. ``asker_id`` is who wrote it; ``people`` names people.
    ``direct`` is a bot's own chat rather than a channel.
    """
    ordered = list(events)
    read = len(ordered)
    if ordered and _is_the_question(ordered[0], answering):
        ordered = ordered[1:]
    asker = (people or {}).get(asker_id) if asker_id is not None else None
    labels = dict(people or {})
    if asker_id is not None and not asker:
        labels[asker_id] = UNNAMED_ASKER
    summary = (summary or "").strip() or None
    if not ordered and not summary:
        return None

    # Build from the newest backwards so the budget is spent on what matters,
    # then reverse. Trimming a chronological list from the front would mean
    # rendering lines only to throw them away.
    lines: list[str] = []
    used = 0
    overflowed = False
    for event in ordered:
        line = _line(event, names, labels)
        if line is None:
            continue
        if used + len(line) + 1 > max_chars:
            overflowed = True
            break
        lines.append(line)
        used += len(line) + 1
    if read >= max_events:
        overflowed = True
    lines.reverse()

    if not lines and not summary:
        return None

    where = "your chat" if direct else "this channel"
    parts = [
        f"WHAT HAS BEEN SAID IN {where.upper()} BEFORE NOW.",
        (
            f"This is background, not a request. It is the conversation in {where} "
            "so far, there so you can answer the way a colleague who has been "
            "reading along would: it tells you what has already been asked, what "
            "another agent has already done, and what anybody has corrected. A "
            "later message outranks an earlier one. Do not repeat work another "
            "agent has already reported here."
        ),
    ]
    if asker_id is not None:
        parts.append(
            f"{asker} is the person writing to you now. You are talking to "
            f"{asker}: speak to them as you, not about them."
            if asker
            else "Lines from the person writing to you now are marked "
            f'"{UNNAMED_ASKER}". Speak to them as you, not about them.'
        )
    if summary:
        parts.append(f"Earlier in {where}, in summary:\n" + summary)
    elif overflowed:
        # Something is genuinely not shown and nothing covers it: the fold has
        # not run yet. Said out loud so the bot knows it came in late.
        parts.append(TRUNCATED_NOTE)
    if lines:
        parts.append("Recent messages, oldest first:\n" + "\n".join(lines))
    return "\n".join(parts)


async def _window(organization_id: int) -> tuple[int, int]:
    """``(max_chars, max_events)`` for this account: the plan's memory.

    Never narrower than the module floor. The line cap stays: a pasted
    contract is still one line's worth of thread, whatever the plan.
    """
    from api.services.workflow import chat_memory

    plan = await chat_memory.budget(organization_id)
    chars = max(MAX_CHARS, plan.tokens * chat_memory.CHARS_PER_TOKEN)
    events = min(chat_memory.MAX_ROWS, max(MAX_EVENTS, chars // MAX_LINE))
    return chars, events


async def _names_for(organization_id: int) -> dict[int, str]:
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    return {w.id: w.name for w in workflows if getattr(w, "name", None)}


async def _people_for(organization_id: int, events: Iterable[Any]) -> dict[int, str]:
    """``author_id -> name`` for the people who wrote in ``events``.

    The name is the one each person asked to be called (Settings, or the
    onboarding question that seeds it) and nothing else: never an email, never
    a phone number. Only members of this organisation are looked up, so a row
    that somehow names an outsider resolves to "A teammate" rather than to
    somebody else's account. A person with no name on file is left out and
    renders as "A teammate".

    Empty on any failure: a thread with unnamed people is still a thread.
    """
    ids = {a for a in (_author_of(e) for e in events) if a is not None}
    if not ids:
        return {}
    try:
        from sqlalchemy import select

        from api.db.controls_models import MemberPreferencesModel
        from api.db.models import OrganizationMembershipModel
        from api.db.shell_models import UserOnboardingModel

        async with db_client.async_session() as session:
            members = set(
                (
                    await session.execute(
                        select(OrganizationMembershipModel.user_id).where(
                            OrganizationMembershipModel.organization_id
                            == organization_id,
                            OrganizationMembershipModel.user_id.in_(ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not members:
                return {}
            chosen = dict(
                (
                    await session.execute(
                        select(
                            MemberPreferencesModel.user_id,
                            MemberPreferencesModel.preferred_name,
                        ).where(MemberPreferencesModel.user_id.in_(members))
                    )
                ).all()
            )
            onboarding = dict(
                (
                    await session.execute(
                        select(
                            UserOnboardingModel.user_id,
                            UserOnboardingModel.preferred_name,
                        ).where(UserOnboardingModel.user_id.in_(members))
                    )
                ).all()
            )
    except Exception as exc:  # noqa: BLE001 - names are an improvement, not a dependency
        logger.warning("Could not name the people in a thread: {}", exc)
        return {}
    people: dict[int, str] = {}
    for user_id in members:
        name = " ".join(
            str(chosen.get(user_id) or onboarding.get(user_id) or "").split()
        )
        if name:
            people[int(user_id)] = name
    return people


async def recent_thread(
    *,
    organization_id: Optional[int],
    folder_id: Optional[int],
    answering: Optional[str] = None,
) -> Optional[str]:
    """The channel's conversation, ready to put in front of a bot.

    The précis covers every row at or below the watermark; the window is the
    rows above it. Nothing is in both and nothing is in neither -- that is the
    invariant ``set_folder_context_summary`` writes atomically.

    ``answering`` is the message the bot is about to be handed; see ``render``.

    Empty on any failure. A bot that answers without the thread is a bot that
    answers less well; a bot that raises because the thread could not be read
    is a message nobody replies to, and silence is the one outcome this whole
    path exists to avoid.
    """
    if not organization_id or not folder_id:
        return None
    try:
        folder = await db_client.get_folder(folder_id, organization_id=organization_id)
        if folder is None:
            return None
        max_chars, max_events = await _window(organization_id)
        rows = await db_client.agent_events(
            organization_id=organization_id,
            folder_id=folder_id,
            after_id=folder.context_summarised_through,
            limit=max_events,
        )
        names = await _names_for(organization_id)
    except Exception as exc:  # noqa: BLE001 - context is an improvement, not a dependency
        logger.warning("Could not read channel {} for context: {}", folder_id, exc)
        return None
    return render(
        rows,
        names,
        summary=folder.context_summary,
        max_chars=max_chars,
        max_events=max_events,
        people=await _people_for(organization_id, rows),
        answering=answering,
        asker_id=asker_of(rows, answering),
    )


async def recent_bot_thread(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    answering: Optional[str] = None,
) -> Optional[str]:
    """A bot's own chat, for when somebody talks to it directly.

    No précis: a bot's thread has no watermark of its own yet, so this is the
    window alone -- the last MAX_EVENTS rows across everything the bot did,
    which is what the person on its chat is looking at.
    """
    if not organization_id or not workflow_id:
        return None
    try:
        max_chars, max_events = await _window(organization_id)
        rows = await db_client.agent_events(
            organization_id=organization_id, workflow_id=workflow_id, limit=max_events
        )
        names = await _names_for(organization_id)
    except Exception as exc:  # noqa: BLE001 - context is an improvement, not a dependency
        logger.warning(
            "Could not read agent {} thread for context: {}", workflow_id, exc
        )
        return None
    return render(
        rows,
        names,
        max_chars=max_chars,
        max_events=max_events,
        people=await _people_for(organization_id, rows),
        answering=answering,
        asker_id=asker_of(rows, answering),
        direct=True,
    )


async def compact(*, organization_id: int, folder_id: int, run_id: int) -> bool:
    """Fold the oldest unsummarised stretch of a channel into its précis.

    Runs after a reply, as its own job. ``run_id`` is the run that just
    answered here: its LLM configuration is what the fold runs on, and its
    correlation id is what the tokens are billed against.

    Returns True when the watermark moved. Never raises -- a fold that fails
    leaves the channel exactly as it was, which is a channel that still works.
    """
    try:
        folder = await db_client.get_folder(folder_id, organization_id=organization_id)
        if folder is None:
            return False

        # Everything above the watermark, oldest first, so the batch is the
        # oldest end and the window a bot is shown stays the newest end.
        pending = await db_client.agent_events(
            organization_id=organization_id,
            folder_id=folder_id,
            after_id=folder.context_summarised_through,
            limit=COMPACT_AFTER + COMPACT_BATCH,
        )
        if len(pending) < COMPACT_AFTER:
            return False
        pending.reverse()
        batch = pending[:COMPACT_BATCH]

        names = await _names_for(organization_id)
        people = await _people_for(organization_id, batch)
        lines = [line for line in (_line(e, names, people) for e in batch) if line]
        if not lines:
            # Nothing renderable in the batch -- still advance, or this batch
            # blocks every later one forever.
            return await db_client.set_folder_context_summary(
                folder_id,
                organization_id,
                summary=folder.context_summary or "",
                summarised_through=batch[-1].id,
            )

        summary = await _fold(
            previous=folder.context_summary or "",
            transcript="\n".join(lines),
            run_id=run_id,
        )
        if not summary:
            return False
        return await db_client.set_folder_context_summary(
            folder_id,
            organization_id,
            summary=summary[:MAX_SUMMARY_CHARS],
            summarised_through=batch[-1].id,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("Could not compact channel {}: {}", folder_id, exc)
        return False


async def _fold(*, previous: str, transcript: str, run_id: int) -> Optional[str]:
    """One summary plus one batch in; one summary out.

    Built the way ``qa/analysis.py`` builds its post-call inference, and for
    the same reason it resolves the model from a run rather than an
    organisation: that is the only place the configured LLM, its key and its
    billing correlation id all live together.
    """
    from api.services.managed_model_services import get_mps_correlation_id
    from api.services.pipecat.service_factory import create_llm_service_from_provider
    from api.services.workflow.qa.llm_config import (
        accumulate_token_usage,
        resolve_user_llm_config,
    )

    workflow_run = await db_client.get_workflow_run(run_id)
    if workflow_run is None:
        return None
    provider, model, api_key, service_kwargs = await resolve_user_llm_config(
        workflow_run
    )
    llm = create_llm_service_from_provider(
        provider,
        model,
        api_key,
        correlation_id=get_mps_correlation_id(
            getattr(workflow_run, "initial_context", None)
        ),
        **service_kwargs,
    )

    content = (
        f"## Summary so far\n{previous}\n\n## Messages since\n{transcript}"
        if previous.strip()
        else f"## Messages\n{transcript}"
    )
    if len(previous) > MAX_SUMMARY_CHARS:
        content += "\n\nThe summary so far is too long; make the new one shorter."

    context = LLMContext()
    context.set_messages([{"role": "user", "content": content}])
    text = await llm.run_inference(context, system_instruction=FOLD_SYSTEM_PROMPT)
    usage: dict = {}
    accumulate_token_usage(usage, getattr(llm, "last_inference_usage", None))
    if usage:
        logger.info("Channel fold on run {} used {}", run_id, usage)
    return (text or "").strip() or None


__all__ = [
    "COMPACT_AFTER",
    "COMPACT_BATCH",
    "MAX_CHARS",
    "MAX_EVENTS",
    "MAX_LINE",
    "MAX_SUMMARY_CHARS",
    "TRUNCATED_NOTE",
    "UNNAMED_ASKER",
    "UNNAMED_PERSON",
    "asker_of",
    "compact",
    "recent_thread",
    "render",
]

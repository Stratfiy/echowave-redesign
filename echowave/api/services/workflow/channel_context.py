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

import re
from typing import Any, Iterable, Mapping, Optional

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.db import db_client
from api.enums import AgentEventActor, AgentEventVisibility

#: How many verbatim rows a bot is shown at most. Beyond this the oldest are
#: covered by the summary instead. Thirty is roughly a morning in a busy
#: channel and a week in a quiet one.
MAX_EVENTS = 30

#: The budget the rendered verbatim block has to fit. Tokens on every turn of
#: every reply, so this is a cost decision rather than a formatting one.
MAX_CHARS = 6_000

#: What one line of the verbatim window may carry. A message longer than this
#: is a document somebody pasted, and the window shows its head and its tail
#: rather than spend the whole budget on one line. A preview only: a fold
#: reads the whole message (see "Folding" below).
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

#: What the précis's narrative may be. A fold whose summary comes back longer
#: is asked again within this budget, and then loses whole lines -- never part
#: of one. A summary that grows without bound is the original problem wearing
#: a different hat. The lines kept word for word have their own budget,
#: MAX_NOTES_CHARS.
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
    "every correction (a later message overrides an earlier one), every "
    "cancellation, price, date and promise, every task an agent reported doing "
    "or failing to do, and every open request nobody has answered. Each message "
    "starts with its event id in brackets, like [#123]; a long one arrives in "
    "parts marked [#123, continued]. Put the id after any correction, "
    "cancellation, price, date or promise you keep. Drop pleasantries and "
    "repetition. Write plainly, in the past tense, one fact per line, in at "
    "most 12 short lines. Output only the summary."
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
    return f"{_speaker(event, names, people)}: {_clip(text, MAX_LINE)}"


def _clip(text: str, limit: int) -> str:
    """``text`` as a preview of at most ``limit`` characters: its head and its
    tail, on word boundaries, with " … " where the middle was.

    Head *and* tail because the end of a long message is where people put
    the thing that changes it -- "CORRECTION: the meeting is cancelled" after
    a page of background. A head-only cut showed the background and hid that.
    The whole message still reaches the fold; this is only what the window
    shows.
    """
    if len(text) <= limit:
        return text
    gap = " … "
    head_room = (limit - len(gap)) * 3 // 5
    tail_room = limit - len(gap) - head_room
    head = text[:head_room]
    space = head.rfind(" ")
    head = head[:space] if space > head_room // 2 else head
    tail = text[-tail_room:]
    space = tail.find(" ")
    tail = tail[space + 1 :] if 0 <= space < tail_room // 2 else tail
    return head.rstrip() + gap + tail.lstrip()


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


# ---------------------------------------------------------------------------
# Folding
#
# Order. The watermark is an ``agent_events.id``, so a fold reads, consumes and
# fences on ``id`` and nothing else: ``channel_events_to_compact`` returns the
# rows above the watermark oldest-first *by id*, the batch is a prefix of that,
# and the new watermark is the batch's last id. Every row at or below it has
# been read by a fold. Insertion order, not timestamp order, is the fold's
# chronology -- deliberately. ``at`` can be set by a caller and two workers'
# clocks disagree, and a watermark on one key with a read on the other skips
# rows (the original bug: a newest-first read, limited, folded from the
# middle). A row whose ``at`` is earlier than rows already folded is still
# above the watermark by id, so it is shown verbatim in the window (which does
# sort by ``at``) until its own turn to fold. Nothing is skipped either way.
#
# The one gap ids leave: an id is taken at insert and becomes visible at
# commit, so a row could in principle commit after a later id was folded past
# it. ``record_agent_event`` commits straight after its flush, and a batch's
# last id is at least ``COMPACT_AFTER - COMPACT_BATCH`` committed rows behind
# the newest, so that window is far narrower than the trailing gap.
#
# Text. The verbatim window clips a long message to one line (``_line``);
# that is a preview, and it never feeds a fold. A fold reads each message's
# full text, split into bounded chunks, one model call per chunk. Separately,
# and without a model, every sentence that carries a correction, a
# cancellation, a negation, a price, a date or a promise is kept word for
# word, with its event id, under the précis (``key_notes``). The model's
# narrative is the part that may lose detail; those lines are the part that
# may not. The full original stays in ``agent_events`` (``source_text``).
# ---------------------------------------------------------------------------

#: How many folds one job may run. A channel that piled up a backlog while
#: the compactor was down drains it over a few replies, a bounded number of
#: model calls at a time, rather than in one job nobody can see the end of.
MAX_FOLDS_PER_RUN = 5

#: What one fold model call is handed as transcript, at most.
FOLD_CHUNK_CHARS = 8_000

#: Model calls one fold may spend on its batch. A batch of pasted documents
#: that would need more is shortened to the prefix that fits (never below one
#: message); the rest is the next fold's.
MAX_FOLD_CALLS = 4

#: How much of one message the model reads. Beyond this the transcript says
#: so in words -- never a silent cut -- and the key lines below, taken from
#: the whole message, are what carries the rest.
MAX_SOURCE_CHARS = 16_000

#: One kept line's width. A sentence longer than this is kept around the word
#: that made it worth keeping, marked with an ellipsis; its event id points at
#: the whole thing.
MAX_NOTE_CHARS = 280

#: What the kept lines may add to the précis. Separate from the narrative's
#: budget so a long narrative cannot crowd out a correction.
MAX_NOTES_CHARS = 1_500

#: The line that separates the model's narrative from the lines kept word for
#: word. It is how a later fold tells the two apart in the stored summary.
NOTES_HEADER = (
    "Exact words kept from earlier messages (#event id; a later line outranks "
    "an earlier one):"
)


# Why a sentence is kept, strongest first. Each is a net cast wide on purpose
# -- the worst case of an extra kept line is a little budget, the worst case
# of a missing one is a cancelled meeting somebody turns up to. Latin-script
# words match on word boundaries; Devanagari and Tamil match as substrings,
# because their vowel signs are not word characters to ``re``. Romanised Hindi
# and Tamil ("cancel ho gaya", "illai") are listed with the English.
def _tier_patterns() -> tuple[tuple[int, "re.Pattern[str]"], ...]:
    def words(*w: str) -> str:
        return r"\b(?:" + "|".join(w) + r")\b"

    def subs(*s: str) -> str:
        return "(?:" + "|".join(re.escape(x) for x in s) + ")"

    correction = "|".join(
        [
            words(
                r"correct\w*",
                r"actually",
                r"instead",
                r"cancel\w*",
                r"call(?:ed|ing)? off",
                r"postpone\w*",
                r"resched\w*",
                r"moved? to",
                r"chang\w*",
                r"revis\w*",
                r"wrong",
                r"mistake\w*",
                r"scratch that",
                r"ignore",
                r"no longer",
                r"galat",
                r"badal\w*",
                r"radd",
                r"jagah",
                r"asal",
                r"thappu",
                r"maathi\w*",
                r"maatr\w*",
            ),
            subs(
                "रद्द",
                "कैंसल",
                "कैंसिल",
                "गलत",
                "ग़लत",
                "बदल",
                "स्थगित",
                "की जगह",
                "असल में",
                "ரத்து",
                "தவறு",
                "திருத்த",
                "மாற்ற",
                "ஒத்திவை",
                "கேன்சல்",
                "பதிலாக",
            ),
        ]
    )
    negation = "|".join(
        [
            words(
                r"not",
                r"no",
                r"never",
                r"\w+n[’']t",
                r"cannot",
                r"nahi",
                r"nahin",
                r"nai",
                r"mat",
                r"illa",
                r"illai",
                r"vendaam",
                r"vendam",
                r"venam",
            ),
            subs("नहीं", "नही", "मत ", "இல்லை", "வேண்டாம்", "முடியாது"),
        ]
    )
    figures = "|".join(
        [
            r"\d",
            r"[₹$€£]",
            words(
                r"rs\.?",
                r"inr",
                r"rupees?",
                r"price\w*",
                r"cost\w*",
                r"rate",
                r"amount",
                r"fees?",
                r"discount\w*",
                r"jan(?:uary)?",
                r"feb(?:ruary)?",
                r"mar(?:ch)?",
                r"apr(?:il)?",
                r"june?",
                r"july?",
                r"aug(?:ust)?",
                r"sept?(?:ember)?",
                r"oct(?:ober)?",
                r"nov(?:ember)?",
                r"dec(?:ember)?",
                r"(?:mon|tues|wednes|thurs|fri|satur|sun)day",
                r"today",
                r"tonight",
                r"tomorrow",
                r"yesterday",
                r"[ap]\.?m\.?",
                r"aaj",
                r"kal",
                r"parso",
                r"naalai",
                r"indru",
                r"netru",
            ),
            subs(
                "रुपये",
                "रुपए",
                "रु.",
                "कीमत",
                "दाम",
                "आज",
                "कल",
                "परसों",
                "बजे",
                "वार",
                "ரூபாய்",
                "விலை",
                "இன்று",
                "நாளை",
                "நேற்று",
                "மணி",
                "கிழமை",
            ),
        ]
    )
    commitment = "|".join(
        [
            words(
                r"will",
                r"shall",
                r"\w+[’']ll",
                r"promis\w*",
                r"commit\w*",
                r"deadline",
                r"due",
                r"confirm\w*",
                r"agree\w*",
                r"deliver\w*",
                r"send\w*",
                r"pay\w*",
                r"guarantee\w*",
                r"karenge",
                r"karunga",
                r"karungi",
                r"dunga",
                r"dungi",
                r"bhej\w*",
                r"pakka",
                r"anupp\w*",
                r"pannuven",
                r"kandippa",
            ),
            subs(
                "करेंगे",
                "करूँगा",
                "करूंगा",
                "करूंगी",
                "दूंगा",
                "दूँगा",
                "भेज",
                "पक्का",
                "वादा",
                "அனுப்ப",
                "செய்வேன்",
                "கட்டாயம்",
                "உறுதி",
                "கொடுப்பேன்",
            ),
        ]
    )
    return tuple(
        (tier, re.compile(pattern, re.IGNORECASE))
        for tier, pattern in (
            (0, correction),
            (1, negation),
            (2, figures),
            (3, commitment),
        )
    )


_TIERS = _tier_patterns()

#: Rank of a line that matches none of the tiers: the first to go when a
#: narrative must shrink.
_UNRANKED = len(_TIERS)

# A sentence ends at ! ? । ॥ or a newline, and at a full stop followed by a
# space and something that is not a digit or a lowercase Latin letter -- so
# "Rs. 450" and "approx. ten" stay whole and "cancelled. Meeting" splits.
_SENTENCE_END = re.compile(r"(?<=[!?।॥])\s+|(?<=\.)\s+(?=[^\da-z\s])|\s*\n+\s*")


def _rank(text: str) -> tuple[int, Optional["re.Match[str]"]]:
    """The strongest reason ``text`` matters, and where it was found."""
    for tier, pattern in _TIERS:
        found = pattern.search(text)
        if found:
            return tier, found
    return _UNRANKED, None


def _sentences(text: str) -> list[str]:
    parts = (" ".join(p.split()) for p in _SENTENCE_END.split(text or ""))
    return [p for p in parts if p]


def _around(sentence: str, at: int) -> str:
    """``sentence`` cut to MAX_NOTE_CHARS around position ``at``, on word
    boundaries, with an ellipsis wherever something was left out."""
    if len(sentence) <= MAX_NOTE_CHARS:
        return sentence
    start = max(0, at - MAX_NOTE_CHARS // 4)
    end = min(len(sentence), start + MAX_NOTE_CHARS - 2)
    start = max(0, end - (MAX_NOTE_CHARS - 2))
    if start > 0:
        space = sentence.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    if end < len(sentence):
        space = sentence.rfind(" ", at, end)
        end = space if space > at else end
    piece = sentence[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(sentence) else "")


def _raw_text(event: Any) -> str:
    """The words as written: the payload body when there is one, else the
    display line. Unlike ``_text_of``, whitespace is left as it was."""
    body = (getattr(event, "payload", None) or {}).get("body")
    if isinstance(body, str) and body.strip():
        return body
    return str(getattr(event, "summary", None) or "")


def key_notes(
    event: Any, names: Mapping[int, str], people: Optional[Mapping[int, str]] = None
) -> list[str]:
    """The sentences of one message that must survive a fold word for word.

    Every sentence of the *whole* message is looked at, however long, so a
    correction at the end of a pasted page is found. Each comes back as one
    kept line: ``- [#<event id>] <speaker>: <the sentence>``.
    """
    lines = []
    speaker = _speaker(event, names, people)
    for sentence in _sentences(_raw_text(event)):
        tier, found = _rank(sentence)
        if found is None:
            continue
        lines.append(f"- [#{event.id}] {speaker}: {_around(sentence, found.start())}")
    return lines


_NOTE_ID = re.compile(r"^- \[#(\d+)\]")


def split_summary(stored: Optional[str]) -> tuple[str, list[str]]:
    """A stored précis as ``(narrative, kept lines)``."""
    narrative, header, notes = (stored or "").partition(NOTES_HEADER)
    lines = [l.strip() for l in notes.splitlines() if l.strip()] if header else []
    return narrative.strip(), lines


def join_summary(narrative: str, notes: list[str]) -> str:
    narrative = (narrative or "").strip()
    if not notes:
        return narrative
    block = NOTES_HEADER + "\n" + "\n".join(notes)
    return f"{narrative}\n\n{block}" if narrative else block


def _note_id(line: str) -> int:
    found = _NOTE_ID.match(line)
    return int(found.group(1)) if found else 0


def _fit_notes(notes: list[str]) -> list[str]:
    """The kept lines, de-duplicated, oldest first, within MAX_NOTES_CHARS.

    When they do not fit, whole lines go -- the weakest reason first
    (a promise, then a figure, then a plain negation, a correction last) and
    the oldest within it. Never silently: the ids that went are logged, and
    each is still in ``agent_events`` word for word.
    """
    seen: set[str] = set()
    unique = []
    for line in notes:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    kept = sorted(enumerate(unique), key=lambda p: (_note_id(p[1]), p[0]))
    size = sum(len(l) + 1 for _, l in kept)
    dropped = []
    while kept and size > MAX_NOTES_CHARS:
        weakest = max(
            range(len(kept)),
            key=lambda i: (_rank(kept[i][1])[0], -i),
        )
        _, line = kept.pop(weakest)
        size -= len(line) + 1
        dropped.append(_note_id(line))
    if dropped:
        logger.warning(
            "Channel précis dropped {} kept line(s) over budget, from events {}",
            len(dropped),
            sorted(set(dropped)),
        )
    return [l for _, l in kept]


def _drop_to_fit(text: str, limit: int) -> str:
    """``text`` within ``limit`` by dropping whole lines -- never by cutting
    one. Least important first: a line with nothing the tiers recognise, then
    a promise, a figure, a negation, a correction last; the oldest first
    within each. A single line too long to fit is taken a sentence at a time.
    """
    items: list[str] = []
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        items.extend(_sentences(line) if len(line) > limit else [line])
    size = sum(len(i) + 1 for i in items)
    dropped = 0
    while items and size > limit:
        weakest = max(range(len(items)), key=lambda i: (_rank(items[i])[0], -i))
        size -= len(items.pop(weakest)) + 1
        dropped += 1
    if dropped:
        logger.warning(
            "Channel précis over budget after a retry; dropped {} whole line(s)",
            dropped,
        )
    return "\n".join(items)


def _fold_entries(
    event: Any, names: Mapping[int, str], people: Optional[Mapping[int, str]]
) -> list[str]:
    """One message as fold input: its full text, tagged with its event id,
    in pieces no bigger than a chunk."""
    text = _text_of(event)
    if not text:
        return []
    if len(text) > MAX_SOURCE_CHARS:
        cut = text.rfind(" ", 0, MAX_SOURCE_CHARS)
        cut = cut if cut > 0 else MAX_SOURCE_CHARS
        text = (
            text[:cut]
            + f" [... {len(text) - cut} more characters not shown here; their "
            "key lines are kept separately and the full message is "
            f"event #{event.id}]"
        )
    head = f"[#{event.id}] {_speaker(event, names, people)}: "
    room = FOLD_CHUNK_CHARS - len(head) - 32
    pieces = []
    while text:
        if len(text) <= room:
            pieces.append(text)
            break
        cut = text.rfind(" ", 0, room)
        cut = cut if cut > 0 else room
        pieces.append(text[:cut])
        text = text[cut:].lstrip()
    return [
        (head if i == 0 else f"[#{event.id}, continued] ") + piece
        for i, piece in enumerate(pieces)
    ]


def _pack(entries: list[str]) -> list[str]:
    chunks: list[str] = []
    for entry in entries:
        if chunks and len(chunks[-1]) + 1 + len(entry) <= FOLD_CHUNK_CHARS:
            chunks[-1] += "\n" + entry
        else:
            chunks.append(entry)
    return chunks


def _plan(
    batch: list[Any],
    names: Mapping[int, str],
    people: Optional[Mapping[int, str]],
) -> tuple[list[Any], list[str]]:
    """The prefix of ``batch`` one fold can afford, and its chunks.

    A prefix, never a selection: the watermark will be its last id, so every
    row up to there has to be in it.
    """
    entries: list[str] = []
    used = 0
    for event in batch:
        mine = _fold_entries(event, names, people)
        if used and len(_pack(entries + mine)) > MAX_FOLD_CALLS:
            break
        entries.extend(mine)
        used += 1
    return batch[:used], _pack(entries)


async def compact(*, organization_id: int, folder_id: int, run_id: int) -> bool:
    """Fold the oldest unsummarised stretch of a channel into its précis.

    Runs after a reply, as its own job. ``run_id`` is the run that just
    answered here: its LLM configuration is what the fold runs on, and its
    correlation id is what the tokens are billed against.

    Folds again while a backlog remains, up to MAX_FOLDS_PER_RUN, so a channel
    that was not compacted for a while catches up.

    Returns True when the watermark moved. Never raises -- a fold that fails
    leaves the channel exactly as it was, which is a channel that still works.
    """
    moved = False
    for _ in range(MAX_FOLDS_PER_RUN):
        try:
            folded = await _fold_next(
                organization_id=organization_id, folder_id=folder_id, run_id=run_id
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("Could not compact channel {}: {}", folder_id, exc)
            break
        if not folded:
            break
        moved = True
    return moved


async def _fold_next(*, organization_id: int, folder_id: int, run_id: int) -> bool:
    """One fold: the next prefix of the backlog into the précis. True when
    the watermark moved."""
    folder = await db_client.get_folder(folder_id, organization_id=organization_id)
    if folder is None:
        return False
    through = folder.context_summarised_through

    # The oldest rows above the watermark, by the key the watermark is on.
    pending = await db_client.channel_events_to_compact(
        organization_id=organization_id,
        folder_id=folder_id,
        after_id=through,
        limit=COMPACT_AFTER,
    )
    if len(pending) < COMPACT_AFTER:
        return False

    names = await _names_for(organization_id)
    people = await _people_for(organization_id, pending[:COMPACT_BATCH])
    batch, chunks = _plan(pending[:COMPACT_BATCH], names, people)

    narrative, notes = split_summary(folder.context_summary)
    for event in batch:
        notes.extend(key_notes(event, names, people))

    # No chunks (nothing renderable) still advances below, or this batch
    # blocks every later one.
    for chunk in chunks:
        folded = await _fold(previous=narrative, transcript=chunk, run_id=run_id)
        if not folded:
            return False
        if len(folded) > MAX_SUMMARY_CHARS:
            # Regenerate within budget once; then drop whole lines. A
            # summary sliced mid-sentence can turn "not cancelled" into
            # "not".
            again = await _fold(
                previous=narrative,
                transcript=chunk,
                run_id=run_id,
                max_chars=MAX_SUMMARY_CHARS,
            )
            folded = (
                again
                if again and len(again) <= MAX_SUMMARY_CHARS
                else _drop_to_fit(again or folded, MAX_SUMMARY_CHARS)
            )
        if folded:
            narrative = folded
        else:
            logger.warning(
                "Channel {} fold left no narrative within budget; kept the "
                "previous one",
                folder_id,
            )

    advanced = await db_client.set_folder_context_summary(
        folder_id,
        organization_id,
        summary=join_summary(narrative, _fit_notes(notes)),
        summarised_through=batch[-1].id,
        expected_through=through,
    )
    if not advanced:
        # Another fold moved the watermark first. This one's summary was built
        # from a précis that is no longer current, so it is discarded whole.
        logger.info(
            "Channel {} fold from watermark {} lost a race; discarded",
            folder_id,
            through,
        )
    return advanced


async def source_text(
    *, organization_id: int, folder_id: int, event_id: int
) -> Optional[str]:
    """The exact words of one channel message, as written, by its event id --
    the id a kept line in the précis carries. None when the row is not a
    message of this channel that its context read would show."""
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.folder_id != folder_id:
        return None
    if event.visibility != AgentEventVisibility.ALWAYS.value:
        return None
    if (event.payload or {}).get("private_to") is not None:
        return None
    return _raw_text(event) or None


async def _fold(
    *,
    previous: str,
    transcript: str,
    run_id: int,
    max_chars: Optional[int] = None,
) -> Optional[str]:
    """One summary plus one chunk in; one summary out.

    Built the way ``qa/analysis.py`` builds its post-call inference, and for
    the same reason it resolves the model from a run rather than an
    organisation: that is the only place the configured LLM, its key and its
    billing correlation id all live together.

    ``max_chars`` is a retry's hard budget; the first call has the prompt's.
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
    if max_chars is not None:
        content += (
            f"\n\nThe new summary must be at most {max_chars} characters. Drop "
            "the least important lines whole; never drop a correction, a "
            "cancellation, a price, a date or a promise."
        )
    elif len(previous) > MAX_SUMMARY_CHARS:
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
    "MAX_NOTES_CHARS",
    "MAX_SUMMARY_CHARS",
    "NOTES_HEADER",
    "TRUNCATED_NOTE",
    "UNNAMED_ASKER",
    "UNNAMED_PERSON",
    "asker_of",
    "compact",
    "join_summary",
    "key_notes",
    "recent_thread",
    "render",
    "source_text",
    "split_summary",
]

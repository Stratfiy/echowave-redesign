"""The whole request, held under what the model can take.

Two budgets, and they are different things:

* **The plan's allowance** (``chat_memory``, ``chat_context_tokens``) is how
  much of a conversation an account keeps in mind. It is a product figure: it
  grows with the plan, the meter in the composer shows it, and it covers the
  thread only.
* **The request ceiling** (here) is how big one request to a vendor may be
  before the vendor refuses it. It is a technical figure, it covers
  everything that goes on the wire -- the system prompt, every message, tool
  schemas, tool results, retrieved passages, pictures -- and it has to leave
  room for the reply.

``chat_memory.window`` deliberately keeps the newest message even when it
alone is over the plan's allowance (the question being answered is never
dropped), and nothing counted the rest of the request at all. A pasted
contract, a connected app returning a whole mailbox, or a Tamil thread
counted at the Latin rate could all make a request the vendor refused,
which the person sees as "I could not think that through". This module is
the last check before the wire.

**Estimated, conservatively.** No vendor tokenizer runs here (there is none
in the environment for most vendors, and the three disagree), so the count
is a bound meant to sit *above* what any of them reports: three characters
a token for Latin text where the plan meter uses four, and one and a half
tokens a character for Indian scripts, which vendors' tokenizers split far
worse than English (a Tamil or Telugu character is three UTF-8 bytes and is
often more than one token). Every request also logs the estimate against
the vendor's own count when the reply reports one (:func:`reconcile`), so
the bound can be checked against what was actually billed.

**Never a silent cut.** When the request is over, the oldest history goes
first, then oversized tool results and old messages are shortened to the
passages that match the request, and only then the request's own context
and, last, the request itself. Every shortening is said in the text the
model reads -- what was left out and how to get it -- because a model handed
half of something with no sign of it answers as if it had the whole. The
person's current request is never dropped: its opening, its closing and the
passages that match them are always kept.

**Never over the ceiling either.** When the system prompt and the tool
schemas alone leave no room for the conversation, the tools this turn does
not need (none of its messages called them) are shortened to a brief
description and then left out, largest first, and the model is told which.
If the request is still over after everything that is ours to shorten, it
is not sent: the turn fails with :class:`RequestTooLarge`, which says why in
words a person can act on, rather than reaching the vendor and coming back
as its refusal.

**The voice pipeline is measured, not fitted.** A call's request is built by
the pipeline, not here; :func:`measure_pipeline` logs one line when its
estimate is over the vendor's ceiling, so how often that happens is known
before anything is cut on a live call.

Behind ``context_v2``; off, requests go out exactly as built.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from api.services import features

FLAG = "context_v2"

#: What each vendor's models accept in one request, input and output
#: together, in tokens. Per vendor rather than per model: the smallest
#: current model of each is the figure, so a model swap cannot make it
#: optimistic.
PROVIDER_CEILINGS: dict[str, int] = {
    "anthropic": 200_000,
    "openai": 128_000,
    "google": 1_000_000,
    # Sarvam's chat models (the voice pipeline's Indic brain). The smallest
    # context among them, as for every vendor here.
    "sarvam": 32_000,
}

#: The vendors a builder turn can be moved to when its own is out of credit
#: (``client._fallback_model`` walks ``client.SUPPORTED_PROVIDERS``).
FALLBACK_VENDORS: tuple[str, ...] = ("anthropic", "openai", "google")

#: For a vendor not in the table: the small end of what current chat models
#: take, so an unknown vendor is never assumed to be generous.
DEFAULT_CEILING = 32_000

#: The share of the ceiling a request may fill. The estimate is already
#: conservative; this is room for what it cannot see (vendor framing of
#: tool schemas, message wrappers).
SAFETY = 0.9

#: Kept free for the reply. Matches the client's own cap on output, so a
#: request that fits here can always be answered in full.
OUTPUT_RESERVE = 4096

#: Characters a token for Latin text. Vendors land between three and five;
#: three is the pessimistic end, where the plan meter uses the middle.
LATIN_CHARS_PER_TOKEN = 3

#: Tokens a character for Indian scripts (Devanagari through Sinhala:
#: Hindi, Marathi, Bengali, Punjabi, Gujarati, Odia, Tamil, Telugu, Kannada,
#: Malayalam). Measured tokenizers run from about half a token a character
#: to over two on byte-level vocabularies; one and a half keeps the bound
#: above all but the worst, and SAFETY covers that one.
INDIC_TOKENS_PER_CHAR = 1.5
_INDIC_START, _INDIC_END = 0x0900, 0x0DFF

#: Any other script outside ASCII (CJK, Arabic, Cyrillic, symbols): a token
#: a character. Outside the Basic Multilingual Plane (emoji): two.
OTHER_TOKENS_PER_CHAR = 1
ASTRAL_TOKENS_PER_CHAR = 2

#: Framing every message carries on the wire (role, separators).
MESSAGE_OVERHEAD = 6

#: One picture, whatever its size: the vendors scale large images down to
#: roughly this many tokens.
IMAGE_TOKENS = 1600

#: How large one piece is when a text is cut into passages.
CHUNK_CHARS = 1200

#: Below this a shortened text is not worth keeping as anything but its
#: opening and closing.
MIN_KEEP_TOKENS = 400

#: History messages kept before any are shortened: the turn just before the
#: request is usually what "that" and "her" refer to.
KEEP_RECENT_HISTORY = 2

#: A tool the turn does not need keeps this much of its description before
#: it is left out altogether.
SHORT_TOOL_DESCRIPTION_CHARS = 200

#: Tools always kept when tools have to go: the way to look up what the
#: context could not hold.
ESSENTIAL_TOOLS = frozenset({"search_memory"})

#: Tool names listed in the note to the model before "and N more".
NAMED_DROPPED_TOOLS = 12

#: Tokens kept free for that note: twelve names of up to 64 characters
#: and the sentence around them, at the Latin rate.
TOOLS_NOTE_RESERVE = 400

#: Where Decibyl's request message puts the person's words after its
#: context (``decibyl._answer``). The context above it is shortened first.
QUESTION_MARKER = "\n\n## Question\n"


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


# --- the estimate ------------------------------------------------------------


def estimate(text: Any) -> int:
    """A conservative token count for one piece of text."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    ascii_chars = indic = other = astral = 0
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            ascii_chars += 1
        elif _INDIC_START <= code <= _INDIC_END:
            indic += 1
        elif code > 0xFFFF:
            astral += 1
        else:
            other += 1
    tokens = (
        math.ceil(ascii_chars / LATIN_CHARS_PER_TOKEN)
        + math.ceil(indic * INDIC_TOKENS_PER_CHAR)
        + other * OTHER_TOKENS_PER_CHAR
        + astral * ASTRAL_TOKENS_PER_CHAR
    )
    return max(1, tokens)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _images_of(content: Any) -> tuple[Any, int]:
    """``content`` without its pictures, and how many there were.

    The client's tool-result shape (``_images``) and a vendor's content
    blocks (``{"type": "image"}``) both count as pictures, never as the
    base64 text they are carried in -- a screenshot counted as text would
    look like half a million tokens and be cut to pieces.
    """
    if isinstance(content, dict) and isinstance(content.get("_images"), list):
        rest = {k: v for k, v in content.items() if k != "_images"}
        return rest, len(content["_images"])
    if isinstance(content, list):
        pictures = 0
        rest: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                "image",
                "image_url",
                "input_image",
            ):
                pictures += 1
            else:
                rest.append(block)
        return rest, pictures
    return content, 0


def content_tokens(content: Any) -> int:
    rest, pictures = _images_of(content)
    if isinstance(rest, str):
        text_tokens = estimate(rest)
    elif rest in (None, "", [], {}):
        text_tokens = 0
    else:
        text_tokens = estimate(_json(rest))
    return text_tokens + pictures * IMAGE_TOKENS


def message_tokens(message: dict[str, Any]) -> int:
    tokens = MESSAGE_OVERHEAD + content_tokens(message.get("content"))
    for call in message.get("tool_calls") or []:
        tokens += MESSAGE_OVERHEAD
        tokens += estimate(call.get("name"))
        tokens += estimate(_json(call.get("arguments")))
    return tokens


def tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    return sum(estimate(_json(tool)) + MESSAGE_OVERHEAD for tool in tools or [])


def ceiling_for(provider: str | None) -> int:
    return PROVIDER_CEILINGS.get(str(provider or "").lower(), DEFAULT_CEILING)


def request_ceiling(provider: str | None) -> int:
    """The ceiling one turn is held to.

    The smallest of the vendor asked for and every vendor the client can
    fall back to: a turn whose vendor is out of credit is asked again on
    another (``client._fallback_model``), and the request is not rebuilt
    for it.
    """
    return min(
        [ceiling_for(provider), *(PROVIDER_CEILINGS[v] for v in FALLBACK_VENDORS)]
    )


def input_budget(ceiling: int) -> int:
    """Tokens the input may take: the safe share, less the reply's room."""
    return int(ceiling * SAFETY) - OUTPUT_RESERVE


# --- shortening ----------------------------------------------------------------

_WORD = re.compile(r"[\w\u0900-\u0DFF]+", re.UNICODE)

#: Words that match everything and so rank nothing.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "up",
        "us",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "please",
        "tell",
        "give",
        "show",
    ]
)


def terms(text: str) -> set[str]:
    return {
        word
        for word in (w.lower() for w in _WORD.findall(text or ""))
        if len(word) > 1 and word not in _STOPWORDS
    }


def _chunks(text: str) -> list[str]:
    """Paragraphs, and any paragraph too long cut at lines, then hard."""
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if len(paragraph) <= CHUNK_CHARS:
            if paragraph.strip():
                pieces.append(paragraph)
            continue
        current = ""
        for line in paragraph.split("\n"):
            while len(line) > CHUNK_CHARS:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(line[:CHUNK_CHARS])
                line = line[CHUNK_CHARS:]
            if current and len(current) + len(line) + 1 > CHUNK_CHARS:
                pieces.append(current)
                current = ""
            current = f"{current}\n{line}" if current else line
        if current.strip():
            pieces.append(current)
    return pieces


def condense(text: str, query: str, target_tokens: int, *, note: str = "") -> str:
    """``text`` cut to about ``target_tokens``, keeping what the query needs.

    The opening and the closing are always kept (instructions sit at one end
    or the other of most pasted things), then the passages sharing the most
    of the query's rarer words, in their original order, with a marker where
    anything was left out. Returned unchanged when it already fits.
    """
    original = estimate(text)
    if original <= target_tokens:
        return text
    target = max(target_tokens, 1)
    pieces = _chunks(text)
    header = (
        f"[Shortened to fit the model's limit: about {original} tokens, of "
        "which the opening, the closing and the passages that match the "
        f"request are kept below.{(' ' + note) if note else ''}]"
    )
    budget = target - estimate(header) - 8
    if not pieces or budget <= 0:
        return header
    wanted = terms(query)
    # Rarer words count for more: a word in every passage ranks nothing.
    seen_in: dict[str, int] = {}
    piece_terms = [terms(piece) for piece in pieces]
    for found in piece_terms:
        for word in found & wanted:
            seen_in[word] = seen_in.get(word, 0) + 1
    scores = [
        sum(1.0 + math.log(len(pieces) / seen_in[w]) for w in found & wanted)
        for found in piece_terms
    ]
    # The two ends, then the matching passages best first, then -- while
    # there is room -- the rest in reading order.
    ends = [0, len(pieces) - 1]
    middle = range(1, len(pieces) - 1)
    order = (
        ends
        + sorted((i for i in middle if scores[i] > 0), key=lambda i: (-scores[i], i))
        + [i for i in middle if scores[i] <= 0]
    )
    kept: set[int] = set()
    used = 0
    for index in order:
        if index in kept:
            continue
        cost = estimate(pieces[index]) + 12
        if used + cost > budget:
            if index in ends:
                # Too long even for the ends: keep as much of it as fits.
                piece = pieces[index]
                room = max(0, budget - used - 12)
                chars = int(len(piece) * room / max(1, estimate(piece)))
                if chars > 0:
                    pieces[index] = piece[:chars] if index == 0 else piece[-chars:]
                    kept.add(index)
                    used += estimate(pieces[index]) + 12
            continue
        kept.add(index)
        used += cost
    out: list[str] = [header]
    previous = -1
    for index in sorted(kept):
        if index != previous + 1:
            gap = sum(len(pieces[i]) for i in range(previous + 1, index))
            out.append(f"[... {gap} characters left out here ...]")
        out.append(pieces[index])
        previous = index
    if previous != len(pieces) - 1:
        gap = sum(len(pieces[i]) for i in range(previous + 1, len(pieces)))
        out.append(f"[... {gap} characters left out here ...]")
    return "\n\n".join(out)


# --- the request ---------------------------------------------------------------


class RequestTooLarge(RuntimeError):
    """A request that would be over the model's ceiling even after
    everything ours to shorten was shortened. Never sent.

    ``client`` raises it as a ``BuilderClientError`` (``str(exc)`` is what
    the person reads), so it reaches the thread as a failed turn that says
    why, not as the vendor's refusal of an oversized request."""


@dataclass
class Prepared:
    """A request as it goes out, and what was done to make it fit."""

    conversation: Any
    #: The estimate of the input as sent; None when the check did not run.
    estimated: int | None = None
    ceiling: int | None = None
    actions: list[str] = field(default_factory=list)
    #: The system prompt and tools to send, when fitting changed them; None
    #: means send what was given.
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    #: Why the request must not be sent, in words for the person; None when
    #: it fits.
    refused: str | None = None

    def system_or(self, given: str) -> str:
        return given if self.system is None else self.system

    def tools_or(self, given: list[dict[str, Any]] | None) -> Any:
        return given if self.tools is None else self.tools


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return str(getattr(tool, "name", "") or "")
    inner = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    return str(tool.get("name") or inner.get("name") or "")


def _needed_tools(messages: list[dict[str, Any]]) -> set[str]:
    """Tools this turn has already used: a call or a result in the
    conversation names a tool the vendor must still be given."""
    names = set(ESSENTIAL_TOOLS)
    for message in messages:
        for call in message.get("tool_calls") or []:
            names.add(str(call.get("name") or ""))
        if message.get("role") == "tool" and message.get("name"):
            names.add(str(message["name"]))
    return names


def _short_tool(tool: dict[str, Any]) -> dict[str, Any]:
    description = str(tool.get("description") or "")
    if len(description) <= SHORT_TOOL_DESCRIPTION_CHARS:
        return tool
    cut = description[:SHORT_TOOL_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
    return {**tool, "description": cut + " ..."}


def fit_tools(
    tools: list[dict[str, Any]], needed: set[str], over: int
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """``tools`` with about ``over`` tokens taken out, never from a needed
    one: optional tools' descriptions shortened first, then optional tools
    left out, largest first. Returns the tools, how many were shortened,
    and the names left out. Order is kept, so the prefix a cache keys on
    changes as little as it can."""
    kept = list(tools)
    costs = [estimate(_json(t)) + MESSAGE_OVERHEAD for t in kept]
    optional = sorted(
        (i for i, t in enumerate(kept) if _tool_name(t) not in needed),
        key=lambda i: -costs[i],
    )
    saved = shortened = 0
    for index in optional:
        if saved >= over:
            break
        short = _short_tool(kept[index])
        if short is kept[index]:
            continue
        cost = estimate(_json(short)) + MESSAGE_OVERHEAD
        saved += costs[index] - cost
        kept[index], costs[index] = short, cost
        shortened += 1
    dropped: set[int] = set()
    for index in sorted(optional, key=lambda i: -costs[i]):
        if saved >= over:
            break
        dropped.add(index)
        saved += costs[index]
    names = [_tool_name(kept[i]) for i in sorted(dropped)]
    return [t for i, t in enumerate(kept) if i not in dropped], shortened, names


def _tools_note(names: list[str]) -> str:
    shown = ", ".join(name[:64] for name in names[:NAMED_DROPPED_TOOLS])
    more = len(names) - NAMED_DROPPED_TOOLS
    return (
        f"\n\n[To fit the model's limit, {len(names)} tool"
        f"{'s were' if len(names) != 1 else ' was'} left out of this turn: "
        f"{shown}{f' and {more} more' if more > 0 else ''}. If the request "
        "needs one of them, say it was not available just now; do not "
        "pretend to have used it.]"
    )


def _refusal(fixed: int, budget: int) -> str:
    if fixed > budget:
        return (
            "I could not send this to the model: my own instructions and "
            "tools for this workspace are larger than it accepts in one "
            "request, even with every tool this turn could do without left "
            "out. Nothing was sent. This is on our side, not yours -- please "
            "let support know."
        )
    return (
        "I could not send this to the model: the request is larger than it "
        "accepts at once, even after shortening the conversation and the "
        "message to the parts that match what you asked. Nothing was sent. "
        "Try a shorter message or a new conversation; a long document is "
        "better added to Files, where it can be searched."
    )


def _opens(message: dict[str, Any]) -> bool:
    """Something a person said, which a request can start with."""
    return message.get("role") == "user" and isinstance(message.get("content"), str)


def _request_index(messages: list[dict[str, Any]]) -> int:
    """The person's current request: the last user message with words that
    does not follow a tool result.

    A user message straight after tool results is the loop talking to the
    model mid-turn (Decibyl's last-step notice), not the person: taking it
    for the request would make the real request and the reads it started
    look like history, and history is what goes first.
    """
    fallback = len(messages) - 1
    for index in range(len(messages) - 1, -1, -1):
        if not _opens(messages[index]):
            continue
        if index == 0 or messages[index - 1].get("role") != "tool":
            return index
        fallback = min(fallback, index)
    return fallback


def _question_of(request: str) -> str:
    if QUESTION_MARKER in request:
        return request.rsplit(QUESTION_MARKER, 1)[1]
    return request


def _query_for(request: str) -> str:
    """What a shortened passage is matched against: the question, or for a
    question too long to be one, its opening and its closing."""
    question = _question_of(request)
    if len(question) <= 2 * CHUNK_CHARS:
        return question
    return f"{question[:CHUNK_CHARS]}\n{question[-CHUNK_CHARS:]}"


def prepare(
    *,
    provider: str | None,
    system: str,
    conversation: Any,
    tools: list[dict[str, Any]] | None,
    organization_id: int | None = None,
    ceiling: int | None = None,
) -> Prepared:
    """The request held under the ceiling, or as it was when it fits.

    Never mutates ``conversation``: the caller's transcript is the record of
    the turn, and a later round may fit where this one did not.
    """
    if organization_id is None:
        from api.services.billing import model_usage

        organization_id = model_usage.current()[0]
    if not enabled(organization_id):
        return Prepared(conversation)

    limit = ceiling or request_ceiling(provider)
    budget = input_budget(limit)
    fixed = estimate(system) + tools_tokens(tools)
    messages = [dict(m) for m in getattr(conversation, "messages", [])]
    costs = [message_tokens(m) for m in messages]
    total = fixed + sum(costs)
    if total <= budget:
        return Prepared(conversation, estimated=total, ceiling=limit)
    initial = total

    actions: list[str] = []
    sent_tools = list(tools or [])
    dropped_tools: list[str] = []
    needed = _needed_tools(messages)

    def trim_tools(over: int) -> None:
        nonlocal sent_tools, fixed, total
        if over <= 0 or not sent_tools:
            return
        # Room for the note that names what was left out, which is added
        # to the system prompt afterwards.
        fitted, shortened, names = fit_tools(
            sent_tools, needed, over + TOOLS_NOTE_RESERVE
        )
        if not shortened and not names:
            return
        saved = tools_tokens(sent_tools) - tools_tokens(fitted)
        sent_tools = fitted
        fixed -= saved
        total -= saved
        dropped_tools.extend(names)
        if shortened:
            actions.append(f"shortened {shortened} tool descriptions")
        if names:
            actions.append(f"left out {len(names)} tools")

    # 0. A system prompt and tools that leave no room for the conversation:
    # the tools this turn does not need give way first.
    trim_tools(fixed + min(sum(costs), 2 * MIN_KEEP_TOKENS) - budget)

    request_at = _request_index(messages)
    request_text = messages[request_at].get("content") if messages else ""
    query = _query_for(request_text if isinstance(request_text, str) else "")
    dropped = 0

    def pop_first() -> None:
        nonlocal total, request_at, dropped
        total -= costs.pop(0)
        messages.pop(0)
        request_at -= 1
        dropped += 1

    def drop_oldest(keep: int) -> None:
        while total > budget and request_at > keep:
            pop_first()
            # A tool result or an assistant turn cannot open a request, and
            # a tool result without the call it answers is refused: keep
            # dropping to the next thing a person said.
            while request_at > 0 and not _opens(messages[0]):
                pop_first()

    def shorten(index: int, note: str) -> None:
        nonlocal total
        message = messages[index]
        over = total - budget
        target = max(MIN_KEEP_TOKENS, costs[index] - over - MESSAGE_OVERHEAD)
        if target >= costs[index]:
            return
        content = message.get("content")
        rest, pictures = _images_of(content)
        text = rest if isinstance(rest, str) else _json(rest)
        short = condense(text, query, target - pictures * IMAGE_TOKENS, note=note)
        if isinstance(content, dict) and pictures:
            message["content"] = {"_images": content["_images"], "text": short}
        else:
            message["content"] = short
        new_cost = message_tokens(message)
        total -= costs[index] - new_cost
        costs[index] = new_cost

    # 1. The oldest history, down to the turn before the request.
    drop_oldest(KEEP_RECENT_HISTORY)
    if dropped:
        actions.append(f"dropped {dropped} oldest messages")

    # 2. Tool results, then older messages, biggest first.
    if total > budget:
        tool_results = sorted(
            (i for i, m in enumerate(messages) if m.get("role") == "tool"),
            key=lambda i: -costs[i],
        )
        for index in tool_results:
            if total <= budget:
                break
            shorten(
                index,
                "Ask for a narrower read (a search, a filter, one record) "
                "for the rest.",
            )
            actions.append(f"shortened a tool result ({messages[index].get('name')})")
        history = sorted(
            (
                i
                for i in range(request_at)
                if messages[i].get("role") in ("user", "assistant")
                and isinstance(messages[i].get("content"), str)
            ),
            key=lambda i: -costs[i],
        )
        for index in history:
            if total <= budget:
                break
            shorten(index, "An earlier message in this conversation.")
            actions.append("shortened an earlier message")

    # 3. The rest of the history.
    if total > budget:
        before = dropped
        drop_oldest(0)
        if dropped > before:
            actions.append(f"dropped {dropped - before} more earlier messages")

    # 4. The request: its context first, then the person's own words.
    if total > budget and isinstance(messages[request_at].get("content"), str):
        request = messages[request_at]["content"]
        if QUESTION_MARKER in request:
            context, question = request.rsplit(QUESTION_MARKER, 1)
            over = total - budget
            target = max(MIN_KEEP_TOKENS, estimate(context) - over)
            short = condense(context, query, target)
            request = f"{short}{QUESTION_MARKER}{question}"
            messages[request_at]["content"] = request
            new_cost = message_tokens(messages[request_at])
            total -= costs[request_at] - new_cost
            costs[request_at] = new_cost
            actions.append("shortened the context in front of the request")
    if total > budget:
        shorten(
            request_at,
            "The person's message was too long to read whole. Tell them so, "
            "say which part you answered from, and suggest adding the "
            "document to Files, where it can be searched, for the rest.",
        )
        actions.append("shortened the request itself")

    if dropped and messages:
        first = messages[0]
        if isinstance(first.get("content"), str):
            first["content"] = (
                f"[{dropped} earlier message{'s' if dropped != 1 else ''} of this "
                "conversation were left out to fit the model's limit.]\n\n"
                + first["content"]
            )
            total += estimate(str(dropped)) + 20

    # 5. Still over: the optional tools that are left, before giving up.
    trim_tools(total - budget)

    sent_system = system
    if dropped_tools:
        note = _tools_note(dropped_tools)
        sent_system = system + note
        fixed += estimate(note)
        total += estimate(note)

    refused = None
    if total > budget:
        # Nothing left that is ours to shorten. Not sent: the vendor's
        # refusal would reach the person as an unexplained failure.
        refused = _refusal(fixed, budget)
        logger.error(
            "Request still over its ceiling after fitting, not sent: about {} "
            "of {} input tokens (system and tools {})",
            total,
            budget,
            fixed,
        )
    logger.warning(
        "Fitted a request to the model's limit ({} -> about {} input tokens, "
        "ceiling {}): {}",
        initial,
        total,
        limit,
        "; ".join(actions) or "nothing to do",
    )
    fitted = type(conversation)(messages=messages)
    return Prepared(
        fitted,
        estimated=total,
        ceiling=limit,
        actions=actions,
        system=sent_system if sent_system != system else None,
        tools=sent_tools if sent_tools != list(tools or []) else None,
        refused=refused,
    )


def vendor_of(service: Any) -> str:
    """The vendor behind a pipeline LLM service, from its class name; ""
    when it is none in the ceiling table (which then reads the default)."""
    name = type(service).__name__.lower()
    if "gemini" in name:
        return "google"
    for vendor in PROVIDER_CEILINGS:
        if vendor in name:
            return vendor
    return ""


def measure_pipeline(
    vendor: str, system: str, tools: list[Any] | None, messages: list[Any]
) -> int:
    """The voice pipeline's request, estimated as a builder request is, with
    one log line when it is over the vendor's ceiling. Measures only:
    nothing on a live call is cut here. Returns the estimate."""
    limit = ceiling_for(vendor)
    budget = input_budget(limit)
    schemas = [
        tool.to_default_dict() if hasattr(tool, "to_default_dict") else tool
        for tool in tools or []
    ]
    total = (
        estimate(system)
        + tools_tokens(schemas)
        + sum(
            message_tokens(m) if isinstance(m, dict) else content_tokens(_json(m))
            for m in messages
        )
    )
    if total > budget:
        logger.warning(
            "pipeline_request_over_ceiling vendor={} estimated={} budget={} ceiling={}",
            vendor or "unknown",
            total,
            budget,
            limit,
        )
    return total


def reported_input(usage: dict[str, Any] | None, provider: str | None) -> int | None:
    """What the vendor says the input was. Anthropic reports input net of
    its cache, so the cache counts are added back; the others include them."""
    if not usage:
        return None
    prompt = int(usage.get("prompt_tokens") or 0)
    if str(provider or "").lower() == "anthropic":
        prompt += int(usage.get("cache_read_input_tokens") or 0)
        prompt += int(usage.get("cache_creation_input_tokens") or 0)
    return prompt or None


def reconcile(prepared: Prepared, reply: Any, provider: str | None) -> float | None:
    """The estimate against the vendor's own count, logged. Returns the
    ratio (estimate / reported), or None when either is missing.

    Above one is the bound doing its job. Below one means the estimate
    undercounted and the ceiling was closer than it looked -- logged as a
    warning, because that is the case that becomes a refused request.
    """
    if prepared.estimated is None or reply is None:
        return None
    reported = reported_input(getattr(reply, "usage", None), provider)
    if not reported:
        return None
    ratio = prepared.estimated / reported
    if ratio < 1:
        logger.warning(
            "Request estimate under the vendor's count: estimated {}, {} "
            "reported {} (ratio {:.2f})",
            prepared.estimated,
            provider,
            reported,
            ratio,
        )
    else:
        logger.info(
            "Request estimate: estimated {}, {} reported {} (ratio {:.2f})",
            prepared.estimated,
            provider,
            reported,
            ratio,
        )
    return ratio

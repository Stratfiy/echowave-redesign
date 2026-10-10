"""Which confirmed facts reach a prompt: the ones the question needs.

Confirmed memory used to reach a prompt by popularity alone. The rows came
back most-seen first (``organisation_memory``), the read stopped at two
hundred, and the prompt kept the first forty -- with no idea what was being
asked. A business with a hundred confirmed facts that asked about the one
it had confirmed once ("the GST number for the Pune branch") got forty facts
about everything else.

This picks for the question, in three layers, and says what it left out:

1. **What must always be there.** A bot's own instructions (its scope is the
   operator telling *this* bot something) and the business's identity and
   standing rules -- its name, its language, what it always or never does.
2. **What the question is about.** Words the question shares with a fact's
   key or value, rarer words counting for more, a fact's key counting for
   more than its value, and names and numbers (entities) for more again.
   A question about a period ("this week", "last month") prefers facts
   confirmed in it. Matching is by words, not meaning: confirmed facts have
   no embeddings, and computing them per turn would need every account to
   hold an embeddings key, which a fact lookup should not.
3. **Nothing else.** An unrelated fact is not padding. Only a question with
   nothing to match on ("hi", "what can you do") gets the most-seen few, as
   general context. A question that has words to match on and matches
   nothing ("the GST number for Pune?" against a hundred service facts) gets
   the first layer alone and a line saying none of the rest bears on it:
   ten popular unrelated facts there read as an answer to lean on, and the
   model guesses from them instead of saying it does not know.

Scopes are applied before anything here sees a row: the database read is
what keeps another member's personal memory out (``organisation_memory``
never returns it), so no ranking can surface it. On a shared key the
narrower scope wins -- a member's own over a bot's over the organisation's
-- and within one scope the later confirmation wins, so a correction
supersedes the value it corrects.

Every fact shown carries where it came from (its id, when it was
confirmed, the call that taught it), and what was not shown is counted in
the block with the way to find it: ``search_memory`` on Decibyl's thread.

Behind ``context_v2``; off, the most-seen forty as before.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import features, prompt_budget
from api.services.workflow.organisation_learning import KIND_FACT, STATUS_CONFIRMED

FLAG = "context_v2"

#: Facts in one prompt, as before: the cap is about tokens on every turn.
MAX_SELECTED = 40

#: Of those, at most this many are the always-there layer, so a business
#: with sixty "rules" still leaves room for what was asked.
MAX_REQUIRED = 15

#: A question with nothing to match on gets this many of the most-seen.
GENERIC_FILL = 10

#: Rows read for one selection: the most-seen, and separately the ones that
#: contain the question's words, so a rare fact is a candidate however far
#: down the popularity order it sits.
CANDIDATE_LIMIT = 200

#: The question's words used to find candidates in the database.
MAX_QUERY_WORDS = 12

#: Question words scored, at most. A bot's steps can run to hundreds.
MAX_SCORED_TERMS = 80

#: Facts ``search_memory`` returns at once.
SEARCH_LIMIT = 12

TOOL_NAME = "search_memory"

#: Keys that state who the business is or how it works. Matched as words in
#: the key, so "business name", "language_policy" and "never_discuss" count.
_REQUIRED_WORDS = frozenset(
    {
        "name",
        "business",
        "company",
        "brand",
        "identity",
        "language",
        "languages",
        "tone",
        "rule",
        "rules",
        "policy",
        "always",
        "never",
        "must",
    }
)

_WORD = re.compile(r"[\w\u0900-\u0DFF]+", re.UNICODE)

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
        "could",
        "do",
        "does",
        "did",
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
        "would",
        "you",
        "your",
        "please",
        "tell",
        "give",
        "show",
        "know",
        "about",
        "any",
        "all",
        "also",
        "get",
        "got",
        "just",
        "let",
        "like",
        "make",
        "need",
        "okay",
        "ok",
        "want",
        "hi",
        "hello",
        "hey",
        "thanks",
        # Verbs about memory itself, which every fact would answer to.
        "confirm",
        "confirmed",
        "decide",
        "decided",
        "agree",
        "agreed",
        "said",
        "told",
        "remember",
        "remembered",
        "note",
        "noted",
        "know",
        "knew",
    ]
)

#: Words that name a period rather than a subject.
_TIME_WORDS = frozenset(
    [
        "today",
        "yesterday",
        "week",
        "month",
        "recent",
        "recently",
        "lately",
        "latest",
        "last",
        "past",
        "this",
        "new",
        "newest",
    ]
)


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


# --- words ----------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def query_terms(query: str) -> list[str]:
    """The question's content words, in order, once each."""
    seen: dict[str, None] = {}
    for word in _words(query):
        if len(word) > 1 and word not in _STOPWORDS and word not in _TIME_WORDS:
            seen.setdefault(word, None)
    return list(seen)


def _stem(word: str) -> str:
    """A crude stem: "deliveries" and "delivery" meet at "deliver"."""
    return word[: max(4, len(word) - 3)] if len(word) > 5 else word


def _matches(term: str, word: str) -> bool:
    return word.startswith(_stem(term)) or term.startswith(_stem(word))


def _entities(query: str) -> set[str]:
    """Names and numbers: capitalised words past the first, and anything
    with a digit in it. What a question is most precisely about."""
    raw = _WORD.findall(query or "")
    out = {w.lower() for w in raw if any(ch.isdigit() for ch in w)}
    out |= {w.lower() for w in raw[1:] if w[:1].isupper()}
    return out


def key_words(key: str) -> list[str]:
    return [w for w in re.split(r"[^\w\u0900-\u0DFF]+|_+", (key or "").lower()) if w]


def normalised_key(key: str) -> str:
    return " ".join(key_words(key))


# --- scope ----------------------------------------------------------------------


def _scope_rank(row: Any) -> int:
    """A member's own beats a bot's beats the organisation's."""
    if getattr(row, "user_id", None) is not None:
        return 2
    if getattr(row, "workflow_id", None) is not None:
        return 1
    return 0


def _when(row: Any) -> datetime:
    at = getattr(row, "confirmed_at", None) or getattr(row, "last_seen_at", None)
    if at is None:
        return datetime.min.replace(tzinfo=UTC)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def resolve(rows: Iterable[Any]) -> list[Any]:
    """One row per fact: the narrower scope, then the later confirmation.

    Keys are compared as words, so "Opening hours" confirmed last week and
    "opening_hours" confirmed today are one fact with today's value.
    """
    best: dict[str, Any] = {}
    order: list[str] = []
    for row in rows:
        if getattr(row, "kind", KIND_FACT) != KIND_FACT:
            continue
        if not str(getattr(row, "value", "") or "").strip():
            continue
        name = normalised_key(str(row.key))
        held = best.get(name)
        if held is None:
            order.append(name)
            best[name] = row
            continue
        if (_scope_rank(row), _when(row)) > (_scope_rank(held), _when(held)):
            best[name] = row
    return [best[name] for name in order]


def is_required(row: Any) -> bool:
    if getattr(row, "workflow_id", None) is not None:
        return True
    return any(word in _REQUIRED_WORDS for word in key_words(str(row.key)))


# --- time -------------------------------------------------------------------------


def since_for(query: str, now: datetime | None = None) -> datetime | None:
    """The start of the period a question names, or None."""
    text = (query or "").lower()
    now = now or datetime.now(UTC)
    if "yesterday" in text:
        return now - timedelta(days=2)
    if "today" in text:
        return now - timedelta(days=1)
    if re.search(r"\b(last|past|previous) week\b", text):
        return now - timedelta(days=14)
    if re.search(r"\bthis week\b", text):
        return now - timedelta(days=7)
    if re.search(r"\b(last|past|previous) month\b", text):
        return now - timedelta(days=62)
    if re.search(r"\b(this month|recent|recently|lately|latest|newest)\b", text):
        return now - timedelta(days=31)
    return None


# --- selection --------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    rows: list[Any]
    #: Facts after resolving, of which ``rows`` were chosen.
    total: int
    #: Rows chosen because the question matched them.
    matched: int = 0
    #: Facts the question matched at all, shown or not.
    relevant: int = 0
    #: The question had words to match on and no fact matched them: what
    #: memory holds does not answer it, and the block says so.
    unmatched: bool = False

    @property
    def left_out(self) -> int:
        return max(0, self.total - len(self.rows))


def scores(rows: list[Any], query: str, now: datetime | None = None) -> list[float]:
    """How much each fact has to do with the question; 0 for nothing.

    Each question word is matched against the facts' vocabulary once, not
    against every fact: on a call the "question" is the bot's own steps,
    which can be a few hundred words, and this runs before the agent's
    first line.
    """
    wanted = query_terms(query)[:MAX_SCORED_TERMS]
    entities = _entities(query)
    since = since_for(query, now)
    if not wanted and since is None:
        return [0.0] * len(rows)
    keys = [set(key_words(str(row.key))) for row in rows]
    values = [set(_words(str(row.value))) for row in rows]
    vocabulary = set().union(*keys, *values) if rows else set()
    hits = {
        term: {word for word in vocabulary if _matches(term, word)} for term in wanted
    }
    weights: dict[str, float] = {}
    for term, words in hits.items():
        df = sum(1 for k, v in zip(keys, values) if words & (k | v))
        if df:
            weight = 1.0 + math.log(len(rows) / df)
            weights[term] = weight * 1.5 if term in entities else weight
    out: list[float] = []
    for index, row in enumerate(rows):
        score = 0.0
        for term, weight in weights.items():
            if hits[term] & keys[index]:
                score += 2 * weight
            elif hits[term] & values[index]:
                score += weight
        if since is not None and _when(row) >= since:
            # A period narrows what matched; when no word matched anything
            # it is the whole question: "what did we confirm this week?".
            score = score * 1.5 if score else (0.0 if weights else 1.0)
        out.append(score)
    return out


def select(
    rows: Iterable[Any],
    query: str,
    *,
    limit: int = MAX_SELECTED,
    generic_fill: int = GENERIC_FILL,
    required: bool = True,
    now: datetime | None = None,
) -> Selection:
    """The facts for this question: required, then relevant, nothing else.

    ``required=False`` and ``generic_fill=0`` is a search: only what the
    words matched."""
    facts = resolve(rows)
    ranked = scores(facts, query, now)
    # "hi" has nothing to match on; "GST number for Pune?" has, whether or
    # not anything matches it. Only the first gets general context.
    nothing_to_match = not query_terms(query) and since_for(query, now) is None
    chosen: list[Any] = []
    taken: set[int] = set()

    for index, row in enumerate(facts):
        if not required or len(chosen) >= min(limit, MAX_REQUIRED):
            break
        if is_required(row):
            chosen.append(row)
            taken.add(index)

    relevant = sorted(
        (i for i in range(len(facts)) if ranked[i] > 0 and i not in taken),
        key=lambda i: (-ranked[i], -(getattr(facts[i], "times_seen", 0) or 0), i),
    )
    matched = 0
    for index in relevant:
        if len(chosen) >= limit:
            break
        chosen.append(facts[index])
        taken.add(index)
        matched += 1

    if nothing_to_match:
        # Nothing to match on: the most-seen few as general context, in the
        # database's order (most-seen first). A question that matched
        # nothing gets none: unrelated facts are not padding.
        cap = min(limit, len(chosen) + generic_fill)
        for index, row in enumerate(facts):
            if len(chosen) >= cap:
                break
            if index not in taken:
                chosen.append(row)
                taken.add(index)
    return Selection(
        rows=chosen,
        total=len(facts),
        matched=matched,
        relevant=len(relevant),
        unmatched=bool(facts) and not nothing_to_match and not relevant,
    )


# --- reading ----------------------------------------------------------------------


async def candidates(
    organization_id: int,
    query: str,
    *,
    workflow_id: int | None = None,
    user_id: int | None = None,
) -> list[Any]:
    """Confirmed facts this reader may see: the most-seen, plus every one
    containing one of the question's words. Scope is the database's."""
    common = {
        "organization_id": organization_id,
        "kind": KIND_FACT,
        "status": STATUS_CONFIRMED,
        "workflow_id": workflow_id,
        "user_id": user_id,
        "limit": CANDIDATE_LIMIT,
    }
    rows = list(await db_client.organisation_memory(**common))
    words = sorted(query_terms(query), key=len, reverse=True)[:MAX_QUERY_WORDS]
    if words:
        seen = {row.id for row in rows}
        found = await db_client.organisation_memory(
            **common, matching=sorted({_stem(w) for w in words})
        )
        rows.extend(row for row in found if row.id not in seen)
    return rows


def _reference(row: Any) -> str:
    parts = [f"fact {row.id}"]
    at = getattr(row, "confirmed_at", None)
    if at is not None:
        parts.append(f"confirmed {at.strftime('%d %b %Y')}")
    if getattr(row, "source_run_id", None):
        parts.append(f"from run {row.source_run_id}")
    if getattr(row, "user_id", None) is not None:
        parts.append("the asker's own")
    elif getattr(row, "workflow_id", None) is not None:
        parts.append("one agent's own")
    return ", ".join(parts)


def line(row: Any) -> str:
    value = prompt_budget.clip(str(row.value), prompt_budget.MAX_FACT_CHARS)
    return f"- {row.key}: {value} ({_reference(row)})"


def block(selection: Selection, *, can_search: bool = True) -> str:
    """The selection as the prompt reads it, with what was left out said."""
    if not selection.rows:
        if selection.total:
            return (
                f"None of the {selection.total} confirmed facts bears on this "
                f"question.{(' ' + TOOL_NAME + ' looks them up by words.') if can_search else ''}"
            )
        return "Nothing confirmed yet."
    lines = [line(row) for row in selection.rows]
    if selection.unmatched and selection.left_out:
        more = selection.left_out
        lines.append(
            f"- none of the other {more} confirmed fact{'s' if more != 1 else ''} "
            "bears on this question"
            + (f"; {TOOL_NAME} looks them up by words" if can_search else "")
        )
    elif selection.left_out:
        more = selection.left_out
        lines.append(
            f"- and {more} more confirmed fact{'s' if more != 1 else ''} not "
            "shown here, chosen against this question"
            + (f"; {TOOL_NAME} looks them up by words" if can_search else "")
        )
    return "\n".join(lines)


async def for_question(
    organization_id: int, question: str, *, user_id: int | None = None
) -> tuple[list[Any], str]:
    """Decibyl's memory block for one question, and the rows in it."""
    try:
        rows = await candidates(organization_id, question, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Decibyl could not read memory: {}", exc)
        return [], "Memory could not be read just now."
    chosen = select(rows, question)
    return chosen.rows, block(chosen)


async def remembered_for_bot(
    organization_id: int, workflow_id: int | None, query: str
) -> tuple[dict[str, str], int]:
    """A bot's facts for ``query``, as ``recall_for_bot`` returns them, and
    how many were left out. Never another member's personal memory: the
    read names no member."""
    rows = await candidates(organization_id, query, workflow_id=workflow_id)
    chosen = select(rows, query)
    return {row.key: row.value for row in chosen.rows}, chosen.left_out


# --- the tool ---------------------------------------------------------------------

RULES = (
    "- Memory search: the confirmed facts in the context are the ones chosen "
    f"for this question. When one you need is not there, call {TOOL_NAME} "
    "with the words it would use (a name, a number, a subject); it returns "
    "confirmed facts with where each came from. State them as fact.\n"
    "- Not on record: when no confirmed fact, context line or tool result "
    "supports an answer -- a number, a name, a date, a price, a policy -- do "
    f"not guess one. Call {TOOL_NAME} if it might be on record; if it is not, "
    "say you don't know it yet, or ask the person. A likely-sounding guess "
    "stated as fact is worse than saying you don't have it.\n"
)


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Look up the business's confirmed facts by words: the ones the "
            "context did not show. Runs now. Returns each fact with its id, "
            "when it was confirmed and the run it came from."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words the fact would contain.",
                },
            },
            "required": ["query"],
        },
    }


def _found(row: Any) -> dict[str, Any]:
    """One fact as the search returns it, with the value a correction
    replaced when there was one ("was 5 km until 8 Oct")."""
    fact = {
        "id": row.id,
        "key": row.key,
        "value": row.value,
        "source": _reference(row),
    }
    # Rows read before the history columns existed, and test rows, have none.
    previous = getattr(row, "previous_value", None)
    if previous:
        at = getattr(row, "superseded_at", None)
        fact["previously"] = previous + (
            f" (until {at.strftime('%d %b %Y')})" if at is not None else ""
        )
    return fact


async def for_thread(
    organization_id: int, arguments: dict[str, Any], *, user_id: int | None
) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering."""
    query = str(arguments.get("query") or "").strip()[:300]
    if not query:
        return {"status": "error", "error": "Say what to look for."}
    try:
        rows = await candidates(organization_id, query, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory search failed for org {}: {}", organization_id, exc)
        return {"status": "error", "error": "Memory could not be read just now."}
    chosen = select(rows, query, limit=SEARCH_LIMIT, generic_fill=0, required=False)
    facts = [_found(row) for row in chosen.rows]
    if not facts:
        return {
            "status": "success",
            "facts": [],
            "note": "No confirmed fact matched those words.",
        }
    return {
        "status": "success",
        "facts": facts,
        "more": max(0, chosen.relevant - chosen.matched),
        "note": "Confirmed by the business: state these as fact.",
    }
